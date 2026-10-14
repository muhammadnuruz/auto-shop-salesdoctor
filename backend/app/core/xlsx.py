"""Helpers for writing user/SD-controlled text into spreadsheets."""

import re
from typing import Any

# characters openpyxl refuses (IllegalCharacterError): C0 controls except \t \n \r
_ILLEGAL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")
_FORMULA_START = ("=", "+", "-", "@", "\t", "\r")


def cell(value: Any) -> Any:
    """Neutralise formula injection and drop characters a workbook cannot store."""
    if not isinstance(value, str):
        return value
    value = _ILLEGAL.sub("", value)
    if value[:1] in _FORMULA_START:
        return "'" + value
    return value
