"""The vault ``pixi run dev`` serves: a copy of ``examples/vault`` in ``.edda/dev-vault``, never the example itself."""

import shutil
import sys
from pathlib import Path

import structlog

log = structlog.get_logger("edda.devvault")

EXAMPLE = Path("examples") / "vault"
DEV_VAULT = Path(".edda") / "dev-vault"
_RUNTIME = shutil.ignore_patterns(".edda", "proposals", "jobs")


def prepare(root: Path, *, fresh: bool = False) -> Path:
    """Copies the example vault to ``.edda/dev-vault`` unless a copy is there; ``fresh`` replaces any copy first."""
    target = root / DEV_VAULT
    if fresh and target.exists():
        shutil.rmtree(target)
        log.info("dev_vault_removed", vault=str(target))
    if target.exists():
        log.info("dev_vault_kept", vault=str(target))
        return target
    shutil.copytree(root / EXAMPLE, target, ignore=_RUNTIME)
    log.info("dev_vault_copied", vault=str(target), source=str(root / EXAMPLE))
    return target


def main(argv: list[str] | None = None) -> int:
    """``devvault <project root> [keep|fresh]``."""
    from edda.log import configure_logging

    configure_logging()
    args = sys.argv[1:] if argv is None else argv
    if len(args) not in (1, 2) or (len(args) == 2 and args[1] not in ("keep", "fresh")):
        log.error("usage", expected="devvault <project root> [keep|fresh]")
        return 2
    prepare(Path(args[0]), fresh=len(args) == 2 and args[1] == "fresh")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
