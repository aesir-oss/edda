"""The document importer: Office files, PDFs and scans, each down its own lane, kept with the Markdown made from them.

- Office files (.docx, .pptx, .xlsx) go through MarkItDown, which reads their own XML: method ``native``.
- PDFs go through Docling, which works out reading order and tables, with OCR (RapidOCR) only where a page has no
  text layer: method ``layout``, or ``ocr`` when any page needed it. Images are OCRed: method ``ocr``.

The original is stored unedited; the Markdown is a second raw source naming it in ``derived_from``, with the converter
and its version, so a newer converter adds text instead of changing what older citations point to. Paged Markdown
marks each page (``<!-- page 5 -->``), so a citation can name a page and the kernel can check it exists.
"""

import io
import re
import zipfile
from collections.abc import Callable
from functools import cache
from importlib.metadata import packages_distributions, version
from pathlib import Path, PurePath
from typing import Any, Literal

import structlog
from pydantic import BaseModel

from edda.drivers.errors import DriverError
from edda.drivers.markdown import markdown_title
from edda.kernel import Kernel
from edda.llm.localkey import resolve
from edda.models import DocumentImport

log = structlog.get_logger("edda.drivers.document")

MODELS_DIR = "~/.cache/edda/docling"
OFFICE = {".docx", ".pptx", ".xlsx"}
PAGED = {".pdf"}
IMAGES = {".png", ".jpg", ".jpeg", ".tif", ".tiff"}
SUPPORTED = sorted(OFFICE | PAGED | IMAGES)
# The container of password-protected Office files and of the old .doc, .ppt and .xls formats.
_OLE = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"

Method = Literal["native", "layout", "ocr"]

# MarkItDown marks slides; as page markers they can be cited and checked like a PDF's pages.
_SLIDE = re.compile(r"^<!-- Slide number: (\d+) -->$", re.MULTILINE)


class Converted(BaseModel):
    """Markdown made from a document, and how it was made."""

    text: str
    converter: str
    method: Method


Convert = Callable[[bytes, str], Converted]


def import_document(
    kernel: Kernel,
    data: bytes,
    *,
    filename: str,
    title: str | None = None,
    by: str = "owner",
    convert: Convert | None = None,
) -> DocumentImport:
    """Converts a document to Markdown, then stores the original and the Markdown, both journaled.

    The lane follows the file's type, which must match its contents. ``convert`` replaces the lanes, for tests.

    Raises:
        DriverError: The file is empty, of a type this importer does not read, damaged, encrypted, or has no text;
            or the document models are not downloaded. Nothing is stored then.
    """
    if not data:
        raise DriverError("the file is empty")
    extension = PurePath(filename).suffix.lower()
    _check_type(data, extension, filename)
    converted = (convert or _convert)(data, extension)
    if not converted.text.strip():
        raise DriverError(f"found no text in {filename}")
    name = title or markdown_title(converted.text) or PurePath(filename).stem
    original = kernel.add_source(data, extension=extension[1:], title=name, driver="document", by=by)
    text = converted.text.rstrip() + "\n"
    derived = kernel.add_source(
        text.encode("utf-8"),
        extension="md",
        title=name,
        driver="document",
        by=by,
        derived_from=original.source.hash,
        converter=converted.converter,
        method=converted.method,
    )
    log.info(
        "document_imported",
        filename=filename,
        original=original.source.hash,
        derived=derived.source.hash,
        converter=converted.converter,
        method=converted.method,
    )
    return DocumentImport(original=original, derived=derived)


def _check_type(data: bytes, extension: str, filename: str) -> None:
    if extension not in SUPPORTED:
        raise DriverError(f"{filename} is not a document this importer reads ({', '.join(SUPPORTED)})")
    magic = {
        **dict.fromkeys(OFFICE, (b"PK\x03\x04",)),
        ".pdf": (b"%PDF",),
        ".png": (b"\x89PNG",),
        ".jpg": (b"\xff\xd8",),
        ".jpeg": (b"\xff\xd8",),
        ".tif": (b"II*\x00", b"MM\x00*"),
        ".tiff": (b"II*\x00", b"MM\x00*"),
    }[extension]
    if extension in OFFICE and data.startswith(_OLE):
        raise DriverError(f"{filename} is password-protected, or an old binary Office file; save it as {extension}")
    if not data.startswith(magic):
        raise DriverError(f"{filename} does not look like a {extension} file")


def _convert(data: bytes, extension: str) -> Converted:
    if extension in OFFICE:
        return _office(data, extension)
    return _docling(data, extension)


# Each Office format's main part. MarkItDown reads anything else as plain text, so a damaged file would come back
# as its own bytes rather than as an error.
_OFFICE_PARTS = {".docx": "word/document.xml", ".pptx": "ppt/presentation.xml", ".xlsx": "xl/workbook.xml"}


def _office(data: bytes, extension: str) -> Converted:
    from markitdown import MarkItDown, MarkItDownException

    try:
        with zipfile.ZipFile(io.BytesIO(data)) as package:
            package.getinfo(_OFFICE_PARTS[extension])
    except (zipfile.BadZipFile, KeyError) as exc:
        raise DriverError(f"the {extension} file is damaged: it has no {_OFFICE_PARTS[extension]}") from exc
    try:
        result = MarkItDown(enable_plugins=False).convert_stream(io.BytesIO(data), file_extension=extension)
    except MarkItDownException as exc:
        raise DriverError(f"could not read the {extension} file: {exc}") from exc
    text = _SLIDE.sub(r"<!-- page \1 -->", result.text_content)
    return Converted(text=text, converter=converter_name("markitdown"), method="native")


def converter_name(module: str) -> str:
    """``module`` and the version of the distribution that provides it: conda-forge's docling is ``docling-slim``."""
    distribution = (packages_distributions().get(module) or [module])[0]
    return f"{module} {version(distribution)}"


def models_dir() -> Path:
    """Where ``pixi run doc-models`` puts Docling's and RapidOCR's models."""
    return resolve(MODELS_DIR)


@cache
def _converter(models: Path) -> Any:
    from docling.datamodel.base_models import InputFormat
    from docling.datamodel.pipeline_options import PdfPipelineOptions, RapidOcrOptions
    from docling.document_converter import DocumentConverter, ImageFormatOption, PdfFormatOption

    options = PdfPipelineOptions(
        artifacts_path=models,
        do_ocr=True,
        ocr_options=RapidOcrOptions(),
        do_table_structure=True,
        enable_remote_services=False,
        allow_external_plugins=False,
    )
    return DocumentConverter(
        allowed_formats=[InputFormat.PDF, InputFormat.IMAGE],
        format_options={
            InputFormat.PDF: PdfFormatOption(pipeline_options=options),
            InputFormat.IMAGE: ImageFormatOption(pipeline_options=options),
        },
    )


def _docling(data: bytes, extension: str) -> Converted:  # pragma: no cover - needs the models; covered by recordings
    from docling.datamodel.base_models import ConversionStatus, DocumentStream
    from docling.exceptions import ConversionError

    models = models_dir()
    if not models.is_dir():
        raise DriverError(f"the document models are not downloaded; run `pixi run doc-models` (to {models})")
    try:
        result = _converter(models).convert(DocumentStream(name=f"document{extension}", stream=io.BytesIO(data)))
    except ConversionError as exc:
        raise DriverError(f"could not read the {extension} file: {exc}") from exc
    if result.status != ConversionStatus.SUCCESS:
        errors = "; ".join(e.error_message for e in result.errors) or result.status.value
        raise DriverError(f"could not convert the {extension} file: {errors}")
    method: Method = "ocr" if extension in IMAGES or _pages_without_text(data, extension) else "layout"
    return Converted(text=paged_markdown(result.document), converter=converter_name("docling"), method=method)


def paged_markdown(document: Any) -> str:
    """A Docling document as Markdown, each page's text after its ``<!-- page N -->`` marker."""
    parts = []
    for page in sorted(document.pages):
        text = document.export_to_markdown(page_no=page).strip()
        parts.append(f"<!-- page {page} -->\n\n{text}" if text else f"<!-- page {page} -->")
    return "\n\n".join(parts)


def _pages_without_text(data: bytes, extension: str) -> list[int]:  # pragma: no cover - called only by _docling
    """The pages of a PDF that have no text layer, so Docling's OCR read them."""
    if extension not in PAGED:
        return []
    import pypdfium2  # type: ignore[import-untyped]

    pdf = pypdfium2.PdfDocument(data)
    try:
        return [n + 1 for n in range(len(pdf)) if not pdf[n].get_textpage().get_text_range().strip()]
    finally:
        pdf.close()
