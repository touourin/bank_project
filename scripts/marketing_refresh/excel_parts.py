"""Preserve workbook packaging/styles while assembling Artifact Tool chunks.

Cell values are authored/exported by Artifact Tool; this module only rebases
those cells into the existing template and preserves its other worksheets.
This bounded-memory packaging step is needed for 750k x 103-cell workbooks.
"""

import argparse
import hashlib
import re
from xml.etree import ElementTree as E
from zipfile import ZIP_DEFLATED, ZipFile

from .common import DESKTOP, OUTPUT, TABLES, dump, load

NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
E.register_namespace("x", NS[1:-1])


def letter(n):
    s = ""
    while n:
        n, i = divmod(n - 1, 26)
        s = chr(65 + i) + s
    return s


def metadata():
    result = {}
    for label, table in TABLES.items():
        with ZipFile(DESKTOP / (label + ".xlsx")) as z:
            styles = E.fromstring(z.read("xl/styles.xml"))
            formats = {
                14: "mm-dd-yy",
                15: "d-mmm-yy",
                16: "d-mmm",
                17: "mmm-yy",
                18: "h:mm AM/PM",
                19: "h:mm:ss AM/PM",
                20: "h:mm",
                21: "h:mm:ss",
                22: "m/d/yy h:mm",
            }
            for e in styles.findall(NS + "numFmts/" + NS + "numFmt"):
                formats[int(e.get("numFmtId"))] = e.get("formatCode")
            style_formats = {
                i: formats.get(int(e.get("numFmtId", "0")), "General")
                for i, e in enumerate(styles.find(NS + "cellXfs"))
            }
            cell_styles = {}
            cell_types = {}
            headers = []
            captured = []
            with z.open("xl/worksheets/sheet1.xml") as f:
                for _, e in E.iterparse(f, events=("end",)):
                    if e.tag != NS + "row":
                        continue
                    n = int(e.get("r"))
                    if n == 1:
                        headers = [
                            c.findtext(NS + "v", "")
                            or "".join(t.text or "" for t in c.iter(NS + "t"))
                            for c in e
                        ]
                    else:
                        for c in e:
                            col = re.match(r"[A-Z]+", c.get("r"))[0]
                            raw = c.findtext(NS + "v") or "".join(
                                t.text or "" for t in c.iter(NS + "t")
                            )
                            if not raw or col in cell_types:
                                continue
                            cell_styles[col] = c.get("s", "0")
                            fmt = style_formats.get(int(c.get("s", "0")), "General")
                            if c.get("t") in {"str", "s", "inlineStr"}:
                                kind = "text"
                            elif re.search(r"[yd]", fmt, re.I):
                                kind = "datetime" if "h" in fmt.lower() else "date"
                            else:
                                kind = "number"
                            cell_types[col] = kind
                    if n <= 20:
                        captured.append(E.tostring(e, encoding="utf-8"))
                    e.clear()
                    if n >= 20 and (label == "交易流水" or len(cell_types) == len(headers)):
                        break
            # Include null-only columns using the original schema's styles;
            # fallback text prevents any identifier being converted to a number.
            result[label] = {
                "table": table,
                "columns": headers,
                "styles": cell_styles,
                "types": cell_types,
                "rows": load(OUTPUT / "manifest.json")["tables"][label]["rows"],
            }
            # A cropped original workbook is a QA-only artifact. All original
            # values and styles in its first 20 rows are retained verbatim.
            preview_dir = OUTPUT / "original_previews"
            preview_dir.mkdir(exist_ok=True)
            with z.open("xl/worksheets/sheet1.xml") as f:
                head = f.read(400000)
            prefix = head[: head.index(b"<x:sheetData>") + len(b"<x:sheetData>")]
            prefix = re.sub(
                rb'<x:dimension ref="[^"]+"',
                f'<x:dimension ref="A1:{letter(len(headers))}20"'.encode(),
                prefix,
            )
            with ZipFile(preview_dir / (label + ".xlsx"), "w", ZIP_DEFLATED) as dest:
                for name in z.namelist():
                    if name == "xl/worksheets/sheet1.xml":
                        dest.writestr(
                            name, prefix + b"".join(captured) + b"</x:sheetData></x:worksheet>"
                        )
                    elif name == "xl/tables/table1.xml":
                        dest.writestr(
                            name,
                            re.sub(
                                rb'ref="[A-Z]+1:[A-Z]+\d+"',
                                f'ref="A1:{letter(len(headers))}20"'.encode(),
                                z.read(name),
                            ),
                        )
                    else:
                        dest.writestr(name, z.read(name))
    dump(OUTPUT / "excel_metadata.json", result)


def assemble(label):
    info = load(OUTPUT / "excel_metadata.json")[label]
    chunks = sorted((OUTPUT / "chunks" / info["table"]).glob("*.xlsx"))
    expected = info["rows"]
    if not chunks:
        raise ValueError("No authored chunks")
    target = OUTPUT / "workbooks" / (label + ".xlsx")
    target.parent.mkdir(exist_ok=True)
    with (
        ZipFile(DESKTOP / (label + ".xlsx")) as source,
        ZipFile(target, "w", ZIP_DEFLATED, compresslevel=3, allowZip64=True) as dest,
    ):
        with source.open("xl/worksheets/sheet1.xml") as f:
            head = f.read(400000)
        prefix = head[: head.index(b"<x:sheetData>") + len(b"<x:sheetData>")]
        prefix = re.sub(
            rb'<x:dimension ref="[^"]+"',
            f'<x:dimension ref="A1:{letter(len(info["columns"]))}{expected + 1}"'.encode(),
            prefix,
        )
        header = head[head.index(b"<x:row ") : head.index(b"</x:row>") + len(b"</x:row>")]
        with source.open("xl/worksheets/sheet1.xml") as f:
            f.seek(max(0, source.getinfo("xl/worksheets/sheet1.xml").file_size - 65536))
            tail = f.read()
        footer = tail[tail.rfind(b"</x:sheetData>") :]
        if not footer.startswith(b"</x:sheetData>"):
            raise ValueError("Missing template footer")
        count = 0
        with dest.open("xl/worksheets/sheet1.xml", "w", force_zip64=True) as out:
            out.write(prefix)
            out.write(header)
            for part in chunks:
                with ZipFile(part) as z:
                    strings = []
                    if "xl/sharedStrings.xml" in z.namelist():
                        root = E.fromstring(z.read("xl/sharedStrings.xml"))
                        strings = ["".join(t.text or "" for t in si.iter(NS + "t")) for si in root]
                    with z.open("xl/worksheets/sheet1.xml") as stream:
                        for _, row in E.iterparse(stream, events=("end",)):
                            if row.tag != NS + "row":
                                continue
                            count += 1
                            row.set("r", str(count + 1))
                            for cell in row:
                                col = re.match(r"[A-Z]+", cell.get("r"))[0]
                                cell.set("r", col + str(count + 1))
                                cell.set("s", info["styles"].get(col, "0"))
                                if cell.get("t") == "s":
                                    v = cell.find(NS + "v")
                                    v.text = strings[int(v.text)]
                                    cell.set("t", "str")
                            out.write(E.tostring(row, encoding="utf-8"))
                            row.clear()
                if count % 50000 == 0:
                    print(f"Assembled {label}: {count:,}", flush=True)
            out.write(footer)
        if count != expected:
            raise ValueError(f"{label}: {count} vs {expected}")
        for name in source.namelist():
            if name == "xl/worksheets/sheet1.xml":
                continue
            content = source.read(name)
            if name.endswith(".xml"):
                content = content.replace(b"MOCK_", b"")
            if name == "xl/tables/table1.xml":
                content = re.sub(
                    rb'ref="[A-Z]+1:[A-Z]+\d+"',
                    f'ref="A1:{letter(len(info["columns"]))}{expected + 1}"'.encode(),
                    content,
                )
            dest.writestr(name, content)
    with target.open("rb") as f:
        digest = hashlib.file_digest(f, "sha256").hexdigest()
    dump(
        target.with_suffix(".check.json"),
        {"rows": count, "sha256": digest, "artifact_tool_authored": True},
    )
    print("Workbook ready", label, count, flush=True)


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("action", choices=["metadata", "assemble"])
    p.add_argument("label", nargs="?")
    a = p.parse_args()
    metadata() if a.action == "metadata" else assemble(a.label)
