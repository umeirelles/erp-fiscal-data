"""Carrega os gabaritos de-para escritos pelo cliente.

Só datasul e oracle têm gabarito de colunas: de_para_procosa.xlsx é uma
especificação no nível de registro, e sap_terumo não tem de-para.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

import openpyxl

from mapeval.excel import parse_coluna_annotation

# Correções do gabarito verificadas nos dados do cliente. Sem elas, a avaliação
# classificaria essas escolhas corretas como erros. --strict-key-only usa o
# gabarito original para permitir a comparação. A CLI mostra apenas os números
# das colunas corrigidas; as justificativas ficam neste registro.
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
    """Reúne as correspondências anotadas pelo cliente e as correções verificadas do gabarito."""

    client: str
    #: número da coluna do modelo (a partir de 1) -> número da coluna do cliente (a partir de 1)
    present: dict[int, int]
    #: número da coluna do modelo -> por que a anotação do próprio cliente foi substituída
    corrections: dict[int, str] = field(default_factory=dict)

    @property
    def source_conflicts(self) -> dict[int, list[int]]:
        """Lista origens atribuídas a mais de um destino; exige revisão do gabarito."""
        targets = defaultdict(list)
        for target, source in self.present.items():
            targets[source].append(target)
        return {source: sorted(columns) for source, columns in sorted(targets.items())
                if len(columns) > 1}

    @property
    def denominator(self) -> int:
        """Retorna o número de colunas anotadas no gabarito, usado como denominador do recall."""
        return len(self.present)


def load_answer_key(
    path: str | Path,
    client: str,
    *,
    apply_corrections: bool = True,
) -> AnswerKey:
    """Lê a primeira linha do de-para e associa os números das colunas do modelo aos do cliente."""
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
