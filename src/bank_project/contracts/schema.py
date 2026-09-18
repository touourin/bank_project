"""Shared resource limits for source parsers."""

from dataclasses import dataclass


@dataclass(frozen=True)
class IntakeLimits:
    file_bytes: int = 20 * 1024 * 1024
    expanded_bytes: int = 80 * 1024 * 1024
    rows: int = 50000
    columns: int = 500
    text_characters: int = 200000
    pdf_pages: int = 200
