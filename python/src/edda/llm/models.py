"""Known local models, and fetching them.

Each entry pins the file's URL, size and SHA-256, so a download is verified before it is used. Weights live in
``~/.cache/edda/models``, outside any repository.

    pixi run llm-fetch qwen3-14b
"""

import hashlib
import sys
from pathlib import Path

import httpx
import structlog
from pydantic import BaseModel, ConfigDict

log = structlog.get_logger("edda.llm.models")

MODELS_DIR = Path.home() / ".cache" / "edda" / "models"


class KnownModel(BaseModel):
    """A model file Edda has been tested with."""

    model_config = ConfigDict(frozen=True)

    name: str
    file: str
    url: str
    size: int
    sha256: str
    license: str


KNOWN: dict[str, KnownModel] = {
    m.name: m
    for m in [
        KnownModel(
            name="qwen3-14b",
            file="Qwen3-14B-Q4_K_M.gguf",
            url="https://huggingface.co/Qwen/Qwen3-14B-GGUF/resolve/main/Qwen3-14B-Q4_K_M.gguf",
            size=9_001_752_960,
            sha256="500a8806e85ee9c83f3ae08420295592451379b4f8cf2d0f41c15dffeb6b81f0",
            license="Apache-2.0",
        ),
    ]
}


class FetchError(RuntimeError):
    """A model could not be downloaded, or the download does not match its pinned SHA-256."""


def sha256_file(path: Path) -> str:
    """The SHA-256 of a file, read in 1 MiB chunks."""
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def fetch(name: str, models_dir: Path = MODELS_DIR, client: httpx.Client | None = None) -> Path:
    """Downloads a known model unless a verified copy is already there, and returns its path.

    The file is written to ``<file>.part`` and renamed only after its SHA-256 matches, so an interrupted or tampered
    download is never used.

    Raises:
        FetchError: The name is unknown, the download failed, or the hash does not match.
    """
    if name not in KNOWN:
        raise FetchError(f"unknown model {name!r}; known: {', '.join(sorted(KNOWN))}")
    model = KNOWN[name]
    target = models_dir / model.file
    if target.exists() and sha256_file(target) == model.sha256:
        log.info("model_present", name=name, path=str(target))
        return target
    models_dir.mkdir(parents=True, exist_ok=True)
    part = target.with_name(target.name + ".part")
    http = client or httpx.Client(follow_redirects=True, timeout=httpx.Timeout(60.0, read=600.0))
    log.info("model_download", name=name, url=model.url, size=model.size)
    try:
        with http.stream("GET", model.url) as response:
            response.raise_for_status()
            with part.open("wb") as out:
                for chunk in response.iter_bytes(1 << 20):
                    out.write(chunk)
    except httpx.HTTPError as exc:
        raise FetchError(f"downloading {model.url} failed: {exc}") from exc
    actual = sha256_file(part)
    if actual != model.sha256:
        part.unlink()
        raise FetchError(f"{model.file} hashes to {actual}, not the pinned {model.sha256}; the download was removed")
    part.rename(target)
    log.info("model_verified", name=name, path=str(target))
    return target


def main(argv: list[str] | None = None) -> int:
    """``llm-fetch <name>``: download and verify a known model."""
    from edda.log import configure_logging

    configure_logging()
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 1:
        log.error("usage", expected="llm-fetch <name>", known=sorted(KNOWN))
        return 2
    try:
        fetch(args[0])
    except FetchError as exc:
        log.error("model_fetch_failed", error=str(exc))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
