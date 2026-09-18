"""Load operator-managed mapping profiles, never code or caller-provided file paths."""

import json
from pathlib import Path

from bank_project.contracts.mapping import MappingCatalog, MappingProfile


def load_mappings(folder: Path) -> MappingCatalog:
    if not folder.is_dir():
        raise ValueError("BANK_MAPPING_DIR must point to an existing profile directory")
    profiles = []
    for path in sorted(folder.glob("*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        reference = data.pop("schema_ref", None)
        if reference:
            schema_path = (folder / reference).resolve()
            if not schema_path.is_relative_to(folder.resolve().parent):
                raise ValueError("Schema references must remain in the configuration directory")
            schema = json.loads(schema_path.read_text(encoding="utf-8"))
            specification = schema["tables"][data["table"]]
            data["columns"] = [
                {
                    key: value
                    for key, value in column.items()
                    if key in {"name", "sql_type", "format", "source_nullable"}
                }
                for column in specification["columns"]
            ]
            data.setdefault("primary_key", specification["primary_key"])
            if data.get("event"):
                data["event"]["dictionary"] = schema.get("events", {})
                data["event"]["pending_codes"] = [
                    item["mock_code"] for item in schema.get("pending_bank_confirmation", [])
                ]
        profiles.append(MappingProfile.model_validate(data))
    if len({p.id for p in profiles}) != len(profiles):
        raise ValueError("Mapping IDs must be unique")
    if any(p.id == "generic_record" for p in profiles):
        raise ValueError("generic_record is a reserved fallback mapping ID")
    return MappingCatalog(profiles=tuple(profiles))
