"""Refresh dictionaries using Artifact Tool-authored cell patches."""

import argparse
import copy
import hashlib
import re
import shutil
import subprocess
from collections import Counter, defaultdict
from zipfile import ZIP_DEFLATED, ZipFile

from lxml import etree as E

from .build_workbooks import BUILDER, NODE
from .common import DESKTOP, OUTPUT, TABLES, dump, load, rows

NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"


def prepare():
    folder = OUTPUT / "notes"
    folder.mkdir(exist_ok=True)
    manifest = load(OUTPUT / "manifest.json")
    for label, table in TABLES.items():
        count = 0
        present = defaultdict(Counter)
        totals = Counter()
        events = Counter()
        first = None
        for r in rows(OUTPUT / (table + ".jsonl.gz")):
            first = first or r
            group = r.get("source_table", "all")
            present[group].update(r.keys())
            totals[group] += 1
            count += 1
            if label == "客户旅程":
                events[r["EVT_TYPE"]] += 1
        patches = {}
        with ZipFile(DESKTOP / (label + ".xlsx")) as source:
            for name in source.namelist():
                if not re.fullmatch(r"xl/worksheets/sheet[2-9].xml", name):
                    continue
                sheet = name.rsplit("/", 1)[1].removesuffix(".xml")
                root = E.fromstring(source.read(name))
                patch = {}
                for row in root.find(NS + "sheetData"):
                    cells = {re.match(r"[A-Z]+", c.get("r"))[0]: "".join(c.itertext()) for c in row}
                    for cell in row:
                        text = "".join(cell.itertext())
                        if "MOCK_" in text or "mock" in text:
                            patch[cell.get("r")] = text.replace("MOCK_", "").replace("mock", "本批")
                    n = row.get("r")
                    if sheet == "sheet2":
                        if (
                            label in {"征信", "工商"}
                            and cells.get("A") in totals
                            and cells.get("B") in manifest["tables"][label]["columns"]
                        ):
                            patch["H" + n] = totals[cells["A"]] - present[cells["A"]][cells["B"]]
                        elif (
                            label == "交易流水"
                            and cells.get("B") in manifest["tables"][label]["columns"]
                        ):
                            patch["H" + n] = count - present["all"][cells["B"]]
                        elif (
                            label == "客户旅程"
                            and cells.get("B", "").replace("MOCK_", "") in events
                            and int(n) >= 21
                        ):
                            patch["H" + n] = events[cells["B"].replace("MOCK_", "")]
                        elif label == "客户标签" and cells.get("B") in first:
                            patch["G" + n] = first[cells["B"]]
                if sheet == "sheet2":
                    if label == "客户旅程":
                        patch["A3"] = (
                            f"8 个字段，{count:,} 条合成事件，{len(events)} 个已生成事件码。CUST_ID 对应客户标签 cust_ind。数据日期 2026-09-17。"
                        )
                        patch["A5"] = (
                            "全部为合成测试数据。FX_SIGN、LC_SIGN、CREDIT_SIGN 为本包测试码；PROPERTIES 为合法 JSON。定期存款使用独立子账户号。"
                        )
                    elif label == "交易流水":
                        patch["A3"] = f"103 个字段，{count:,} 条合成记录。数据日期 2026-09-17。"
                        codes = "测试码：SETTLE_IN=销售货款收款；SETTLE_OUT=采购货款支付；PAYROLL=代发工资；LOAN_DRAW=贷款发放；LOAN_REPAY=贷款还款；REV_ORIGINAL=转账支出；REVERSAL=转账冲正；OPEN_DEPOSIT=开户存入；TERM_DEPOSIT=定期存款存入；TERM_TRANSFER=本企业活期转定期支出"
                        for address in ["I8", "I31", "I44", "I46", "I95"]:
                            patch[address] = codes
                        patch["I71"] = (
                            "测试码：TRF_IN=销售货款收款；TRF_OUT=转账支出；PAYROLL=代发工资；LN_DRAW=贷款发放；LN_REPAY=贷款还款；REVERSAL=转账冲正；OPEN_DEP=开户存入；TERM_IN=定期存款存入；TERM_OUT=本企业活期转定期支出"
                        )
                    elif label in {"征信", "工商"}:
                        patch["H9"] = "本批空值数"
                patches[sheet] = patch
        input_path = folder / (label + ".json")
        dump(input_path, patches)
        output = folder / (label + ".xlsx")
        run = subprocess.run(
            [NODE, str(BUILDER), "patch", str(input_path), str(output)],
            capture_output=True,
            text=True,
            timeout=120,
        )
        if run.returncode:
            raise RuntimeError(run.stderr[-2500:])
        print("Dictionary patches authored", label, flush=True)


def apply(label):
    path = OUTPUT / "workbooks" / (label + ".xlsx")
    temp = path.with_suffix(".updated.xlsx")
    patches = load(OUTPUT / "notes" / (label + ".json"))
    with (
        ZipFile(OUTPUT / "notes" / (label + ".xlsx")) as authored,
        ZipFile(path) as source,
        ZipFile(temp, "w", ZIP_DEFLATED, compresslevel=1) as target,
    ):
        shared = []
        if "xl/sharedStrings.xml" in authored.namelist():
            shared = [
                "".join(si.itertext()) for si in E.fromstring(authored.read("xl/sharedStrings.xml"))
            ]
        replacements = {}
        for i, (sheet, cells) in enumerate(patches.items(), 1):
            name = "xl/worksheets/" + sheet + ".xml"
            root = E.fromstring(source.read(name))
            existing = {c.get("r"): c for c in root.iter(NS + "c")}
            authored_cells = {
                c.get("r"): c
                for c in E.fromstring(authored.read(f"xl/worksheets/sheet{i}.xml")).iter(NS + "c")
            }
            for address, value in cells.items():
                old = existing[address]
                new = copy.deepcopy(authored_cells[address])
                if old.get("s") is not None:
                    new.set("s", old.get("s"))
                else:
                    new.attrib.pop("s", None)
                if new.get("t") == "s":
                    new.find(NS + "v").text = shared[int(new.findtext(NS + "v"))]
                    new.set("t", "str")
                text = "".join(new.itertext())
                if text != str(value):
                    raise ValueError("Authored note differs: " + address)
                old.getparent().replace(old, new)
            replacements[name] = E.tostring(root, xml_declaration=True, encoding="utf-8")
        for name in source.namelist():
            if name in replacements:
                target.writestr(name, replacements[name])
            else:
                with source.open(name) as f, target.open(name, "w", force_zip64=True) as out:
                    shutil.copyfileobj(f, out, 1024 * 1024)
    # The data XML is copied byte for byte; prior full cell verification remains
    # valid. Verify its CRC and size, then refresh the whole-file evidence hash.
    with ZipFile(path) as old, ZipFile(temp) as new:
        a = old.getinfo("xl/worksheets/sheet1.xml")
        b = new.getinfo("xl/worksheets/sheet1.xml")
        if (a.CRC, a.file_size) != (b.CRC, b.file_size):
            raise ValueError("Data changed while refreshing notes")
    temp.replace(path)
    with path.open("rb") as f:
        digest = hashlib.file_digest(f, "sha256").hexdigest()
    check = load(path.with_suffix(".check.json"))
    check["sha256"] = digest
    check["notes_refreshed"] = True
    dump(path.with_suffix(".check.json"), check)
    marker = OUTPUT / "import_rows" / (TABLES[label] + ".verified.json")
    if marker.exists():
        info = load(marker)
        info["xlsx_sha256"] = digest
        info["notes_refreshed_data_unchanged"] = True
        dump(marker, info)
    dump(
        OUTPUT / "notes" / (label + ".applied.json"), {"sha256": digest, "data_xml_unchanged": True}
    )
    print("Workbook dictionary refreshed", label, flush=True)


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("action", choices=["prepare", "apply"])
    p.add_argument("label", nargs="?")
    a = p.parse_args()
    if a.action == "prepare":
        prepare()
    else:
        apply(a.label)
