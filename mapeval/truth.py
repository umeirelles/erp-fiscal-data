"""Carrega os gabaritos de-para escritos pelo cliente.

Só datasul e oracle têm gabarito de colunas: de_para_procosa.xlsx é uma
especificação no nível de registro, e sap_terumo não tem de-para.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import openpyxl

from mapeval.excel import parse_coluna_annotation

# Linhas em que o próprio gabarito está errado e o mapeador está certo, cada
# uma verificada nos dados do cliente. Sem elas a avaliação puniria o
# comportamento correto; --strict-key-only pontua contra o gabarito cru e
# mostra que só estas linhas separam os dois números. As justificativas nunca
# são impressas (a CLI mostra só os números das colunas).
KEY_CORRECTIONS: dict[str, dict[int, tuple[int, str]]] = {
    "oracle": {
        213: (36, "o gabarito aponta c37 ALIQ COFINS; c36 ALIQ PIS é a alíquota do PIS"),
        220: (40, "o gabarito aponta c39 CST-PIS; o par está trocado no gabarito"),
        221: (39, "o gabarito aponta c40 CST-COFINS; o par está trocado no gabarito"),
    },
    "datasul": {
        184: (188, "o gabarito aponta c99 (bloco A), mas é incoerente consigo mesmo; "
                   "c188 fecha o trio fiscal com as colunas m52-m55 do próprio gabarito"),
    },
}


@dataclass(frozen=True)
class AnswerKey:
    """As colunas que um cliente de fato tem, segundo a anotação do próprio cliente."""

    client: str
    #: número da coluna do modelo (a partir de 1) -> número da coluna do cliente (a partir de 1)
    present: dict[int, int]
    #: número da coluna do modelo -> por que a anotação do próprio cliente foi substituída
    corrections: dict[int, str] = field(default_factory=dict)

    @property
    def denominator(self) -> int:
        """O único denominador legítimo do recall. Nunca 227."""
        return len(self.present)


def load_answer_key(
    path: str | Path,
    client: str,
    *,
    apply_corrections: bool = True,
) -> AnswerKey:
    """Lê a linha 1 de uma planilha de-para num mapa coluna do modelo -> coluna do cliente."""
    workbook = openpyxl.load_workbook(path, read_only=True, data_only=True)
    try:
        sheet = workbook[workbook.sheetnames[0]]
        header_row = next(sheet.iter_rows(min_row=1, max_row=1, values_only=True))
    finally:
        workbook.close()

    present: dict[int, int] = {}
    for model_column, cell in enumerate(header_row, start=1):
        client_column = parse_coluna_annotation(cell)
        if client_column is not None:
            present[model_column] = client_column

    corrections: dict[int, str] = {}
    if apply_corrections:
        for model_column, (correct_column, why) in KEY_CORRECTIONS.get(client, {}).items():
            if model_column in present:
                present[model_column] = correct_column
                corrections[model_column] = why

    return AnswerKey(client=client, present=present, corrections=corrections)
