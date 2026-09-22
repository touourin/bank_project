"""Sparse Artifact Tool cell patches, preserving all other OpenXML content.

The ledger is too large for an in-memory workbook. Artifact Tool authors a
dictionary of replacement cells; this adapter streams those cells into their
original addresses. Unchanged rows and all non-worksheet ZIP members stay exact.
"""

from __future__ import annotations

import argparse
import copy
import json
import re
import shutil
from collections import Counter
from zipfile import ZIP_DEFLATED, ZipFile

from lxml import etree as E

from .fingerprints import PROJECTIONS, Fingerprint
from .state import BASES, FILES, OUTPUT, ROOT, STATE, policy, save, sha

NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
ROW = re.compile(rb"<(?:\w+:)?row\b[^>]*(?:/>|>.*?</(?:\w+:)?row>)", re.S)
WRAPPER = b'<root xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:x="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'


def strings(archive):
    if "xl/sharedStrings.xml" not in archive.namelist():
        return []
    return [
        "".join(t.text or "" for t in si.iter(NS + "t"))
        for si in E.fromstring(archive.read("xl/sharedStrings.xml"))
    ]


def value(cell, shared):
    if cell.find(NS + "f") is not None:
        raise ValueError("Unexpected formula in a source field")
    if cell.get("t") == "inlineStr":
        return "".join(t.text or "" for t in cell.iter(NS + "t")) or None
    raw = cell.findtext(NS + "v")
    return shared[int(raw)] if cell.get("t") == "s" and raw is not None else raw or None


def parse(raw):
    return E.fromstring(WRAPPER + raw + b"</root>")[0]


def parts(stream):
    """Yield (is_row, exact XML bytes), retaining bounded memory."""
    buffer = b""
    while block := stream.read(1024 * 1024):
        buffer += block
        end = 0
        for match in ROW.finditer(buffer):
            if match.start() > end:
                yield False, buffer[end : match.start()]
            yield True, match[0]
            end = match.end()
        buffer = buffer[end:]
        # The final worksheet footer can have no more rows.
        if len(buffer) > 8 * 1024 * 1024:
            raise ValueError("Unexpectedly large XML row or footer")
    if buffer:
        yield False, buffer


def sheets(archive):
    rels = {
        r.get("Id"): r.get("Target")
        for r in E.fromstring(archive.read("xl/_rels/workbook.xml.rels"))
    }
    result = {}
    for sheet in E.fromstring(archive.read("xl/workbook.xml")).find(NS + "sheets"):
        rid = sheet.get("{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id")
        target = rels[rid]
        path = target.lstrip("/") if target.startswith("/") else "xl/" + target
        result[path] = sheet.get("name")
    return result


def prepare():
    STATE.mkdir(parents=True, exist_ok=True)
    original = ROOT / "data/mock-sources/reference/customer_identity.csv"
    reference = STATE / "customer_identity.before.csv"
    if not reference.exists():
        shutil.copy2(original, reference)
    values = {None}
    for version, base in BASES.items():
        directory = STATE / version / "before"
        manifest = {}
        for name in (*FILES, "客户旅程.xlsx"):
            path, backup = base / name, directory / name
            backup.parent.mkdir(parents=True, exist_ok=True)
            if backup.exists() and sha(backup) != sha(path):
                raise ValueError(f"Input changed since backup: {path}")
            if not backup.exists():
                shutil.copy2(path, backup)
            manifest[name] = sha(backup)
        p = policy(version)
        plan = p.export()
        save(STATE / version / "plan.json", plan)
        save(STATE / version / "before.json", manifest)
        for person in p.people.values():
            values.update((person.name, person.id, person.certificate))
        for company in p.companies.values():
            values.update((company["group_id"], company["group_name"]))
        print(version, plan["coverage"], flush=True)
    values.update(("男", "女"))
    save(STATE / "values.json", sorted(values, key=lambda x: x or ""))


def authored_cells():
    values = json.loads((STATE / "values.json").read_text())
    with ZipFile(STATE / "authored.xlsx") as archive:
        shared = strings(archive)
        cells = {
            int(re.search(r"\d+", c.get("r"))[0]): c
            for c in E.fromstring(archive.read("xl/worksheets/sheet1.xml")).iter(NS + "c")
        }
        result = {}
        for i, expected in enumerate(values, 1):
            cell = cells[i]
            if value(cell, shared) != expected:
                raise ValueError("Artifact Tool value readback mismatch")
            if cell.get("t") == "s":
                cell.find(NS + "v").text = expected
                cell.set("t", "str")
            result[expected] = cell
        return result


def rewrite(path, output, p, templates):
    changes, counts = {}, {}
    with (
        ZipFile(path) as source,
        ZipFile(output, "w", ZIP_DEFLATED, compresslevel=3, allowZip64=True) as destination,
    ):
        shared, titles = strings(source), sheets(source)
        for item in source.infolist():
            title = titles.get(item.filename)
            if title is None or title.endswith("说明"):
                destination.writestr(item, source.read(item))
                continue
            table = path.stem if title == "数据" else title
            counter, nrows = Counter(), 0
            with (
                source.open(item) as stream,
                destination.open(item.filename, "w", force_zip64=True) as out,
            ):
                headers = None
                for is_row, raw in parts(stream):
                    if not is_row:
                        out.write(raw)
                        continue
                    row = parse(raw)
                    cells = {re.match(r"[A-Z]+", c.get("r"))[0]: c for c in row}
                    if headers is None:
                        headers = {col: value(c, shared) for col, c in cells.items()}
                        if len(set(headers.values())) != len(headers):
                            raise ValueError(f"Duplicate columns in {title}")
                        out.write(raw)
                        continue
                    nrows += 1
                    record = {
                        key: value(cells[col], shared) if col in cells else None
                        for col, key in headers.items()
                    }
                    wanted = p.transform(table, record)
                    delta = {key: val for key, val in wanted.items() if val != record[key]}
                    if delta:
                        for col, key in headers.items():
                            if key not in delta:
                                continue
                            if col not in cells:
                                raise ValueError(
                                    f"Missing source cell: {title}/{col}{row.get('r')}"
                                )
                            old = cells[col]
                            cell = copy.deepcopy(templates[delta[key]])
                            cell.set("r", old.get("r"))
                            if old.get("s") is None:
                                cell.attrib.pop("s", None)
                            else:
                                cell.set("s", old.get("s"))
                            row.replace(old, cell)
                            counter[key] += 1
                        # Validate all cells before packaging, including untouched numeric lexemes.
                        actual = dict.fromkeys(headers.values())
                        actual.update(
                            {
                                headers[re.match(r"[A-Z]+", c.get("r"))[0]]: value(c, shared)
                                for c in row
                            }
                        )
                        if actual != wanted:
                            raise ValueError(f"Unexpected cell difference: {title}/{row.get('r')}")
                        out.write(E.tostring(row, encoding="utf-8"))
                    else:
                        out.write(raw)
                    if nrows % 100000 == 0:
                        print(f"{path.parent.name}/{path.name}: {nrows:,} rows", flush=True)
            counts[title], changes[title] = nrows, dict(counter)
    return {"rows": counts, "changed_cells": changes, "sha256": sha(output)}


def build(version, only=None):
    directory = OUTPUT / version
    directory.mkdir(parents=True, exist_ok=True)
    p, templates = policy(version), authored_cells()
    manifest_path = STATE / version / "built.json"
    manifest = json.loads(manifest_path.read_text()) if only and manifest_path.exists() else {}
    for name in (only,) if only else FILES:
        output = directory / name
        output.parent.mkdir(parents=True, exist_ok=True)
        manifest[name] = rewrite(STATE / version / "before" / name, output, p, templates)
        save(STATE / version / "built.json", manifest)
        print(version, name, manifest[name]["changed_cells"], flush=True)


def verify(version):
    """Independent full readback: numeric values, styles, formulas, package shape."""
    p = policy(version)
    built = json.loads((STATE / version / "built.json").read_text())
    for name in FILES:
        original, output = STATE / version / "before" / name, OUTPUT / version / name
        count = Counter()
        projected_before = Fingerprint(PROJECTIONS.get(original.stem, ()))
        projected_after = Fingerprint(PROJECTIONS.get(original.stem, ()))
        with ZipFile(original) as before, ZipFile(output) as after:
            assert before.namelist() == after.namelist(), "ZIP members changed"
            old_strings, new_strings = strings(before), strings(after)
            titles = sheets(before)
            for item in before.infolist():
                title = titles.get(item.filename)
                if title is None or title.endswith("说明"):
                    assert before.read(item) == after.read(item.filename), item.filename
                    continue
                table = original.stem if title == "数据" else title
                with before.open(item) as a, after.open(item.filename) as b:

                    def rows(stream):
                        for _, row in E.iterparse(stream, events=("end",), tag=NS + "row"):
                            yield row
                            row.clear()
                            while row.getprevious() is not None:
                                del row.getparent()[0]

                    import itertools

                    headers = None
                    for x, y in itertools.zip_longest(rows(a), rows(b)):
                        assert x is not None and y is not None, "Row count changed"
                        assert dict(x.attrib) == dict(y.attrib), "Row attributes changed"
                        assert len(x) == len(y), "Cell count changed"
                        vals = [value(c, old_strings) for c in x]
                        if headers is None:
                            headers = {
                                c.get("r").rstrip("0123456789"): v
                                for c, v in zip(x, vals, strict=True)
                            }
                            expected = dict(zip(headers.values(), vals, strict=True))
                        else:
                            record = {
                                headers[c.get("r").rstrip("0123456789")]: v
                                for c, v in zip(x, vals, strict=True)
                            }
                            expected = p.transform(table, record)
                            count[title] += 1
                            if title == "数据" and original.stem in PROJECTIONS:
                                projected_before.add(record)
                                projected_after.add(expected)
                        for old, new in zip(x, y, strict=True):
                            assert old.get("r") == new.get("r") and old.get("s") == new.get("s"), (
                                "Address/style changed"
                            )
                            key = headers[old.get("r").rstrip("0123456789")]
                            assert value(new, new_strings) == expected[key], (
                                title,
                                new.get("r"),
                                key,
                            )
                            if value(old, old_strings) == expected[key]:
                                assert dict(old.attrib) == dict(new.attrib), (
                                    "Untouched cell type changed"
                                )
                        if sum(count.values()) and sum(count.values()) % 100000 == 0:
                            print(f"Verified {version}/{name}: {sum(count.values()):,}", flush=True)
        assert dict(count) == built[name]["rows"], name
        assert sha(output) == built[name]["sha256"], "Built file changed"
        if projected_before.rows:
            built[name]["projection_before"] = projected_before.result()
            built[name]["projection_after"] = projected_after.result()
        print(f"Verified every cell: {version}/{name}", flush=True)
    save(STATE / version / "verified.json", built)


def install(version):
    verified = json.loads((STATE / version / "verified.json").read_text())
    before = json.loads((STATE / version / "before.json").read_text())
    for name, result in verified.items():
        current, output = BASES[version] / name, OUTPUT / version / name
        assert sha(current) in {before[name], result["sha256"]}, "Live file changed"
        assert sha(output) == result["sha256"], "Verified output changed"
    for name in verified:
        current = BASES[version] / name
        temporary = current.with_suffix(".repairing.xlsx")
        shutil.copy2(OUTPUT / version / name, temporary)
        temporary.replace(current)
    assert sha(BASES[version] / "客户旅程.xlsx") == before["客户旅程.xlsx"]
    print(f"Installed verified {version} workbooks", flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=("prepare", "build", "verify", "install"))
    parser.add_argument("version", nargs="?", choices=BASES)
    parser.add_argument("--only", choices=FILES)
    args = parser.parse_args()
    if args.action == "prepare":
        prepare()
    elif args.action == "build":
        build(args.version, args.only)
    else:
        globals()[args.action](args.version)
