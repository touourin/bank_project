"""Exact values, deterministic sampling and streaming CSV output."""

from __future__ import annotations

import csv
import hashlib
import json
import re
from collections import Counter
from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCALE = Decimal("0.00000001")


def stable(*values: object) -> int:
    return int.from_bytes(hashlib.sha256("|".join(map(str, values)).encode()).digest()[:8])


def dec(value: object) -> Decimal:
    return Decimal(str(value or 0))


def amount(value: object, scale: int = 8) -> str:
    return format(dec(value).quantize(Decimal(10) ** -scale, rounding=ROUND_HALF_UP), "f")


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as stream:
        return list(csv.DictReader(stream))


def write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def day(value: str) -> date:
    return datetime.strptime(value, "%Y%m%d").date()


def compact(value: date) -> str:
    return value.strftime("%Y%m%d")


def scalar_error(value: str, field: dict) -> str | None:
    if value == "":
        return "required value missing" if field.get("mock_required") else None
    dtype = field["type"].upper().replace(" ", "")
    if m := re.fullmatch(r"(?:VAR)?CHAR\((\d+)\)", dtype):
        if len(value) > int(m[1]):
            return f"exceeds {dtype}"
    if dtype == "INT" and (not re.fullmatch(r"-?\d+", value) or not -(2**31) <= int(value) < 2**31):
        return "invalid INT"
    if m := re.fullmatch(r"DECIMAL\((\d+),(\d+)\)", dtype):
        if not re.fullmatch(r"-?\d+(?:\.\d+)?", value):
            return "invalid decimal"
        whole, _, fraction = value.lstrip("-").partition(".")
        if len(whole.lstrip("0")) > int(m[1]) - int(m[2]) or len(fraction) > int(m[2]):
            return f"exceeds {dtype}"
    try:
        if dtype == "DATE":
            date.fromisoformat(value)
        elif dtype == "TIMESTAMP":
            datetime.fromisoformat(value)
        elif field.get("format") == "YYYYMMDD":
            if len(value) != 8 or not value.isascii() or not value.isdigit():
                return "invalid compact date"
            day(value)
    except ValueError:
        return "invalid date/time"
    return None


class CsvSink:
    """Stream validated rows; retain only coverage counters and uniqueness keys."""

    def __init__(self, path: Path, fields: list[dict], keys: list[str] | None = None):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path, self.fields = path, fields
        self.names = [f["name"] for f in fields]
        self.keys, self.seen = keys or [], set()
        self.stream = path.open("w", encoding="utf-8-sig", newline="")
        self.writer = csv.DictWriter(self.stream, fieldnames=self.names, lineterminator="\n")
        self.writer.writeheader()
        self.rows = 0
        self.nulls: Counter = Counter()
        self.zeros: Counter = Counter()

    def add(self, row: dict) -> None:
        if set(row) != set(self.names):
            raise ValueError(f"{self.path.name}: column mismatch {set(row) ^ set(self.names)}")
        for f in self.fields:
            v = row[f["name"]]
            if not isinstance(v, str):
                raise TypeError(f"{f['name']}: expected serialization string")
            if error := scalar_error(v, f):
                raise ValueError(f"{self.path.name}:{self.rows + 2}.{f['name']}: {error}: {v!r}")
            if v == "":
                self.nulls[f["name"]] += 1
            elif re.fullmatch(r"0(?:\.0+)?", v):
                self.zeros[f["name"]] += 1
        if self.keys:
            key = tuple(row[k] for k in self.keys)
            if key in self.seen:
                raise ValueError(f"{self.path.name}: duplicate key {key}")
            self.seen.add(key)
        self.writer.writerow(row)
        self.rows += 1

    def close(self) -> dict:
        self.stream.close()
        with self.path.open("rb") as source:
            digest = hashlib.file_digest(source, "sha256").hexdigest()
        return dict(
            rows=self.rows,
            columns=len(self.names),
            sha256=digest,
            field_coverage={
                n: dict(nonempty=self.rows - self.nulls[n], null=self.nulls[n], zero=self.zeros[n])
                for n in self.names
            },
        )


def aux_fields(names: list[str]) -> list[dict]:
    return [dict(name=n, type="STRING") for n in names]
