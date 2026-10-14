"""Fakes e construtores de tabelas compartilhados pelos testes.

Não contém testes: os arquivos ``test_*.py`` importam daqui o que usam.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from mapper import schema
from mapper.resolver import DataMappingDecision
from mapper.spreadsheet import ColumnRef, SheetTable


def table(headers: list[str], rows: list[list[object]]) -> SheetTable:
    """Monta uma SheetTable em memória, contando as ocorrências de cabeçalhos repetidos."""
    occurrences: dict[str, int] = {}
    columns: list[ColumnRef] = []
    for index, name in enumerate(headers):
        occurrences[name] = occurrences.get(name, 0) + 1
        columns.append(
            ColumnRef(
                id=f"column_{index + 1:03d}",
                index=index,
                name=name,
                occurrence=occurrences[name],
            )
        )
    return SheetTable(columns=columns, data=pd.DataFrame(rows))


@dataclass
class FixedScorer:
    """Fake scorer que retorna uma matriz fixa de scores de cabeçalho."""

    matrix: np.ndarray

    def scores(self, model_headers, client_headers) -> np.ndarray:
        return self.matrix


class FixedResolver:
    """Fake resolver que retorna decisões predefinidas e registra as chamadas recebidas."""

    def __init__(self, decisions: dict[str, DataMappingDecision]) -> None:
        self.decisions = decisions
        self.calls: list[str] = []

    def resolve(
        self,
        model_column,
        model_values,
        candidates,
        sample_size,
        relationship_evidence=None,
        catalog_evidence=None,
    ):
        self.calls.append(model_column.name)
        return self.decisions[model_column.name]


def table_from_headers(headers, rows):
    """Monta uma SheetTable identificando as colunas como a leitura do Excel faz."""
    result = SheetTable.from_headers(headers)
    result.data = pd.DataFrame(rows)
    return result


class Scores:
    """Fake scorer com uma linha de scores por cabeçalho do modelo."""

    def __init__(self, rows):
        self.rows = rows

    def scores(self, model_headers, client_headers):
        return np.array([self.rows[name] for name in model_headers])


class FailingResolver:
    """Fake resolver para cenários que não podem consultar a API."""

    def resolve(self, **kwargs):
        raise AssertionError("Este cenário deve ser resolvido sem consultar a API")


def report_files(tmp_path, rows):
    """Grava uma exportação de três colunas e um relatório com as linhas informadas."""
    client = tmp_path / "client.xlsx"
    report = tmp_path / "report.xlsx"
    pd.DataFrame([["Source A", "Source B", "Source C"], [11, 22, 33]]).to_excel(
        client, header=False, index=False,
    )
    defaults = dict(client_column_number=1, client_column="Source A", status="review",
                    final_confidence=.85, name_confidence=.85, name_runner_up_confidence=.4,
                    name_margin=.45, data_confidence=.8, method="data", reason="Proposta",
                    reviewer_status="", correct_client_column="", reviewer_note="")
    frame = pd.DataFrame([{**defaults, **row} for row in rows])
    schema.to_external_frame(frame, "mappings").to_excel(
        report, sheet_name="Mapeamentos", index=False,
    )
    return client, report
