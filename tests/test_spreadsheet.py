"""Testes de ``mapper/spreadsheet.py``: leitura das planilhas e identidade das colunas."""

from __future__ import annotations

import pandas as pd

from mapper.spreadsheet import SheetTable


def test_duplicate_headers_keep_unique_column_ids(tmp_path) -> None:
    path = tmp_path / "duplicates.xlsx"
    pd.DataFrame([["Document Number", "Document Number"], [1, 2]]).to_excel(
        path, header=False, index=False
    )

    sheet = SheetTable.from_excel(path)

    # Em memória o sufixo de ocorrência fica em inglês: o nome de exibição é
    # comparado pelo apply e só vira "(ocorrência 2)" nas células gravadas.
    assert [column.id for column in sheet.columns] == ["column_001", "column_002"]
    assert sheet.columns[0].display_name == "Document Number"
    assert sheet.columns[1].display_name == "Document Number (occurrence 2)"


def test_sheet_table_supports_an_explicit_header_row(tmp_path) -> None:
    path = tmp_path / "preamble.xlsx"
    pd.DataFrame(
        [["Invoice export", None], ["CFOP", "Amount"], ["5102", 100.0]]
    ).to_excel(path, header=False, index=False)

    parsed = SheetTable.from_excel(path, header_row=1)

    assert [column.name for column in parsed.columns] == ["CFOP", "Amount"]
    assert parsed.data.to_dict("records") == [{0: "5102", 1: 100}]
