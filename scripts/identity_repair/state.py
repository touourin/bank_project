"""Migration locations and immutable source-policy loading."""

import csv
import hashlib
import json
from pathlib import Path

from marketing_refresh.summary_common import Sources

from .policy import Policy

ROOT = Path(__file__).resolve().parents[2]
STATE = ROOT / "data/identity-repair-20260922"
OUTPUT = ROOT / "outputs/identity-repair-20260922"
BASES = {"project": ROOT / "examples/mock", "desktop": Path.home() / "Desktop/五张表_修正版"}
FILES = (
    "客户标签.xlsx",
    "工商.xlsx",
    "征信.xlsx",
    "来源明细/工商明细.xlsx",
    "来源明细/征信明细.xlsx",
    "交易流水.xlsx",
)


def save(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n")


def sha(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def policy(version):
    source = STATE / version / "before"
    with (STATE / "customer_identity.before.csv").open(encoding="utf-8-sig", newline="") as stream:
        identities = list(csv.DictReader(stream))
    return Policy(Sources(source / "客户标签.xlsx").rows("数据"), identities)
