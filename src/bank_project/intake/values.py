"""Lossless scalar representation shared by source adapters."""

from datetime import date, datetime, time, timedelta
from decimal import Decimal

from bank_project.intake.models import IntakeError, Limits


def cell_text(value: object, limits: Limits, location: str) -> str | None:
    if value is None:
        return None
    if isinstance(value, bytes):
        value = "0x" + value.hex()
    elif isinstance(value, (datetime, date, time)):
        value = value.isoformat()
    elif isinstance(value, bool):
        value = "true" if value else "false"
    elif isinstance(value, (float, Decimal)):
        if not Decimal(str(value)).is_finite():
            raise IntakeError("数值必须为有限值", location=location)
    elif isinstance(value, timedelta):
        value = str(value)
    text = str(value)
    if len(text) > limits.max_cell_chars:
        raise IntakeError(f"单元格超过 {limits.max_cell_chars:,} 字符上限", location=location)
    return text
