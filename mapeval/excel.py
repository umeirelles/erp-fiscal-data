"""Aritmética de letras de coluna do Excel.

O repositório importa o get_column_letter do openpyxl, mas nunca a função
inversa, e os gabaritos de-para registram a coluna de origem como texto livre
("Coluna FY"); por isso a inversa precisa existir aqui.
"""

from __future__ import annotations

import re

_COLUNA = re.compile(r"coluna\s*([A-Za-z]+)", re.IGNORECASE)


def column_letter_to_index(letters: str) -> int:
    """Devolve o índice da coluna (a partir de 1) de uma letra de coluna do Excel.

    >>> column_letter_to_index("A"), column_letter_to_index("Z")
    (1, 26)
    >>> column_letter_to_index("AA"), column_letter_to_index("FY")
    (27, 181)
    """
    text = letters.strip()
    if not text or not text.isalpha():
        raise ValueError(f"não é uma letra de coluna do Excel: {letters!r}")
    index = 0
    for char in text.upper():
        index = index * 26 + (ord(char) - ord("A") + 1)
    return index


def index_to_column_letter(index: int) -> str:
    """Inversa de :func:`column_letter_to_index`."""
    if index < 1:
        raise ValueError(f"o índice da coluna deve ser >= 1; recebido {index}")
    letters = ""
    while index:
        index, remainder = divmod(index - 1, 26)
        letters = chr(ord("A") + remainder) + letters
    return letters


def parse_coluna_annotation(cell: object) -> int | None:
    """Extrai o índice da coluna (a partir de 1) de uma anotação da linha 1 do de-para.

    Devolve None quando a célula não traz anotação. Tolera o espaço no fim
    que existe em de_para_datasul.xlsx ("Coluna BV ") e ignora notas de
    texto livre que só citam uma coluna de passagem.
    """
    if cell is None:
        return None
    match = _COLUNA.search(str(cell))
    if match is None:
        return None
    return column_letter_to_index(match.group(1))
