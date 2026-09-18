"""Inspect worksheet coordinates before openpyxl expands sparse rows into cell arrays."""

import re
from typing import BinaryIO

from defusedxml import ElementTree

from bank_project.contracts.errors import IntakeError, LimitExceeded
from bank_project.contracts.schema import IntakeLimits

NAMESPACE = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"


def worksheet_numbers(xml: BinaryIO, limits: IntakeLimits) -> dict[tuple[int, int], str]:
    numbers = {}
    row, column = 0, 0
    in_row = False
    for event, node in ElementTree.iterparse(xml, events=("start", "end")):
        if node.tag == f"{NAMESPACE}row":
            if event == "start":
                raw_row = node.attrib.get("r", str(row + 1))
                if not re.fullmatch(r"[1-9][0-9]{0,6}", raw_row):
                    raise IntakeError("Excel 行坐标无效")
                next_row = int(raw_row)
                if next_row > limits.rows + 1:
                    raise LimitExceeded("Excel 行数超限，请拆分批次")
                if in_row or next_row <= row:
                    raise IntakeError("Excel 行坐标重复或顺序无效")
                row, column, in_row = next_row, 0, True
            else:
                in_row = False
                node.clear()
        elif node.tag == f"{NAMESPACE}c":
            if event == "start":
                if not in_row:
                    raise IntakeError("Excel 单元格不在数据行中")
                coordinate = node.attrib.get("r")
                next_column = column + 1
                if coordinate is not None:
                    match = re.fullmatch(r"([A-Z]{1,3})([1-9][0-9]{0,6})", coordinate)
                    if not match or int(match[2]) != row:
                        raise IntakeError("Excel 单元格坐标与所在行不一致")
                    next_column = 0
                    for letter in match[1]:
                        next_column = next_column * 26 + ord(letter) - ord("A") + 1
                if next_column > limits.columns:
                    raise LimitExceeded("Excel 列数超限")
                if next_column <= column:
                    raise IntakeError("Excel 单元格坐标重复或顺序无效")
                column = next_column
            else:
                value = node.find(f"{NAMESPACE}v")
                if (
                    node.attrib.get("t", "n") == "n"
                    and value is not None
                    and value.text is not None
                ):
                    numbers[row, column] = value.text
                node.clear()
    return numbers
