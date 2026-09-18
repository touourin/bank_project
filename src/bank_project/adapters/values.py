"""Lossless JSON representations of source scalars, without business normalization."""

from datetime import date, datetime, time, timedelta
from decimal import Decimal

from bank_project.contracts.errors import IntakeError


def json_scalar(value):
    if isinstance(value, Decimal):
        if not value.is_finite():
            raise IntakeError("数据源包含非有限十进制数")
        return str(value)
    if isinstance(value, (datetime, date, time)):
        return value.isoformat()
    if isinstance(value, timedelta):
        # SQL TIME can be negative or exceed 24 hours. Do not wrap it into a clock time.
        micros = ((value.days * 86400 + value.seconds) * 1000000) + value.microseconds
        sign = "-" if micros < 0 else ""
        seconds, fraction = divmod(abs(micros), 1000000)
        tail = f".{fraction:06d}".rstrip("0") if fraction else ""
        return f"{sign}PT{seconds}{tail}S"
    raise IntakeError("数据源包含不支持的字段类型，请先显式转换为文本")
