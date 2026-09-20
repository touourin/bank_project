"""Stream files into a source-neutral sink. No complete file or table in memory."""

import codecs
import csv
from pathlib import Path

from bank_project.intake.files import TableReader
from bank_project.intake.models import IntakeError
from bank_project.intake.xlsx_stream import workbook_rows


def csv_encoding(path, requested):
    for encoding in ("utf-8-sig", "gb18030") if requested == "auto" else (requested,):
        decoder = codecs.getincrementaldecoder(encoding)(errors="strict")
        try:
            with path.open("rb") as file:
                while chunk := file.read(1024 * 1024):
                    if "\x00" in decoder.decode(chunk):
                        raise IntakeError("文件包含空字节，请将 CSV 另存为 UTF-8")
                decoder.decode(b"", final=True)
            return encoding
        except UnicodeDecodeError:
            continue
    raise IntakeError("无法按所选编码读取 CSV，请选择正确编码或另存为 UTF-8")


def parse_into(
    path,
    filename,
    limits,
    sink,
    *,
    header_row=1,
    encoding="auto",
    delimiter="auto",
    skip_description_sheets=True,
):
    path = Path(path)
    if not path.stat().st_size:
        raise IntakeError("文件为空")
    if path.stat().st_size > limits.max_upload_bytes:
        raise IntakeError("文件超过上传大小上限", status=413)
    if Path(filename).suffix.lower() == ".csv":
        encoding = csv_encoding(path, encoding)
        with path.open(encoding=encoding, newline="") as file:
            sample = file.read(65536)
            file.seek(0)
            if delimiter == "auto":
                try:
                    separator = csv.Sniffer().sniff(sample, delimiters=",;\t").delimiter
                except csv.Error:
                    separator = ","
            else:
                separator = {"comma": ",", "tab": "\t", "semicolon": ";"}[delimiter]
            csv.field_size_limit(limits.max_cell_chars)
            rows = csv.reader(file, delimiter=separator, strict=True)
            reader, cells = None, 0
            try:
                for number, values in enumerate(rows, 1):
                    if number < header_row:
                        continue
                    if reader is None:
                        reader = TableReader(Path(filename).stem, values, limits, sink.append)
                        sink.start(reader.name, reader.columns)
                    elif values:
                        cells += len(values)
                        if cells > limits.max_cells:
                            raise IntakeError("文件超过单批次单元格上限，未保存此批次")
                        reader.add(number, values)
            except csv.Error as exc:
                raise IntakeError(
                    "CSV 格式错误或单元格过长", location=f"记录 {rows.line_num}"
                ) from exc
            if reader is None:
                raise IntakeError("找不到指定的表头行")
            sink.finish(reader.finish().columns)
        return [f"CSV 使用 {encoding} 解码；保留原始文本。"]
    if Path(filename).suffix.lower() != ".xlsx":
        raise IntakeError("目前支持 .xlsx 和 .csv 文件")
    warnings, tables, cells = [], 0, 0
    for name, rows in workbook_rows(path, limits, header_row, skip_description_sheets, warnings):
        reader = None
        for number, values in rows:
            cells += len(values)
            if cells > limits.max_cells:
                raise IntakeError("工作簿超过单批次单元格上限，未保存此批次")
            if number < header_row:
                continue
            if reader is None:
                while values and values[-1] is None:
                    values.pop()
                if not values:
                    continue
                if number != header_row:
                    raise IntakeError("指定表头行为空，请调整表头行号", location=name)
                reader = TableReader(name, values, limits, sink.append)
                sink.start(name, reader.columns)
            elif any(value is not None for value in values):
                width = len(reader.columns)
                if any(value is not None for value in values[width:]):
                    raise IntakeError("数据超出表头列范围", location=f"{name} · 第 {number} 行")
                reader.add(number, values[:width] + [None] * max(0, width - len(values)))
        if reader:
            sink.finish(reader.finish().columns)
            tables += 1
        else:
            warnings.append(f"空白 Sheet「{name}」已跳过。")
    if not tables:
        raise IntakeError("工作簿没有可导入的表")
    warnings.append("Excel 按单元格底层值读取；编号请在原文件中存为文本，显示格式不作为数据。")
    return warnings
