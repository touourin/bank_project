"""Read-only, bounded-memory audit of the three already coherent source tables."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from collections import defaultdict
from zipfile import ZipFile

from lxml import etree

from .split_sources import BASES, ROOT, N, digest, write_json

OUT = ROOT / "data/subject-summary-20260920"
KEYS = {
    "客户标签": ["cust_ind", "dt"],
    "客户旅程": ["DT", "ROWKEY"],
    "交易流水": ["src_sys", "accno", "txn_dt", "acc_dtl_sn"],
}


def audit(version: str, label: str) -> dict:
    path = BASES[version] / f"{label}.xlsx"
    before = digest(path)
    with ZipFile(path) as archive:
        strings = []
        if "xl/sharedStrings.xml" in archive.namelist():
            strings = [
                "".join(el.itertext())
                for el in etree.fromstring(archive.read("xl/sharedStrings.xml"))
            ]
        headers, positions, hashes, nulls = [], {}, [], []
        keys, duplicate_keys, missing_keys, count = set(), 0, 0, 0
        invalid_json = 0
        customers = set()
        with archive.open("xl/worksheets/sheet1.xml") as stream:
            for _, row in etree.iterparse(stream, events=("end",), tag=N + "row"):
                if not headers:
                    for cell in row:
                        address = re.match(r"[A-Z]+", cell.get("r"))[0]
                        val = cell.findtext(N + "v")
                        if cell.get("t") == "s":
                            val = strings[int(val)]
                        elif cell.get("t") == "inlineStr":
                            val = "".join(cell.itertext())
                        positions[address] = len(headers)
                        headers.append(val)
                    assert len(headers) == len(set(headers)), "Duplicate field name"
                    hashes = [hashlib.sha256() for _ in headers]
                    nulls = [0] * len(headers)
                    key_indexes = [headers.index(k) for k in KEYS[label]]
                    customer_index = headers.index("CUST_ID" if label == "客户旅程" else "cust_ind")
                    json_index = headers.index("PROPERTIES") if label == "客户旅程" else None
                else:
                    values = [None] * len(headers)
                    types = ["s"] * len(headers)
                    for cell in row:
                        if cell.find(N + "f") is not None:
                            raise ValueError("Unexpected formula in raw source table")
                        idx = positions[cell.get("r").rstrip("0123456789")]
                        val = cell.findtext(N + "v")
                        typ = cell.get("t", "n")
                        if typ == "s":
                            val = strings[int(val)]
                        elif typ == "inlineStr":
                            val = "".join(cell.itertext())
                        values[idx] = val
                        types[idx] = "s" if typ in {"s", "inlineStr"} else typ
                    for i, value in enumerate(values):
                        if value is None:
                            nulls[i] += 1
                            encoded = b"~"
                        else:
                            encoded = (types[i] + value).encode()
                        hashes[i].update(len(encoded).to_bytes(4, "big") + encoded)
                    key = tuple(values[i] for i in key_indexes)
                    missing_keys += any(v is None for v in key)
                    token = hashlib.sha256(json.dumps(key).encode()).digest()
                    duplicate_keys += token in keys
                    keys.add(token)
                    customers.add(values[customer_index])
                    if json_index is not None:
                        try:
                            if not isinstance(json.loads(values[json_index]), dict):
                                invalid_json += 1
                        except (TypeError, ValueError):
                            invalid_json += 1
                    count += 1
                    if count % 100000 == 0:
                        print(f"Audited {version}/{label}: {count:,} rows", flush=True)
                row.clear()
                while row.getprevious() is not None:
                    del row.getparent()[0]
    assert before == digest(path), "Source changed while auditing"
    groups = defaultdict(list)
    for name, h, blanks in zip(headers, hashes, nulls, strict=True):
        if blanks < count:
            groups[h.hexdigest()].append(name)
    result = {
        "path": str(path),
        "sha256": before,
        "rows": count,
        "columns": len(headers),
        "key": KEYS[label],
        "duplicate_keys": duplicate_keys,
        "missing_keys": missing_keys,
        "invalid_event_json": invalid_json,
        "customers": sorted(str(c) for c in customers),
        "identical_value_columns": [v for v in groups.values() if len(v) > 1],
        "empty_columns": [n for n, blanks in zip(headers, nulls, strict=True) if blanks == count],
        "decision": "Preserve source contract; equal values alone do not establish duplicate meaning.",
    }
    write_json(OUT / version / f"{label}.audit.json", result)
    print(
        f"Verified {version}/{label}: {count:,} rows; duplicate keys={duplicate_keys}", flush=True
    )
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("version", choices=BASES)
    args = parser.parse_args()
    for label in KEYS:
        audit(args.version, label)
