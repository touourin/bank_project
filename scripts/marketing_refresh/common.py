from __future__ import annotations

import csv
import gzip
import hashlib
import json
import random
from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "data/mock-sources"
OUTPUT = ROOT / "data/marketing-refresh-20260920"
DESKTOP = Path.home() / "Desktop/五张表_修正版"
ASOF = date(2026, 9, 17)
START = date(2025, 9, 18)
SEED = 20260920
TABLES = {
    "客户标签": "marketing_customer_tags",
    "客户旅程": "marketing_customer_journey",
    "征信": "marketing_credit",
    "工商": "marketing_business",
    "交易流水": "marketing_transactions",
}
RISK_NAMES = {
    "cashflow_decline": "经营回款下降、资金压力",
    "counterparty_concentration": "回款客户集中度上升",
    "credit_overdue": "贷款逾期",
    "legal_execution": "未结司法执行与经营异常",
    "activity_decline": "结算活跃度下降、流失迹象",
}


def stable(*parts):
    return int.from_bytes(hashlib.sha256("|".join(map(str, (SEED, *parts))).encode()).digest()[:8])


def rng(*parts):
    return random.Random(stable(*parts))


def dec(value):
    return Decimal(str(value or 0))


def money(value):
    return dec(value).quantize(Decimal(".01"), rounding=ROUND_HALF_UP)


def amount(value):
    return format(money(value), ".2f")


def day(value):
    value = str(value)
    return (
        date.fromisoformat(value[:10])
        if "-" in value
        else datetime.strptime(value[:8], "%Y%m%d").date()
    )


def compact(value):
    return value.strftime("%Y%m%d")


def read_csv(path):
    with Path(path).open(encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def dump(path, data):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(
        json.dumps(data, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8"
    )


def load(path):
    return json.loads(Path(path).read_text())


class RowsWriter:
    """Sparse JSONL intermediate, not an Excel writer."""

    def __init__(self, path, columns):
        self.path, self.columns = Path(path), columns
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.f = gzip.open(self.path, "wt", encoding="utf-8", compresslevel=1)
        self.count = 0
        self.digest = hashlib.sha256()

    def add(self, row):
        extra = row.keys() - set(self.columns)
        if extra:
            raise ValueError(f"Unexpected columns: {extra}")
        row = {k: str(v) for k, v in row.items() if v is not None and v != ""}
        line = json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n"
        self.f.write(line)
        self.digest.update(line.encode())
        self.count += 1

    def close(self):
        self.f.close()
        return {
            "rows": self.count,
            "columns": self.columns,
            "content_sha256": self.digest.hexdigest(),
        }


def rows(path):
    with gzip.open(path, "rt", encoding="utf-8") as f:
        for line in f:
            yield json.loads(line)
