"""Writes the API's OpenAPI schema, which the web app's TypeScript types are generated from."""

import json
import sys
from pathlib import Path

from edda.api.app import create_app


def schema_json() -> str:
    """The OpenAPI schema as stable, indented JSON."""
    return json.dumps(create_app().openapi(), indent=2, sort_keys=True) + "\n"


def main(argv: list[str] | None = None) -> int:
    """Writes the schema to the path given, or to standard output."""
    args = sys.argv[1:] if argv is None else argv
    if args:
        Path(args[0]).write_text(schema_json(), encoding="utf-8")
    else:
        sys.stdout.write(schema_json())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
