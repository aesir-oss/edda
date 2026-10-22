# Document ingestion

How Edda turns PDFs, scans and Office files into sources the ingest job can read and cite. Part 1 is the reference
architecture the owner brought in (a Google AI Mode answer, 2026-10-22). Part 2 is what Edda takes from it, what it
changes and why. The work is tracked in [#23](https://github.com/aesir-oss/edda/issues/23) (the document importer)
and [#61](https://github.com/aesir-oss/edda/issues/61) (opt-in vision-model transcription).

## Part 1: the reference architecture

[reference-document-to-markdown.md](reference-document-to-markdown.md) is the owner's original, kept byte for byte
(the whitespace hooks skip it). In short, it routes documents into three lanes before converting them: native AST
converters for Office files, layout-aware extractors for born-digital PDFs, and OCR or vision-language models for
scans. It compares MarkItDown, MarkEverythingDown, Docling and Unstructured, offline Mac apps, and Adobe's PDF Extract
API, and closes with a MarkItDown script that converts a folder. "Vwen2.5 VL" in its table is probably Qwen2.5-VL.

The answer it came from ended with three questions: will this run offline or use cloud APIs; are the tables simple
rows or nested grids; and is the Markdown for an LLM/RAG pipeline or for people to read and edit.

## Part 2: what Edda does with it

The reference architecture is built for RAG: chunk documents and retrieve them. Edda is different. The model reads a
whole source and writes cited wiki pages, and a person reviews every change. Because "every claim has a source" is
the rule the kernel enforces, provenance matters more here than formatting polish.

**Answers to its questions, for Edda:** offline by default, with cloud only as an explicit opt-in using the owner's
own key. Tables vary, so table detection is needed. The Markdown goes first to the model and then to people checking
its citations.

### Kept

- **Lanes by document type**, not one extractor for everything. Office files through their own structure, born-digital
  PDFs through a layout engine, scans through OCR.
- **MarkItDown for Office files** (.docx, .pptx, .xlsx). It reads their XML directly, and it is light.
- **Local-first.** Every default lane runs on the machine.

### Changed

| Reference architecture | Edda | Why |
|---|---|---|
| Writes a `.md` next to the input, keeps nothing else | The **original file** stays unedited in `raw/` under its SHA-256; the Markdown is a second raw source whose `.meta.toml` records `derived_from`, the converter and its version, and the `method` (`native`, `layout`, `ocr`, `vlm`) | Citations must point at text that never changes. A newer converter adds new text instead of rewriting what old citations cite |
| MarkItDown as the unified tool, PDFs included | **Docling for PDFs**, MarkItDown for Office only | MarkItDown loses page boundaries and multi-column order on PDFs. Docling keeps reading order and records the page each block came from |
| No notion of pages | **Page markers** in the Markdown; the model cites "page 5" and `read_source` reads by page | The kernel checks citations; a page number it cannot verify is a citation it cannot trust |
| Route by file | Route **by page**: OCR only the pages without a text layer | Many PDFs mix born-digital and scanned pages |
| VLM as the modern default for scans | **OCR first** (Apple's on-device Vision OCR on macOS, Tesseract elsewhere); a VLM only as an opt-in, marked `method = "vlm"` (#61) | A VLM that misreads a scan produces plausible wrong text, which in Edda becomes a false citation that looks real |
| Adobe PDF Extract API as an option | Cloud converters only as an opt-in with the owner's key, like Anthropic and NVIDIA | It sends the document off the machine |
| Tables as Markdown "or HTML" | Markdown tables only; HTML fallbacks converted or flagged | Edda escapes raw HTML when it renders pages |
| Built for chunking | **No chunking**; sources too big for the context are read in parts | The model reads a source whole and proposes page edits; search runs over the wiki's pages, not over chunks |
| A script with `print`, `except Exception`, and direct file writes | A driver in `edda.drivers` that goes through the journaled `add_source` and raises `DriverError` | Every source is journaled once, errors are never swallowed, and logs are structured |

### Not adopted

- **MarkEverythingDown:** unknown provenance; the VLM path is covered by #61 with a pinned, approved model instead.
- **PyMuPDF and Marker:** AGPL and GPL with model-weight restrictions respectively, incompatible with Edda's license
  posture. PyMuPDF is not in the reference table, but it is a common suggestion for this job.
- **Unstructured:** capable, but built around RAG chunking, which Edda does not do.
