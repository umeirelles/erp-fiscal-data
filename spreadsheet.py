"""Lê a estrutura de uma planilha e identifica suas colunas, inclusive cabeçalhos repetidos."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
import unicodedata

import pandas as pd


def sheet_argument(value: str) -> str | int:
    """Interpreta dígitos como índice de aba a partir de zero; preserva nomes."""
    return int(value) if value.isdigit() else value


def normalize_header(value: object) -> str:
    text = "" if value is None else str(value).strip()
    text = unicodedata.normalize("NFKD", text)
    text = text.encode("ascii", "ignore").decode("ascii").lower()
    return re.sub(r"[^a-z0-9]+", " ", text).strip()


def _header_text(value: object, index: int) -> str:
    # Fica em inglês: o nome vai para o embedding e para o payload da IA. Nas
    # células do relatório ele sai como "Coluna sem nome N" (report_schema).
    if pd.isna(value) or not str(value).strip():
        return f"Unnamed column {index + 1}"
    return str(value).strip()


@dataclass(frozen=True)
class ColumnRef:
    """Uma coluna de planilha: id estável, posição (a partir de 0), cabeçalho e ocorrência.

    ``name`` e ``display_name`` ficam em inglês em memória (``Unnamed column
    N``, ``(occurrence N)``) porque o nome é entrada da IA; a forma em
    português só é aplicada nas células gravadas, por ``report_schema``.
    """

    id: str
    index: int
    name: str
    occurrence: int

    @property
    def display_name(self) -> str:
        suffix = f" (occurrence {self.occurrence})" if self.occurrence > 1 else ""
        return f"{self.name}{suffix}"


@dataclass
class SheetTable:
    """Uma aba lida do Excel: as colunas (``ColumnRef``) e os dados abaixo do cabeçalho."""

    columns: list[ColumnRef]
    data: pd.DataFrame

    @classmethod
    def from_excel(
        cls,
        path: str | Path,
        sheet_name: str | int = 0,
        header_row: int = 0,
    ) -> "SheetTable":
        # dtype=object: cada célula fica como o leitor de Excel a produziu.
        raw = pd.read_excel(path, sheet_name=sheet_name, header=None, dtype=object)
        if raw.empty:
            raise ValueError(f"A planilha não tem linhas: {path}")
        if header_row < 0 or header_row >= len(raw):
            raise ValueError(
                f"A linha de cabeçalho {header_row + 1} está fora das linhas da planilha: {path}"
            )

        columns = cls.from_headers(raw.iloc[header_row]).columns

        data = raw.iloc[header_row + 1 :].reset_index(drop=True)
        data.columns = range(data.shape[1])
        return cls(columns=columns, data=data)

    @classmethod
    def from_headers(cls, headers) -> "SheetTable":
        """Identifica as colunas sem carregar as linhas de dados."""
        headers = [
            _header_text(value, i) for i, value in enumerate(headers)
        ]
        occurrences: dict[str, int] = {}
        columns: list[ColumnRef] = []
        for index, name in enumerate(headers):
            normalized = normalize_header(name)
            occurrences[normalized] = occurrences.get(normalized, 0) + 1
            columns.append(
                ColumnRef(
                    id=f"column_{index + 1:03d}",
                    index=index,
                    name=name,
                    occurrence=occurrences[normalized],
                )
            )

        return cls(columns=columns, data=pd.DataFrame())

    def series(self, column: ColumnRef) -> pd.Series:
        return self.data.iloc[:, column.index]


def positive_column_number(value: object, label: str) -> int:
    """Valida um índice de coluna a partir de 1, sem truncar valores fracionários."""
    try:
        number = float(value)
        if isinstance(value, bool) or not number.is_integer() or number < 1:
            raise ValueError
        return int(number)
    except (TypeError, ValueError, OverflowError):
        raise ValueError(f"{label} deve ser um inteiro positivo; recebido {value!r}") from None
