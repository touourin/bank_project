"""Refresh the offline API contract without starting the application or connecting to Neo4j."""

import argparse
import json
from pathlib import Path

from bank_project.main import create_app


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="Fail if the saved schema is stale")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    # Generating OpenAPI does not enter lifespan, so it needs no environment or credentials.
    content = json.dumps(create_app().openapi(), ensure_ascii=False, indent=2) + "\n"
    path = root / "docs/openapi.json"
    if args.check:
        if not path.exists() or path.read_text(encoding="utf-8") != content:
            parser.exit(1, "OpenAPI is out of date; run make schema.\n")
    else:
        path.write_text(content, encoding="utf-8")


if __name__ == "__main__":
    main()
