"""The key the local model server requires, so other programs on the machine (a web page, say) cannot use it.

``pixi run -e llm llm-serve`` creates it on first use; the ``local`` endpoint reads it. It never leaves the machine.
"""

import os
import secrets
import sys
from collections.abc import Mapping
from pathlib import Path

import structlog

log = structlog.get_logger("edda.llm.localkey")

KEY_FILE = "~/.cache/edda/llm-key"


def resolve(path: str, env: Mapping[str, str] | None = None) -> Path:
    """``path`` with a leading ``~`` expanded from ``HOME`` in ``env`` (default: the environment)."""
    home = (os.environ if env is None else env).get("HOME")
    if home and (path == "~" or path.startswith("~/")):
        return Path(home) / path[2:]
    return Path(path)


def read_key(path: Path) -> str | None:
    """The first key in ``path`` (blank lines and ``#`` comments skipped), or None when the file is missing."""
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        return None
    return next((line.strip() for line in lines if line.strip() and not line.lstrip().startswith("#")), None)


def ensure_key(path: Path) -> bool:
    """Writes a new random key to ``path``, readable only by its owner, unless one is there; True when it wrote one."""
    if read_key(path):
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(f"# Edda's key for the local model server; llm-serve requires it.\n{secrets.token_urlsafe(32)}\n")
    os.chmod(path, 0o600)
    return True


def main(argv: list[str] | None = None) -> int:
    """``localkey [path]``: make sure the key file exists. The key itself is never printed or logged."""
    from edda.log import configure_logging

    configure_logging()
    args = sys.argv[1:] if argv is None else argv
    path = resolve(args[0] if args else KEY_FILE)
    log.info("local_key_created" if ensure_key(path) else "local_key_present", path=str(path))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
