"""Excel column-letter arithmetic.

The repo imports openpyxl's get_column_letter but never its inverse, and the
de-para keys record their source columns as free text ("Coluna FY"), so the
inverse has to exist here.
"""

from __future__ import annotations

import re

_COLUNA = re.compile(r"coluna\s*([A-Za-z]+)", re.IGNORECASE)


def column_letter_to_index(letters: str) -> int:
    """Return the 1-based column index for an Excel column letter.

    >>> column_letter_to_index("A"), column_letter_to_index("Z")
    (1, 26)
    >>> column_letter_to_index("AA"), column_letter_to_index("FY")
    (27, 181)
    """
    text = letters.strip()
    if not text or not text.isalpha():
        raise ValueError(f"not an Excel column letter: {letters!r}")
    index = 0
    for char in text.upper():
        index = index * 26 + (ord(char) - ord("A") + 1)
    return index


def index_to_column_letter(index: int) -> str:
    """Inverse of :func:`column_letter_to_index`."""
    if index < 1:
        raise ValueError(f"column index must be >= 1, got {index}")
    letters = ""
    while index:
        index, remainder = divmod(index - 1, 26)
        letters = chr(ord("A") + remainder) + letters
    return letters


def parse_coluna_annotation(cell: object) -> int | None:
    """Extract the 1-based column index from a de-para row-1 annotation.

    Returns None when the cell carries no annotation. Tolerates the trailing
    whitespace present in de_para_datasul.xlsx ("Coluna BV ") and ignores
    free-text notes that merely mention a column in passing.
    """
    if cell is None:
        return None
    match = _COLUNA.search(str(cell))
    if match is None:
        return None
    return column_letter_to_index(match.group(1))
