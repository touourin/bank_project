"""Read every exported Excel cell, compare with prepared data, and stage import.

Uses the XLSX public file format for streaming extraction only; authoring is
performed by Artifact Tool. Credentials are not involved.
"""

import argparse
import hashlib
import itertools
import re
import shutil
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZIP_STORED, ZipFile

from lxml import etree as E

from .common import DESKTOP, OUTPUT, TABLES, RowsWriter, dump, load, rows
from .excel_parts import letter

NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"


def records(path, meta):
    columns = meta["columns"]
    by_letter = {letter(i + 1): k for i, k in enumerate(columns)}
    with ZipFile(path) as z:
        shared = []
        if "xl/sharedStrings.xml" in z.namelist():
            shared = ["".join(si.itertext()) for si in E.fromstring(z.read("xl/sharedStrings.xml"))]
        with z.open("xl/worksheets/sheet1.xml") as f:
            for _, el in E.iterparse(f, events=("end",), tag=NS + "row"):
                row = {}
                for c in el:
                    col = re.match(r"[A-Z]+", c.get("r"))[0]
                    if c.find(NS + "f") is not None:
                        raise ValueError("Unexpected formula in source data")
                    value = c.findtext(NS + "v")
                    typ = c.get("t", "n")
                    if typ == "inlineStr":
                        value = "".join(c.itertext())
                    elif typ == "s":
                        value = shared[int(value)]
                    if value is None or value == "":
                        continue
                    if el.get("r") != "1" and typ == "n":
                        kind = meta["types"].get(col, "text")
                        if kind in {"date", "datetime"}:
                            d = datetime(1899, 12, 30) + timedelta(
                                seconds=round(float(value) * 86400)
                            )
                            value = d.strftime(
                                "%Y-%m-%d" if kind == "date" else "%Y-%m-%d %H:%M:%S"
                            )
                        else:
                            value = format(Decimal(value), "f")
                    row[by_letter[col]] = value
                if el.get("r") == "1":
                    if list(row.values()) != columns:
                        raise ValueError("Workbook headers changed")
                else:
                    yield row
                el.clear()
                while el.getprevious() is not None:
                    del el.getparent()[0]


def verify(label):
    meta = load(OUTPUT / "excel_metadata.json")[label]
    table = TABLES[label]
    path = OUTPUT / "workbooks" / (label + ".xlsx")
    outdir = OUTPUT / "import_rows"
    outdir.mkdir(exist_ok=True)
    writer = RowsWriter(outdir / (table + ".jsonl.gz"), meta["columns"])
    expected = rows(OUTPUT / (table + ".jsonl.gz"))
    by_name = {k: meta["types"].get(letter(i + 1), "text") for i, k in enumerate(meta["columns"])}
    for n, (actual, wanted) in enumerate(itertools.zip_longest(records(path, meta), expected), 1):
        if actual is None or wanted is None:
            raise ValueError(f"{label}: row count mismatch")
        if actual.keys() != wanted.keys():
            raise ValueError(
                f"{label} row {n} null/column mismatch {actual.keys() ^ wanted.keys()}"
            )
        for key, value in wanted.items():
            other = actual[key]
            if by_name[key] == "number":
                if abs(Decimal(value) - Decimal(other)) > Decimal(".00000001"):
                    raise ValueError(
                        f"{label} row {n} {key}: numeric precision lost: {value} / {other}"
                    )
                # Canonical precision agrees with the source contract; Excel
                # double serialization noise is rounded to the intended scale.
                if "." in value:
                    actual[key] = format(
                        Decimal(other).quantize(Decimal("1").scaleb(-len(value.split(".")[1]))), "f"
                    )
                else:
                    actual[key] = str(int(Decimal(other)))
            elif value != other:
                raise ValueError(f"{label} row {n} {key}: text/date mismatch")
        writer.add(actual)
        if n % 100000 == 0:
            print(f"Excel readback {label}: {n:,}", flush=True)
    result = writer.close()
    with path.open("rb") as f:
        result["xlsx_sha256"] = hashlib.file_digest(f, "sha256").hexdigest()
    dump(outdir / (table + ".verified.json"), result)
    print("Excel readback passed", label, result["rows"], flush=True)


def preview(label):
    meta = load(OUTPUT / "excel_metadata.json")[label]
    folder = OUTPUT / "final_previews"
    folder.mkdir(exist_ok=True)
    path = OUTPUT / "workbooks" / (label + ".xlsx")
    with ZipFile(path) as z, ZipFile(folder / (label + ".xlsx"), "w", ZIP_DEFLATED) as out:
        with z.open("xl/worksheets/sheet1.xml") as f:
            head = f.read(400000)
        prefix = head[: head.index(b"<x:sheetData>") + len(b"<x:sheetData>")]
        prefix = re.sub(
            rb'<x:dimension ref="[^"]+"',
            f'<x:dimension ref="A1:{letter(len(meta["columns"]))}20"'.encode(),
            prefix,
        )
        captured = []
        with z.open("xl/worksheets/sheet1.xml") as f:
            for _, e in E.iterparse(f, events=("end",), tag=NS + "row"):
                captured.append(E.tostring(e))
                if int(e.get("r")) >= 20:
                    break
        for name in z.namelist():
            if name == "xl/worksheets/sheet1.xml":
                out.writestr(name, prefix + b"".join(captured) + b"</x:sheetData></x:worksheet>")
            elif name == "xl/tables/table1.xml":
                out.writestr(
                    name,
                    re.sub(
                        rb'ref="[A-Z]+1:[A-Z]+\d+"',
                        f'ref="A1:{letter(len(meta["columns"]))}20"'.encode(),
                        z.read(name),
                    ),
                )
            else:
                out.writestr(name, z.read(name))
    # Artifact Tool's importer can coerce numeric-looking text while rendering.
    # Reassert original text values in the QA-only model; the workbook itself
    # already stores these identifiers as strings and passes full readback.
    text_cells = {}
    for n, r in enumerate(itertools.islice(records(path, meta), 19), 2):
        for i, key in enumerate(meta["columns"], 1):
            col = letter(i)
            if key in r and meta["types"].get(col, "text") == "text":
                text_cells[col + str(n)] = r[key]
    dump(folder / (label + ".text.json"), text_cells)


def install():
    backup = load(OUTPUT / "backup.json")
    installed = {}
    for label, table in TABLES.items():
        verified = load(OUTPUT / "import_rows" / (table + ".verified.json"))
        path = OUTPUT / "workbooks" / (label + ".xlsx")
        with path.open("rb") as f:
            actual = hashlib.file_digest(f, "sha256").hexdigest()
        if actual != verified["xlsx_sha256"]:
            raise ValueError("Workbook changed after validation")
        # Refuse to overwrite any intervening manual edits of the original.
        current = DESKTOP / (label + ".xlsx")
        with current.open("rb") as f:
            current_hash = hashlib.file_digest(f, "sha256").hexdigest()
        old = load(OUTPUT / "sources.json")[label]["sha256"]
        if current_hash not in {old, actual}:
            raise ValueError("Desktop file changed during preparation: " + str(current))
        temp = current.with_name("." + current.name + ".refresh")
        shutil.copy2(path, temp)
        temp.replace(current)
        installed[label] = {"path": str(current), "sha256": actual, "rows": verified["rows"]}
    archive = DESKTOP.with_suffix(".zip")
    saved = Path(backup["directory"]) / archive.name
    if archive.exists():
        if not saved.exists():
            shutil.copy2(archive, saved)
        with archive.open("rb") as f:
            current_archive_hash = hashlib.file_digest(f, "sha256").hexdigest()
        with saved.open("rb") as f:
            old_archive_hash = hashlib.file_digest(f, "sha256").hexdigest()
        prior = (
            load(OUTPUT / "desktop_archive.json")["sha256"]
            if (OUTPUT / "desktop_archive.json").exists()
            else None
        )
        if current_archive_hash not in {old_archive_hash, prior}:
            raise ValueError("Desktop archive was edited during preparation")
    temp = archive.with_suffix(".refresh.zip")
    # XLSX members are already compressed. Store them without recompressing.
    with ZipFile(temp, "w", ZIP_STORED, allowZip64=True) as z:
        for label in TABLES:
            z.write(DESKTOP / (label + ".xlsx"), label + ".xlsx")
    with ZipFile(temp) as z:
        if set(z.namelist()) != {label + ".xlsx" for label in TABLES}:
            raise ValueError("Unexpected archive members")
        for label in TABLES:
            with z.open(label + ".xlsx") as f:
                digest = hashlib.file_digest(f, "sha256").hexdigest()
            if digest != installed[label]["sha256"]:
                raise ValueError("Archive differs from installed workbook")
    temp.replace(archive)
    with archive.open("rb") as f:
        sha = hashlib.file_digest(f, "sha256").hexdigest()
    dump(
        OUTPUT / "desktop_archive.json",
        {"path": str(archive), "sha256": sha, "workbooks": installed},
    )
    dump(OUTPUT / "desktop_installed.json", installed)
    print("Desktop originals updated after backup:", backup["directory"], flush=True)


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("action", choices=["verify", "preview", "install"])
    p.add_argument("label", nargs="?")
    a = p.parse_args()
    if a.action == "verify":
        verify(a.label)
    elif a.action == "preview":
        preview(a.label)
    else:
        install()
