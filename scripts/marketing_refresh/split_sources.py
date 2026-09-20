"""Restore source-table worksheets from the reviewed wide mock workbooks.

The actual workbook is the input, not an earlier generator snapshot. All values
and nulls are checked after export. Artifact Tool owns spreadsheet authoring.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import subprocess
from pathlib import Path
from zipfile import ZIP_STORED, ZipFile

import openpyxl
from lxml import etree as E

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "data/source-table-split-20260920"
NODE = "/Users/ourin/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin/node"
N = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
BASES = {"desktop": Path.home() / "Desktop/五张表_修正版", "project": ROOT / "examples/mock"}
DOMAINS = {"征信": "credit", "工商": "business"}


def read_json(path):
    return json.loads(path.read_text())


def write_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n")


def digest(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def col_index(address):
    result = 0
    for char in re.match(r"[A-Z]+", address)[0]:
        result = result * 26 + ord(char) - 64
    return result - 1


def shared_strings(archive):
    if "xl/sharedStrings.xml" not in archive.namelist():
        return []
    return ["".join(el.itertext()) for el in E.fromstring(archive.read("xl/sharedStrings.xml"))]


def cell_value(cell, strings):
    if cell.find(N + "f") is not None:
        raise ValueError("Unexpected source formula")
    kind = cell.get("t", "n")
    if kind == "inlineStr":
        return "".join(cell.itertext()) or None
    raw = cell.findtext(N + "v")
    if raw is None or raw == "":
        return None
    if kind == "s":
        return strings[int(raw)]
    if kind == "b":
        return bool(int(raw))
    if kind != "n":
        return raw
    # Reproduce Excel's existing numeric value. Identifiers stay strings.
    return float(raw)


def rows(archive, path, strings):
    with archive.open(path) as stream:
        for _, row in E.iterparse(stream, events=("end",), tag=N + "row"):
            yield (
                int(row.get("r")),
                {
                    col_index(cell.get("r")): (cell_value(cell, strings), int(cell.get("s", "0")))
                    for cell in row
                },
            )
            row.clear()
            while row.getprevious() is not None:
                del row.getparent()[0]


def formats(archive):
    root = E.fromstring(archive.read("xl/styles.xml"))
    fmts = dict(openpyxl.styles.numbers.BUILTIN_FORMATS)
    for item in root.findall(N + "numFmts/" + N + "numFmt"):
        fmts[int(item.get("numFmtId"))] = item.get("formatCode")
    return [
        fmts.get(int(item.get("numFmtId", "0")), "General") for item in root.find(N + "cellXfs")
    ]


def source_descriptions(schema, domain):
    source = next(s for s in schema["sources"] if s["domain"] == domain)
    path = Path(source["path"])
    if digest(path) != source["sha256"]:
        raise ValueError("Original dictionary changed")
    workbook = openpyxl.load_workbook(path, read_only=True, data_only=True)
    try:
        return {
            str(row[1]).strip(): str(row[2] or "").strip()
            for row in workbook["table_list"].iter_rows(min_row=2, values_only=True)
            if len(row) >= 3 and row[1]
        }
    finally:
        workbook.close()


def prepare(version, label):
    folder = OUT / version / label
    folder.mkdir(parents=True, exist_ok=True)
    path = BASES[version] / (label + ".xlsx")
    before = folder / "before.xlsx"
    if before.exists() and digest(before) != digest(path):
        raise ValueError("Source changed since preparation")
    if not before.exists():
        shutil.copy2(path, before)
    schema = read_json(ROOT / "data/mock-sources/schema.json")
    source_tables = [t for t in schema["tables"] if t["domain"] == DOMAINS[label]]
    descriptions = source_descriptions(schema, DOMAINS[label])
    tables = {}
    for spec in source_tables:
        columns = ["source_row", *[f["name"] for f in spec["columns"]]]
        if len(set(columns)) != len(columns) or len(spec["sheet"]) > 31:
            raise ValueError("Ambiguous source schema")
        tables[spec["sheet"]] = {
            "name": spec["sheet"],
            "description": descriptions.get(spec["sheet"], ""),
            "columns": columns,
            "formats": {},
            "rows": [],
            "keys": spec["mock_primary_key"],
        }
    with ZipFile(before) as archive:
        strings = shared_strings(archive)
        styles = formats(archive)
        data = rows(archive, "xl/worksheets/sheet1.xml", strings)
        _, header = next(data)
        headers = [header[i][0] for i in range(len(header))]
        if headers[:2] != ["source_table", "source_row"]:
            raise ValueError("Expected unsplit workbook")
        positions = {name: i for i, name in enumerate(headers)}
        seen, count = set(), 0
        for _, record in data:
            source = record[positions["source_table"]][0]
            table = tables[source]
            source_row = record[positions["source_row"]][0]
            key = (source, source_row)
            if key in seen:
                raise ValueError("Duplicate source row identifier")
            seen.add(key)
            allowed = {"source_table", *table["columns"]}
            unexpected = [
                headers[i]
                for i, (value, _) in record.items()
                if value is not None and headers[i] not in allowed
            ]
            if unexpected:
                raise ValueError(f"Nonempty fields outside source schema: {source}: {unexpected}")
            values = []
            for column in table["columns"]:
                value, style = record.get(positions[column], (None, 0))
                values.append(value)
                if value is not None:
                    table["formats"].setdefault(column, styles[style])
            table["rows"].append(values)
            count += 1
        dictionary = []
        for n, record in rows(archive, "xl/worksheets/sheet2.xml", strings):
            if n < 9:
                continue
            values = [record.get(i, (None, 0))[0] for i in range(10)]
            if values[1] == "source_table":
                continue
            if values[1] == "source_row":
                values[9] = "Sheet 名与 source_row 共同定位拆分前的一条原始明细。"
            dictionary.append(values)
        dictionary[0][1], dictionary[0][3] = "字段名", "当前字段类型"
    if len(tables) != (98 if label == "征信" else 39):
        raise ValueError("Unexpected source table count")
    for table in tables.values():
        if not table["rows"]:
            raise ValueError("Unexpected empty source table")
        for column in table["columns"]:
            table["formats"].setdefault(column, "@")
    source_file = next(x for x in schema["sources"] if x["domain"] == DOMAINS[label])["path"]
    payload = {
        "label": label,
        "version": version,
        "input": str(path),
        "input_sha256": digest(before),
        "source_dictionary": Path(source_file).name,
        "row_count": count,
        "previous_columns": len(headers),
        "tables": list(tables.values()),
        "dictionary": dictionary,
    }
    write_json(folder / "input.json", payload)
    print(f"Prepared {version}/{label}: {len(tables)} source tables, {count:,} rows", flush=True)


def verify(version, label):
    folder = OUT / version / label
    data = read_json(folder / "input.json")
    path = folder / "after.xlsx"
    with ZipFile(path) as archive:
        strings = shared_strings(archive)
        styles = formats(archive)
        book = E.fromstring(archive.read("xl/workbook.xml"))
        names = [s.get("name") for s in book.find(N + "sheets")]
        expected = ["目录说明", *[t["name"] for t in data["tables"]], "数据说明"]
        if names != expected:
            raise ValueError("Unexpected workbook sheets/order")
        total = 0
        for index, table in enumerate(data["tables"], 2):
            actual = list(rows(archive, f"xl/worksheets/sheet{index}.xml", strings))
            if len(actual) != len(table["rows"]) + 1:
                raise ValueError("Row count changed: " + table["name"])
            wanted = [table["columns"], *table["rows"]]
            for row_number, ((_, found), values) in enumerate(zip(actual, wanted, strict=True), 1):
                received = [found.get(i, (None, 0))[0] for i in range(len(values))]
                if received != values:
                    raise ValueError(f"Cell value changed: {table['name']} row {row_number}")
                for i, v in enumerate(values):
                    if v is None or row_number == 1:
                        continue
                    if isinstance(v, str) != isinstance(received[i], str):
                        raise ValueError("Text identifier type changed")
                    desired_format = table["formats"][table["columns"][i]]
                    if styles[found[i][1]] != desired_format:
                        raise ValueError(f"Number/date format changed: {table['name']} {i}")
            total += len(table["rows"])
        dictionary = dict(rows(archive, f"xl/worksheets/sheet{len(names)}.xml", strings))
        for i, values in enumerate(data["dictionary"], 9):
            actual = [dictionary[i].get(j, (None, 0))[0] for j in range(10)]
            if actual != values:
                raise ValueError("Field definition changed")
        if total != data["row_count"]:
            raise ValueError("Total rows changed")
        table_files = [n for n in archive.namelist() if re.fullmatch(r"xl/tables/table\d+\.xml", n)]
        if len(table_files) != len(data["tables"]) + 2:
            raise ValueError("Missing native Excel tables")
        schema_count = {
            t["name"]: {"rows": len(t["rows"]), "business_columns": len(t["columns"]) - 1}
            for t in data["tables"]
        }
    result = {
        "input": data["input"],
        "input_sha256": data["input_sha256"],
        "sha256": digest(path),
        "rows": total,
        "data_sheets": len(data["tables"]),
        "tables": schema_count,
        "all_cell_values_and_types_verified": True,
        "field_definitions_verified": True,
    }
    write_json(folder / "verified.json", result)
    print(
        f"Verified {version}/{label}: {total:,} rows, all values/types/formats/definitions",
        flush=True,
    )


def build(version, label):
    folder = OUT / version / label
    subprocess.run(
        [
            NODE,
            str(Path(__file__).with_suffix(".mjs")),
            str(folder / "input.json"),
            str(folder / "after.xlsx"),
        ],
        check=True,
    )
    verify(version, label)


def install():
    results = {}
    # Validate all four outputs and inputs before changing any delivery file.
    for version in BASES:
        for label in DOMAINS:
            folder = OUT / version / label
            result = read_json(folder / "verified.json")
            if digest(Path(result["input"])) not in {result["input_sha256"], result["sha256"]}:
                raise ValueError("Input was edited during restructuring")
            if digest(folder / "after.xlsx") != result["sha256"]:
                raise ValueError("Output changed after verification")
            results[f"{version}/{label}"] = result
    for version in BASES:
        for label in DOMAINS:
            destination = BASES[version] / (label + ".xlsx")
            temp = destination.with_suffix(".splitting.xlsx")
            shutil.copy2(OUT / version / label / "after.xlsx", temp)
            temp.replace(destination)
    archive_path = BASES["desktop"].with_suffix(".zip")
    if archive_path.exists():
        backup = OUT / "desktop_archive_before.zip"
        if not backup.exists():
            shutil.copy2(archive_path, backup)
        temporary = archive_path.with_suffix(".splitting.zip")
        with ZipFile(archive_path) as original, ZipFile(temporary, "w", ZIP_STORED) as output:
            for info in original.infolist():
                if Path(info.filename).name in {label + ".xlsx" for label in DOMAINS}:
                    output.write(BASES["desktop"] / Path(info.filename).name, info.filename)
                else:
                    with original.open(info) as src, output.open(info, "w") as dest:
                        shutil.copyfileobj(src, dest, 1024 * 1024)
        temporary.replace(archive_path)
    write_json(OUT / "installed.json", results)
    print(
        "Installed four source-table workbooks and refreshed existing desktop archive", flush=True
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["prepare", "build", "verify", "install"])
    parser.add_argument("version", choices=list(BASES), nargs="?")
    parser.add_argument("label", choices=list(DOMAINS), nargs="?")
    args = parser.parse_args()
    if args.action == "install":
        install()
    else:
        globals()[args.action](args.version, args.label)
