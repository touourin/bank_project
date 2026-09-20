"""Initialize the independent local ontology catalog's credentials."""

import os
import secrets
from pathlib import Path
from urllib.parse import urlsplit

from dotenv import dotenv_values, set_key

ROOT = Path(__file__).resolve().parents[1]


def prepare(root: Path) -> None:
    env = root / ".env.ontology"
    if env.is_symlink():
        raise ValueError(".env.ontology must not be a symlink")
    values = {**dotenv_values(env), **os.environ}
    target = urlsplit(values.get("BANK_ONTOLOGY_URI", "bolt://127.0.0.1:7691"))
    if target.scheme != "bolt" or target.hostname not in {"localhost", "127.0.0.1", "::1"}:
        raise ValueError("Only a local Bolt endpoint is supported")
    if values.get("BANK_ONTOLOGY_USER", "neo4j") != "neo4j":
        raise ValueError("The local container uses the neo4j user")
    defaults = {
        "BANK_ONTOLOGY_URI": "bolt://127.0.0.1:7691",
        "BANK_ONTOLOGY_USER": "neo4j",
        "BANK_ONTOLOGY_PASSWORD": secrets.token_urlsafe(36),
        "BANK_ONTOLOGY_DATABASE": "neo4j",
    }
    if not env.exists():
        env.touch(mode=0o600)
    for key, value in defaults.items():
        if not values.get(key):
            set_key(env, key, value)
    env.chmod(0o600)


if __name__ == "__main__":
    prepare(ROOT)
    print("Local ontology configuration is in .env.ontology; credentials were not printed.")
