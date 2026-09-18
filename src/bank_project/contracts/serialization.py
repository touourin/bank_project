"""Strict JSON, including bounded iteration over top-level record arrays."""

import json

from bank_project.contracts.errors import IntakeError, LimitExceeded


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise IntakeError("JSON 包含重复字段")
        result[key] = value
    return result


def _constant(value):
    raise IntakeError("JSON 不允许 NaN 或 Infinity")


def _checked(result):
    pending = [(result, 0)]
    while pending:
        value, depth = pending.pop()
        if depth > 64:
            raise IntakeError("JSON 嵌套超过 64 层")
        if isinstance(value, str) and any(0xD800 <= ord(c) <= 0xDFFF for c in value):
            raise IntakeError("JSON 包含无效 Unicode 字符")
        if isinstance(value, dict):
            pending.extend((child, depth + 1) for child in [*value.keys(), *value.values()])
        elif isinstance(value, list):
            pending.extend((child, depth + 1) for child in value)
    return result


def _decoder():
    # Decimal JSON numbers remain strings, never rounded binary floating-point values.
    return json.JSONDecoder(object_pairs_hook=_pairs, parse_float=str, parse_constant=_constant)


def strict_json(text: str):
    try:
        return _checked(_decoder().decode(text))
    except (ValueError, RecursionError) as exc:
        raise IntakeError("JSON 格式无效或嵌套过深") from exc


def json_records(text: str, maximum: int):
    """Stop at the row limit before decoding or allocating the remainder of an array."""
    text = text.strip()
    if not text.startswith("["):
        yield strict_json(text)
        return
    decoder = _decoder()
    position, count = 1, 0
    while True:
        while position < len(text) and text[position] in " \t\r\n":
            position += 1
        if position < len(text) and text[position] == "]" and count == 0:
            if text[position + 1 :].strip():
                raise IntakeError("JSON 数组后存在多余内容")
            return
        if count >= maximum:
            raise LimitExceeded(f"记录数超过 {maximum}，请拆分批次")
        try:
            value, position = decoder.raw_decode(text, position)
        except (ValueError, RecursionError) as exc:
            raise IntakeError("JSON 记录格式无效") from exc
        count += 1
        yield _checked(value)
        while position < len(text) and text[position] in " \t\r\n":
            position += 1
        if position < len(text) and text[position] == "]":
            if text[position + 1 :].strip():
                raise IntakeError("JSON 数组后存在多余内容")
            return
        if position >= len(text) or text[position] != ",":
            raise IntakeError("JSON 数组缺少逗号或结束符")
        position += 1
