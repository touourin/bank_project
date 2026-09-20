"""Read actual XLSX XML rows; shared strings spill to a temporary SQLite index."""

import posixpath
import re
import sqlite3
from contextlib import contextmanager
from functools import lru_cache
from tempfile import TemporaryDirectory
from xml.etree.ElementTree import ParseError
from zipfile import BadZipFile, ZipFile

from defusedxml.common import DefusedXmlException
from defusedxml.ElementTree import fromstring, iterparse
from openpyxl.styles.numbers import BUILTIN_FORMATS, is_date_format
from openpyxl.utils.cell import column_index_from_string
from openpyxl.utils.datetime import CALENDAR_MAC_1904, CALENDAR_WINDOWS_1900, from_excel

from .models import IntakeError

NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
REL = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id"


def metadata(archive, name):
    if archive.getinfo(name).file_size > 4 * 1024 * 1024:
        raise IntakeError("工作簿元数据过大，请简化格式后导入")
    return fromstring(archive.read(name))


@contextmanager
def shared_strings(archive, limits):
    if "xl/sharedStrings.xml" not in archive.namelist():

        def missing(index):
            raise IntakeError("工作簿引用了缺失的共享字符串表")

        yield missing
        return
    with TemporaryDirectory(prefix="bank-xlsx-") as directory:
        db = sqlite3.connect(directory + "/strings.sqlite3")
        try:
            db.execute("PRAGMA cache_size=-2048")
            db.execute("CREATE TABLE strings(id INTEGER PRIMARY KEY,value TEXT NOT NULL)")
            index = 0
            with archive.open("xl/sharedStrings.xml") as file:
                iterator = iterparse(file, events=("start", "end"))
                _, root = next(iterator)
                for event, element in iterator:
                    if event == "end" and element.tag == NS + "si":
                        value = "".join(t.text or "" for t in element.iter(NS + "t"))
                        if len(value) > limits.max_cell_chars:
                            raise IntakeError("共享字符串超过单元格长度上限")
                        db.execute("INSERT INTO strings VALUES(?,?)", (index, value))
                        index += 1
                        if index > limits.max_cells:
                            raise IntakeError("共享字符串数量超过上限")
                        if index % 1000 == 0:
                            db.commit()
                        root.clear()
            db.commit()

            @lru_cache(maxsize=256)
            def lookup(index):
                row = db.execute("SELECT value FROM strings WHERE id=?", (index,)).fetchone()
                if row is None:
                    raise IntakeError("工作簿引用了不存在的共享字符串")
                return row[0]

            yield lookup
        finally:
            db.close()


def cell_value(cell, strings, date_styles, epoch, limits):
    if cell.find(NS + "f") is not None or cell.get("t") == "e":
        raise IntakeError("发现公式或错误单元格，请先粘贴为值后导入", location=cell.get("r", ""))
    kind = cell.get("t", "n")
    raw = cell.findtext(NS + "v")
    if kind == "inlineStr":
        value = "".join(t.text or "" for t in cell.iter(NS + "t"))
    elif raw is None:
        return None
    elif kind == "s":
        value = strings(int(raw))
    elif kind == "b":
        value = "true" if raw == "1" else "false"
    elif kind == "n" and int(cell.get("s", "0")) in date_styles:
        value = from_excel(float(raw), epoch)
    else:
        value = raw
    if isinstance(value, str) and len(value) > limits.max_cell_chars:
        raise IntakeError("单元格超过长度上限", location=cell.get("r", ""))
    return value


def sheet_rows(archive, path, strings, date_styles, epoch, limits, header_row):
    with archive.open(path) as stream:
        parent, previous = None, 0
        for event, element in iterparse(stream, events=("start", "end")):
            if event == "start" and element.tag == NS + "sheetData":
                parent = element
            if event != "end" or element.tag != NS + "row":
                continue
            number = int(element.get("r", str(previous + 1)))
            if number <= previous or number > limits.max_rows + header_row:
                raise IntakeError("Sheet 行号超出读取上限或顺序错误")
            previous = number
            values, last_column = [], 0
            for cell in element:
                if cell.tag != NS + "c":
                    continue
                match = re.fullmatch(r"([A-Z]+)([0-9]+)", cell.get("r", ""))
                column = column_index_from_string(match[1]) if match else last_column + 1
                if (
                    column <= last_column
                    or column > limits.max_columns
                    or (match and int(match[2]) != number)
                ):
                    raise IntakeError("Sheet 单元格坐标错误或超过列数上限")
                values.extend([None] * (column - len(values) - 1))
                values.append(cell_value(cell, strings, date_styles, epoch, limits))
                last_column = column
            yield number, values
            element.clear()
            if parent is not None:
                parent.remove(element)


def workbook_rows(path, limits, header_row, skip_descriptions, warnings):
    try:
        with ZipFile(path) as archive:
            entries = archive.infolist()
            if (
                len(entries) > 10000
                or sum(e.file_size for e in entries) > limits.max_expanded_bytes
            ):
                raise IntakeError("工作簿解压后过大，请拆分文件")
            book = metadata(archive, "xl/workbook.xml")
            relations = metadata(archive, "xl/_rels/workbook.xml.rels")
            targets = {}
            for rel in relations:
                if rel.get("TargetMode") == "External":
                    continue
                target = rel.get("Target", "")
                target = posixpath.normpath(
                    target.lstrip("/") if target.startswith("/") else "xl/" + target
                )
                if not target.startswith("xl/"):
                    raise IntakeError("工作簿包含无效内部路径")
                targets[rel.get("Id")] = target
            styles = set()
            if "xl/styles.xml" in archive.namelist():
                style = metadata(archive, "xl/styles.xml")
                formats = dict(BUILTIN_FORMATS)
                for item in style.findall(NS + "numFmts/" + NS + "numFmt"):
                    formats[int(item.get("numFmtId"))] = item.get("formatCode", "")
                for i, item in enumerate(style.findall(NS + "cellXfs/" + NS + "xf")):
                    if is_date_format(formats.get(int(item.get("numFmtId", "0")), "")):
                        styles.add(i)
            properties = book.find(NS + "workbookPr")
            epoch = (
                CALENDAR_MAC_1904
                if properties is not None and properties.get("date1904") in {"1", "true"}
                else CALENDAR_WINDOWS_1900
            )
            sheets = []
            for sheet in book.findall(NS + "sheets/" + NS + "sheet"):
                name = sheet.get("name", "")
                if skip_descriptions and (name.endswith("说明") or name.casefold() == "readme"):
                    warnings.append(
                        f"说明页「{name}」已跳过；如需作为数据读取，可关闭“跳过说明页”。"
                    )
                else:
                    sheets.append((name, targets[sheet.get(REL)]))
            if len(sheets) > limits.max_tables:
                raise IntakeError("工作簿超过 Sheet 数量上限")
            with shared_strings(archive, limits) as strings:
                for name, target in sheets:
                    yield (
                        name,
                        sheet_rows(archive, target, strings, styles, epoch, limits, header_row),
                    )
    except (
        BadZipFile,
        ParseError,
        DefusedXmlException,
        KeyError,
        ValueError,
        OSError,
        EOFError,
    ) as exc:
        raise IntakeError("无法读取 XLSX，请确认文件未损坏且没有密码保护") from exc
