"""Keep project CSV mirrors and distributable source archives in sync."""

import csv
import itertools
import json
import shutil
from collections import Counter
from zipfile import ZIP_DEFLATED, ZipFile

from .policy import REVISION
from .state import ROOT, STATE, policy, save, sha
from .workbooks import authored_cells

SOURCE = ROOT / "data/mock-sources"
LEGAL_TABLES = {
    "T_SAIC_BASIC",
    "T_SAIC_ORGDETAIL",
    "T_SAIC_PERSON",
    "T_SAIC_FRPOSITION",
    "T_SAIC_FRINV",
    "VW_GSGR_RYPOSFR",
    "VW_GSGR_RYPOSPER",
    "PBCEC_EC03_SENIOREXECUTIVE",
}
CONTROLLER_TABLES = {
    "T_SAIC_SHAREHOLDER",
    "T_SAIC_STOCKPAWN",
    "VW_GSGR_RYPOSSHA",
    "PBCEC_EC02_CONTRIBUTIVE",
    "PBCEC_EC05_ACTUALCONTROLLER",
}


def backup(path):
    original = STATE / "csv-before" / path.relative_to(ROOT)
    original.parent.mkdir(parents=True, exist_ok=True)
    if not original.exists():
        shutil.copy2(path, original)
    elif sha(original) != sha(path):
        raise ValueError(f"CSV migration has already changed {path}")
    return original


def run():
    p, templates = policy("project"), authored_cells()
    manifest_path = SOURCE / "manifest.json"
    backup(manifest_path)
    manifest = json.loads(manifest_path.read_text())
    patches = []
    mappings = [
        (path, path.stem)
        for folder in ("business", "credit", "transactions")
        for path in sorted((SOURCE / folder).glob("*.csv"))
    ]
    mappings += [
        (SOURCE / "expected/customer_tags.csv", "客户标签"),
        (ROOT / "examples/mock/CCM_C_CUST_FLAG_INFO.csv", "客户标签"),
        (SOURCE / "reference/customer_identity.csv", "people"),
        (SOURCE / "reference/source_row_links.csv", "links"),
    ]
    for path, table in mappings:
        table = "交易流水" if table == "交易流水表" else table
        temporary = path.with_suffix(".repairing.csv")
        count = Counter()

        def transformed(row, table=table):
            if table == "people":
                wanted = row.copy()
                if row["person_cust_id"] in p.people:
                    wanted["person_name"] = p.people[row["person_cust_id"]].name
            elif table == "links":
                wanted = row.copy()
                company = p.companies.get(row["cust_ind"])
                role = (
                    "legal"
                    if row["table"] in LEGAL_TABLES
                    else "controller"
                    if row["table"] in CONTROLLER_TABLES
                    else None
                )
                if company and role:
                    wanted["person_cust_id"] = company[role]
            else:
                wanted = p.transform(table, row)
            return {k: v if v is not None else "" for k, v in wanted.items()}

        with (
            path.open(encoding="utf-8-sig", newline="") as stream,
            temporary.open("w", encoding="utf-8-sig", newline="") as out,
        ):
            reader = csv.DictReader(stream)
            writer = csv.DictWriter(out, fieldnames=reader.fieldnames, lineterminator="\n")
            writer.writeheader()
            nrows = 0
            for row in reader:
                nrows += 1
                wanted = transformed(row)
                for key, value in wanted.items():
                    if value != row[key]:
                        assert value in templates or (not value and None in templates), (
                            "Value was not authored by Artifact Tool"
                        )
                        count[key] += 1
                writer.writerow(wanted)
        if not count:
            temporary.unlink()
            continue
        original = backup(path)
        # Read every written CSV cell independently before replacing the mirror.
        with (
            original.open(encoding="utf-8-sig", newline="") as a,
            temporary.open(encoding="utf-8-sig", newline="") as b,
        ):
            left, right = csv.DictReader(a), csv.DictReader(b)
            assert left.fieldnames == right.fieldnames
            for old, new in itertools.zip_longest(left, right):
                assert old is not None and new == transformed(old), f"CSV readback failed: {path}"
        temporary.replace(path)
        relative = str(path.relative_to(SOURCE)) if path.is_relative_to(SOURCE) else None
        if relative in manifest["files"]:
            manifest["files"][relative]["sha256"] = sha(path)
        patches.append(
            dict(
                file=str(path.relative_to(ROOT)),
                rows=nrows,
                changed_cells=dict(count),
                sha256=sha(path),
            )
        )
        print("Verified CSV", path.name, sum(count.values()), flush=True)
    manifest["identity_relation_revision"] = dict(
        revision=REVISION,
        coverage=p.export()["coverage"],
        plan="identity-relations.json",
        previous_semantic_reports="Historical reports describe the pre-repair revision; identity repair verification is separate.",
    )
    manifest["baseline_tags_sha256"] = sha(ROOT / "examples/mock/CCM_C_CUST_FLAG_INFO.csv")
    save(manifest_path, manifest)
    save(SOURCE / "identity-relations.json", p.export())
    for path in SOURCE.glob("*_mock.zip"):
        backup(path)
        temporary = path.with_suffix(".repairing.zip")
        with ZipFile(path) as old, ZipFile(temporary, "w", ZIP_DEFLATED, compresslevel=3) as new:
            for name in old.namelist():
                item = SOURCE / name
                if item.is_file():
                    new.write(item, name)
                else:
                    new.writestr(name, old.read(name))
            if "identity-relations.json" not in old.namelist():
                new.write(SOURCE / "identity-relations.json", "identity-relations.json")
        temporary.replace(path)
        print("Updated source archive", path.name, flush=True)
    save(STATE / "csv-verified.json", {"revision": REVISION, "files": patches})


if __name__ == "__main__":
    run()
