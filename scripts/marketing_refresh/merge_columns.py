"""Merge five reviewed business-column aliases, preserving all source records.

This migration is explicit and one-shot; it never infers aliases from equal
sample values. Artifact Tool authors cell changes. OpenXML rebasing handles
column removal (not exposed by its public API) without rewriting other cells.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import re
import shutil
import subprocess
from decimal import Decimal
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZIP_STORED, ZipFile

from lxml import etree as E

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "data/column-merge-20260920"
NODE = "/Users/ourin/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin/node"
NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
PAIRS = {
    "AGECLEAN": ("int", "INT", "年龄"),
    "FOCUSNUMBER": ("int", "INT", "关注次数"),
    "EXECMONEY": ("decimal", "DECIMAL(26,8)", "执行标的（元）"),
    "REGCAP": ("decimal", "DECIMAL(26,8)", "注册资本（万元）"),
    "SUBCONAM": ("decimal", "DECIMAL(26,8)", "认缴出资额（万元）"),
}
INPUTS = {
    "desktop": Path.home() / "Desktop/五张表_修正版/工商.xlsx",
    "project": ROOT / "examples/mock/工商.xlsx",
}


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")


def sha(path):
    with path.open("rb") as f:
        return hashlib.file_digest(f, "sha256").hexdigest()


def letter(index):
    result = ""
    while index:
        index, digit = divmod(index - 1, 26)
        result = chr(65 + digit) + result
    return result


def number(address):
    result = 0
    for char in re.match(r"[A-Z]+", address)[0]:
        result = result * 26 + ord(char) - 64
    return result


def strings(archive):
    if "xl/sharedStrings.xml" not in archive.namelist():
        return []
    return ["".join(si.itertext()) for si in E.fromstring(archive.read("xl/sharedStrings.xml"))]


def value(cell, shared):
    if cell is None:
        return None
    if cell.find(NS + "f") is not None:
        raise ValueError("Unexpected formula in source data")
    if cell.get("t") == "inlineStr":
        return "".join(cell.itertext()) or None
    raw = cell.findtext(NS + "v")
    return shared[int(raw)] if cell.get("t") == "s" and raw is not None else raw


def merged_value(name, values):
    populated = [Decimal(str(v)) for v in values if v is not None and v != ""]
    if not populated:
        return None
    if any(not v.is_finite() for v in populated) or len(set(populated)) != 1:
        raise ValueError(f"Conflicting or invalid values for {name}")
    result = populated[0]
    quantum = Decimal(1) if PAIRS[name][0] == "int" else Decimal("0.00000001")
    if result != result.quantize(quantum):
        raise ValueError(f"Precision exceeds declared type: {name}")
    limit = Decimal(2**31) if PAIRS[name][0] == "int" else Decimal(10**18)
    if not -limit <= result < limit:
        raise ValueError(f"Value exceeds declared type: {name}")
    native = int(result) if PAIRS[name][0] == "int" else float(result)
    if Decimal(str(native)) != result:
        raise ValueError(f"Excel numeric conversion would lose precision: {name}")
    return native


def tree(archive, sheet):
    return E.fromstring(archive.read(f"xl/worksheets/sheet{sheet}.xml"))


def prepare(label):
    path = INPUTS[label]
    folder = OUT / label
    folder.mkdir(parents=True, exist_ok=True)
    backup = folder / "before.xlsx"
    if backup.exists() and sha(backup) != sha(path):
        raise ValueError("Input changed since this migration started")
    if not backup.exists():
        shutil.copy2(path, backup)
    with ZipFile(backup) as z:
        shared = strings(z)
        data, notes = tree(z, 1), tree(z, 2)
        rows = list(data.find(NS + "sheetData"))
        columns = [value(c, shared) for c in rows[0]]
        if len(columns) != 292 or len(set(columns)) != 292:
            raise ValueError("Unexpected input schema")
        positions = {n: i for i, n in enumerate(columns, 1)}
        patches = {"data": {}, "notes": {}}
        removed, numeric_styles, counts = [], {}, {}
        for name, (kind, _, _) in PAIRS.items():
            first, second = positions[name + "__varchar"], positions[name + "__" + kind]
            if second != first + 1:
                raise ValueError("Alias columns are no longer adjacent")
            removed.append(second)
            patches["data"][letter(first) + "1"] = name
            counts[name] = 0
            for row in rows[1:]:
                cells = {number(c.get("r")): c for c in row}
                a, b = cells.get(first), cells.get(second)
                merged = merged_value(name, [value(a, shared), value(b, shared)])
                if value(b, shared) is not None:
                    numeric_styles.setdefault(name, b.get("s", "0"))
                if merged is not None:
                    patches["data"][letter(first) + row.get("r")] = merged
                    counts[name] += 1
        for row in notes.find(NS + "sheetData"):
            cells = {re.match(r"[A-Z]+", c.get("r"))[0]: c for c in row}
            n = row.get("r")
            alias = value(cells.get("B"), shared)
            if alias and alias.split("__")[0] in PAIRS:
                name = alias.split("__")[0]
                patches["notes"].update(
                    {
                        "B" + n: name,
                        "D" + n: PAIRS[name][1],
                        "J"
                        + n: "同义字段统一数值类型；原始字段和类型保留在 E、F 列，按 source_table 追溯。",
                    }
                )
        for address, text in {
            "A3": "全部为合成测试数据。39 个来源表纵向合并，8,324 条明细，287 列。数据日期：2026-09-17。",
            "A7": "AGECLEAN、FOCUSNUMBER 统一为 INT；EXECMONEY、REGCAP、SUBCONAM 统一为 DECIMAL(26,8)，金额单位不变。保留来源字段说明。",
        }.items():
            patches["notes"][address] = text
        meta = {
            "input": str(path),
            "input_sha256": sha(path),
            "rows": len(rows) - 1,
            "columns_before": columns,
            "removed": removed,
            "numeric_styles": numeric_styles,
            "merged_nonblank_rows": counts,
        }
        save(folder / "patches.json", patches)
        save(folder / "metadata.json", meta)
    print(f"Prepared {label}: {meta['rows']} rows, 292 -> 287 columns; no conflicts", flush=True)


def rebase(address, removed):
    def one(match):
        index = number(match[1])
        return letter(index - sum(n <= index for n in removed)) + match[2]

    return re.sub(r"([A-Z]+)(\d+)", one, address)


def package(label):
    folder = OUT / label
    meta = json.loads((folder / "metadata.json").read_text())
    patches = json.loads((folder / "patches.json").read_text())
    removed = meta["removed"]
    with ZipFile(folder / "before.xlsx") as source, ZipFile(folder / "authored.xlsx") as authored:
        authored_strings = strings(authored)
        replacements = {}
        for index, name in [(1, "data"), (2, "notes")]:
            root = tree(source, index)
            lookup = {c.get("r"): c for c in tree(authored, index).iter(NS + "c")}
            for row in root.find(NS + "sheetData"):
                original = {c.get("r"): c for c in row}
                for address in patches[name]:
                    if int(re.search(r"\d+", address)[0]) != int(row.get("r")):
                        continue
                    cell = copy.deepcopy(lookup[address])
                    if cell.get("t") == "s":
                        cell.find(NS + "v").text = authored_strings[int(cell.findtext(NS + "v"))]
                        cell.set("t", "str")
                    old = original.get(address)
                    style = old.get("s", "0") if old is not None else "0"
                    if name == "data" and int(row.get("r")) > 1:
                        base = meta["columns_before"][number(address) - 1].split("__")[0]
                        style = meta["numeric_styles"][base]
                    cell.set("s", style)
                    if old is not None:
                        row.replace(old, cell)
                    else:
                        row.append(cell)
                if name == "data":
                    for c in list(row):
                        if number(c.get("r")) in removed:
                            row.remove(c)
                        else:
                            c.set("r", rebase(c.get("r"), removed))
                    row[:] = sorted(row, key=lambda c: number(c.get("r")))
                    if row.get("spans"):
                        row.set("spans", "1:287")
            if name == "data":
                for element in root.iter():
                    for attr in ("ref", "sqref", "activeCell", "topLeftCell"):
                        if element.get(attr):
                            element.set(attr, rebase(element.get(attr), removed))
                cols = root.find(NS + "cols")
                if cols is not None:
                    for col in list(cols):
                        lo, hi = int(col.get("min")), int(col.get("max"))
                        retained = [i for i in range(lo, hi + 1) if i not in removed]
                        if not retained:
                            cols.remove(col)
                        else:
                            col.set("min", str(retained[0] - sum(n < retained[0] for n in removed)))
                            col.set(
                                "max", str(retained[-1] - sum(n < retained[-1] for n in removed))
                            )
            replacements[f"xl/worksheets/sheet{index}.xml"] = E.tostring(
                root, xml_declaration=True, encoding="UTF-8"
            )
        root = E.fromstring(source.read("xl/tables/table1.xml"))
        cols = root.find(NS + "tableColumns")
        for i, col in enumerate(list(cols), 1):
            if i in removed:
                cols.remove(col)
            else:
                old_name = col.get("name")
                if old_name.split("__")[0] in PAIRS:
                    col.set("name", old_name.split("__")[0])
                col.set("id", str(i - sum(n < i for n in removed)))
        cols.set("count", "287")
        for item in root.iter():
            if item.get("ref"):
                item.set("ref", rebase(item.get("ref"), removed))
            if item.get("colId"):
                old = int(item.get("colId")) + 1
                if old in removed:
                    raise ValueError("Active filter on removed column requires explicit migration")
                item.set("colId", str(old - 1 - sum(n < old for n in removed)))
        replacements["xl/tables/table1.xml"] = E.tostring(
            root, xml_declaration=True, encoding="UTF-8"
        )
        with ZipFile(folder / "after.xlsx", "w", ZIP_DEFLATED) as target:
            for item in source.infolist():
                target.writestr(item, replacements.get(item.filename, source.read(item.filename)))
    verify(label)


def verify(label):
    folder = OUT / label
    meta = json.loads((folder / "metadata.json").read_text())
    patches = json.loads((folder / "patches.json").read_text())
    with ZipFile(folder / "before.xlsx") as before, ZipFile(folder / "after.xlsx") as after:
        sa = strings(after)
        old, new = tree(before, 1), tree(after, 1)
        old_rows, new_rows = list(old.find(NS + "sheetData")), list(new.find(NS + "sheetData"))
        assert len(old_rows) == len(new_rows) == 8325
        assert len(new_rows[0]) == 287
        for a, b in zip(old_rows, new_rows, strict=True):
            expected = {}
            for cell in a:
                address = cell.get("r")
                if number(address) not in meta["removed"]:
                    expected[rebase(address, meta["removed"])] = (address, cell)
            actual = {c.get("r"): c for c in b}
            for address in patches["data"]:
                if re.search(r"\d+", address)[0] == a.get("r"):
                    expected.setdefault(rebase(address, meta["removed"]), (address, None))
            assert expected.keys() == actual.keys(), a.get("r")
            for dest, (origin, cell) in expected.items():
                result = actual[dest]
                if origin in patches["data"]:
                    desired = patches["data"][origin]
                    got = value(result, sa)
                    if isinstance(desired, str):
                        assert got == desired
                    else:
                        assert result.get("t", "n") == "n"
                        assert Decimal(got) == Decimal(str(desired)), (origin, got, desired)
                else:
                    clone = copy.deepcopy(cell)
                    clone.set("r", dest)
                    assert E.tostring(clone) == E.tostring(result), origin
        old_notes = {c.get("r"): c for c in tree(before, 2).iter(NS + "c")}
        new_notes = {c.get("r"): c for c in tree(after, 2).iter(NS + "c")}
        assert old_notes.keys() == new_notes.keys()
        for address, original in old_notes.items():
            if address in patches["notes"]:
                assert value(new_notes[address], sa) == patches["notes"][address]
            else:
                assert E.tostring(original) == E.tostring(new_notes[address]), address
        changed = {"xl/worksheets/sheet1.xml", "xl/worksheets/sheet2.xml", "xl/tables/table1.xml"}
        for filename in before.namelist():
            if filename not in changed:
                assert before.read(filename) == after.read(filename), filename
        table = E.fromstring(after.read("xl/tables/table1.xml"))
        assert table.get("ref") == "A1:KA8325"
        headers = [value(c, sa) for c in new_rows[0]]
        assert [c.get("name") for c in table.find(NS + "tableColumns")] == headers
        for n in PAIRS:
            assert n in headers and not any(c.startswith(n + "__") for c in headers)
    save(
        folder / "verified.json",
        {
            "rows": 8324,
            "columns": 287,
            "sha256": sha(folder / "after.xlsx"),
            "unaffected_cells_exactly_preserved": True,
            "numeric_values_exact": True,
        },
    )
    print(
        f"Verified {label}: every data cell, field description and unchanged package part",
        flush=True,
    )


def build(label):
    prepare(label)
    folder = OUT / label
    subprocess.run(
        [
            NODE,
            str(Path(__file__).with_suffix(".mjs")),
            str(folder / "patches.json"),
            str(folder / "authored.xlsx"),
        ],
        check=True,
    )
    package(label)


def install():
    installed = {}
    for label, path in INPUTS.items():
        folder = OUT / label
        meta = json.loads((folder / "metadata.json").read_text())
        verified = json.loads((folder / "verified.json").read_text())
        if sha(path) not in {meta["input_sha256"], verified["sha256"]}:
            raise ValueError("Intervening local edits: " + str(path))
        assert sha(folder / "after.xlsx") == verified["sha256"]
        temp = path.with_suffix(".merging.xlsx")
        shutil.copy2(folder / "after.xlsx", temp)
        temp.replace(path)
        installed[label] = {"path": str(path), **verified}
    desktop = INPUTS["desktop"].parent
    archive = desktop.with_suffix(".zip")
    if archive.exists():
        backup = OUT / "desktop_archive_before.zip"
        if not backup.exists():
            shutil.copy2(archive, backup)
        temp = archive.with_suffix(".merging.zip")
        with ZipFile(archive) as old, ZipFile(temp, "w", ZIP_STORED) as new:
            for item in old.infolist():
                if Path(item.filename).name == "工商.xlsx":
                    new.write(INPUTS["desktop"], item.filename)
                else:
                    with old.open(item) as src, new.open(item, "w") as dst:
                        shutil.copyfileobj(src, dst, 1024 * 1024)
        temp.replace(archive)
    save(OUT / "installed.json", installed)
    print("Local workbooks and existing desktop archive installed; backups retained", flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["build", "verify", "install"])
    parser.add_argument("label", nargs="?", choices=list(INPUTS))
    args = parser.parse_args()
    if args.action == "install":
        install()
    else:
        globals()[args.action](args.label)
