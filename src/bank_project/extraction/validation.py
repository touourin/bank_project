import re
from datetime import datetime
from decimal import Decimal, InvalidOperation

from bank_project.contracts.errors import IntakeError
from bank_project.contracts.serialization import strict_json


def bank_date(value: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"\d{8}", value, re.ASCII):
        raise IntakeError("日期必须为有效的 YYYYMMDD")
    try:
        return datetime.strptime(value, "%Y%m%d").date().isoformat()
    except ValueError as exc:
        raise IntakeError("日期不是有效日历日期") from exc


def decimal_string(value, precision: int = 26, scale: int = 8) -> str:
    if isinstance(value, (bool, float)) or not isinstance(value, (str, int)):
        raise IntakeError("金额等十进制字段需使用文本或整数，不能使用已损失精度的浮点值")
    if len(str(value)) > 100:
        raise IntakeError("十进制字段长度超限")
    try:
        number = Decimal(value)
        if not number.is_finite() or (
            not number.is_zero() and number.adjusted() >= precision - scale
        ):
            raise IntakeError("十进制数值超出字段范围")
        if number.as_tuple().exponent < -scale:
            raise IntakeError("十进制小数位超出字段精度，不会自动四舍五入")
        return "0" if number.is_zero() else format(number, "f")
    except InvalidOperation as exc:
        raise IntakeError("十进制字段格式无效") from exc


def column_value(value, column: dict):
    if value is None or value == "":
        if column.get("source_nullable") is False:
            raise IntakeError("已明确非空的字段为空")
        return None
    kind = column["sql_type"].lower()
    if column.get("format") == "json_object":
        parsed = strict_json(value) if isinstance(value, str) else value
        if not isinstance(parsed, dict):
            raise IntakeError("字段必须为 JSON 对象")
        return parsed
    if column.get("format") == "YYYYMMDD":
        return bank_date(str(value))
    if kind.startswith("decimal"):
        precision, scale = map(int, re.fullmatch(r"decimal\((\d+),(\d+)\)", kind).groups())
        return decimal_string(value, precision, scale)
    if kind == "int":
        if isinstance(value, bool) or not re.fullmatch(r"-?\d+", str(value), re.ASCII):
            raise IntakeError("整数字段格式无效")
        integer = int(value)
        if not -(2**31) <= integer < 2**31:
            raise IntakeError("整数超出 SQL INT 范围")
        return integer
    if kind.startswith("varchar"):
        if not isinstance(value, str):
            raise IntakeError("字符字段必须保留为文本，尤其客户号和账号不能转成数字")
        length = int(re.fullmatch(r"varchar\((\d+)\)", kind).group(1))
        if len(value) > length:
            raise IntakeError("字符字段长度超限")
        if any(ord(char) < 32 and char not in "\t\n\r" for char in value):
            raise IntakeError("字符字段包含控制字符")
        return value
    if kind in {"string", "text"} and isinstance(value, str):
        return value
    raise IntakeError("字段类型不匹配")


def event_properties(properties: dict, specification: dict) -> dict:
    result = dict(properties)
    for name, definition in specification.items():
        value = properties.get(name)
        if value is None or value == "":
            if definition.get("required"):
                raise IntakeError(f"事件属性 {name} 不能为空")
            continue
        try:
            if definition.get("format") == "YYYYMMDD":
                result[name] = bank_date(value)
            elif definition["type"] == "number":
                result[name] = decimal_string(value)
            elif definition["type"] == "string" and not isinstance(value, str):
                raise IntakeError("属性需要文本类型")
        except IntakeError as exc:
            raise IntakeError(f"事件属性 {name}: {exc}") from exc
    return result
