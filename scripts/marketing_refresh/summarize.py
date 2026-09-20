"""Prepare summary matrices from each existing workbook independently. No XLSX writes."""

from __future__ import annotations

import argparse
import shutil

from . import summary_business, summary_credit
from .split_sources import BASES, ROOT, digest, write_json
from .summary_common import Sources, customer_lookup

OUT = ROOT / "data/subject-summary-20260920"


def prepare(version):
    base = BASES[version]
    customers = customer_lookup(base / "客户标签.xlsx")
    for label, builder in [("征信", summary_credit.build), ("工商", summary_business.build)]:
        original = base / f"{label}.xlsx"
        folder = OUT / version / label
        folder.mkdir(parents=True, exist_ok=True)
        before = folder / "before.xlsx"
        if before.exists() and digest(original) != digest(before):
            raise ValueError(f"Input changed since preparation: {original}")
        if not before.exists():
            shutil.copy2(original, before)
        source = Sources(before)
        summary = builder(source, customers)
        payload = summary.payload(version, digest(before))
        write_json(folder / "input.json", payload)
        print(
            f"Prepared {version}/{label}: {len(payload['rows'])} rows × {len(payload['fields'])} fields",
            flush=True,
        )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("version", choices=BASES)
    prepare(parser.parse_args().version)
