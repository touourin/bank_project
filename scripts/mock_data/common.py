"""CSV and exact-number serialization shared by the offline mock tools."""

from __future__ import annotations

import csv
import json
from collections import Counter
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SCHEMA = ROOT / "configs/bank/schema.json"
DEFAULT_OUTPUT = ROOT / "examples/mock"
TAG_TABLE = "CCM_C_CUST_FLAG_INFO"
JOURNEY_TABLE = "E_CRM_C_CUST_TOUR_EVT_SUM"
SCALE = Decimal("0.00000001")


def load_schema(path: Path = DEFAULT_SCHEMA) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def compact_date(value: date) -> str:
    return value.strftime("%Y%m%d")


def parse_date(value: str) -> date:
    if len(value) != 8 or not value.isascii() or not value.isdigit():
        raise ValueError("日期必须为 YYYYMMDD")
    return datetime.strptime(value, "%Y%m%d").date()


def json_exact(value) -> str:
    """Encode Decimal as a JSON number without ever converting it to float."""
    if isinstance(value, Decimal):
        if not value.is_finite():
            raise ValueError("Non-finite JSON number")
        return format(value, "f")
    if isinstance(value, dict):
        return (
            "{"
            + ",".join(
                json.dumps(key, ensure_ascii=False) + ":" + json_exact(item)
                for key, item in value.items()
            )
            + "}"
        )
    if isinstance(value, list):
        return "[" + ",".join(json_exact(item) for item in value) + "]"
    return json.dumps(value, ensure_ascii=False, allow_nan=False)


def write_csv(path: Path, fields: list[str], rows: list[dict]) -> None:
    # Excel uses the BOM to recognize UTF-8 when a CSV is opened directly.
    with path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def read_csv(path: Path) -> tuple[list[str], list[dict]]:
    with path.open(encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream)
        rows = list(reader)
        return reader.fieldnames or [], rows


def confirmation_coverage(schema: dict, journeys: list[dict]) -> list[dict]:
    """Report actual coverage while preserving outstanding bank confirmations."""
    counts = Counter(row.get("EVT_TYPE") for row in journeys)
    return [
        {**item, "mock_rows": counts[item["mock_code"]]}
        for item in schema["pending_bank_confirmation"]
    ]
