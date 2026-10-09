"""Pontua um relatório de mapeamento contra um gabarito.

O relatório está no esquema em português. A leitura passa por
``report_schema``, que devolve os cabeçalhos com os nomes internos; a
situação e o método voltam ao vocabulário interno (``accepted``,
``catalog``...) antes de qualquer comparação.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

import openpyxl

import report_schema
from mapeval.outcomes import Outcome, classify
from mapeval.truth import AnswerKey

#: Métodos cuja aceitação não depende de ler o cabeçalho do cliente (valores
#: internos; ``catalog`` é ``catalogo`` no relatório).
HEADER_INDEPENDENT_METHODS = frozenset({"catalog"})


@dataclass
class Row:
    model_column: int
    model_name: str
    client_column: int | None
    #: Situação no vocabulário interno (``accepted``, ``review``, ``no_match``).
    status: str
    #: Método no vocabulário interno (``name``, ``data``, ``catalog``...).
    method: str
    final_confidence: float | None
    outcome: Outcome
    #: Identidade aritmética, nos dados do próprio cliente, que põe esta coluna aqui.
    arithmetic_proof: str | None = None

    @property
    def header_independent(self) -> bool:
        """Se algo além do cabeçalho sustenta o mapeamento: prova aritmética decisiva ou catálogo.

        Uma prova que dois blocos disjuntos do mesmo tributo satisfazem não
        distingue nenhum deles; o mapeador a marca com "não distingue esta
        coluna", e ``proof_is_decisive`` a descarta.
        """
        return (
            report_schema.proof_is_decisive(self.arithmetic_proof)
            or self.method in HEADER_INDEPENDENT_METHODS
        )


@dataclass
class Score:
    client: str
    rows: list[Row] = field(default_factory=list)
    present: int = 0
    #: Entregues sobre uma coluna sem dado. Bloqueante.
    vacuous_targets: list[Row] = field(default_factory=list)
    #: Propostas sobre uma coluna sem dado, mas não entregues. Só informativo.
    vacuous_proposals: list[Row] = field(default_factory=list)

    def count(self, outcome: Outcome) -> int:
        return sum(1 for row in self.rows if row.outcome is outcome)

    @property
    def recall_on_present(self) -> float | None:
        """Corretos / colunas que o cliente de fato tem. Nunca sobre 227."""
        return self.count(Outcome.CORRECT) / self.present if self.present else None

    @property
    def accepted(self) -> list[Row]:
        return [r for r in self.rows if r.status == "accepted"]

    @property
    def delivered_correct(self) -> int:
        """Mapeamentos corretos com situação ``accepted``: o que o cliente recebe.

        O apply também entrega as linhas confirmadas pelo revisor; esta conta
        ainda as ignora (KNOWN_ISSUES 2.6).
        """
        return sum(
            1
            for r in self.rows
            if r.outcome is Outcome.CORRECT and r.status == "accepted"
        )

    @property
    def delivered_recall(self) -> float | None:
        """O número que descreve o produto. Nunca acima de recall_on_present."""
        return self.delivered_correct / self.present if self.present else None

    @property
    def withheld_correct(self) -> list[Row]:
        """Coluna certa encontrada e retida pelo limiar de aceite.

        Em ordem decrescente de confiança, que é a ordem em que um revisor deve
        trabalhá-las.
        """
        held = [
            r
            for r in self.rows
            if r.outcome is Outcome.CORRECT and r.status != "accepted"
        ]
        return sorted(held, key=lambda r: -(r.final_confidence or 0.0))

    @property
    def accept_precision_lower(self) -> float | None:
        """Aceites sem veredito contam como errados, para que um aceite novo e
        não verificado baixe o limite, em vez de deixá-lo lisonjeiramente igual."""
        accepts = self.accepted
        if not accepts:
            return None
        good = sum(1 for r in accepts if r.outcome is Outcome.CORRECT)
        return good / len(accepts)

    @property
    def undetermined_accepts(self) -> list[Row]:
        """Aceites em colunas do modelo fora do gabarito, que ele não consegue julgar.

        O código de saída lê só as fabricações, então uma mudança que inventa
        mapeamentos fora do gabarito (uma regra de ICSP<-ICMS ajustada à mão,
        por exemplo) sai com 0. Esta contagem, impressa, é o que a denuncia.
        """
        return [r for r in self.accepted if r.outcome is Outcome.UNDETERMINED]

    @property
    def header_independent_share(self) -> float | None:
        """Fração dos aceites que não dependem só do cabeçalho: a métrica de generalização."""
        accepts = self.accepted
        if not accepts:
            return None
        return sum(1 for r in accepts if r.header_independent) / len(accepts)

    @property
    def correct_pairs(self) -> set[tuple[int, int]]:
        """Os pares (coluna do modelo, coluna do cliente) corretos.

        Para comparar duas execuções como conjunto: perder uma coluna e ganhar
        outra deixa a contagem igual e ainda assim é uma regressão. Usado pelo
        protocolo de docs/EVALUATION.md.
        """
        return {
            (r.model_column, r.client_column)
            for r in self.rows
            if r.outcome is Outcome.CORRECT and r.client_column is not None
        }


@lru_cache(maxsize=None)
def _vacuous_client_columns(client_path: str | Path) -> frozenset[int]:
    """Colunas do cliente (a partir de 1) sem nenhuma célula preenchida na primeira aba.

    Vazia, não toda zerada: uma coluna zerada pode ser legítima, e há colunas
    zeradas nos próprios gabaritos. Fica em cache porque varrer a exportação
    inteira é o único custo real da avaliação.
    """
    workbook = openpyxl.load_workbook(client_path, read_only=True, data_only=True)
    try:
        worksheet = workbook[workbook.sheetnames[0]]
        rows = worksheet.iter_rows(values_only=True)
        header = next(rows)
        populated = [0] * len(header)
        for row in rows:
            for index, value in enumerate(row[: len(header)]):
                if value is not None and str(value).strip() != "":
                    populated[index] += 1
    finally:
        workbook.close()
    return frozenset(i + 1 for i, count in enumerate(populated) if count == 0)


def read_mappings(report_path: str | Path) -> tuple[list[tuple], dict[str, int]]:
    """Lê a aba de mapeamentos de um relatório.

    Devolve as linhas cruas (a primeira é o cabeçalho) e o índice {nome
    interno do campo: posição da coluna}. Os valores das células não são
    convertidos; quem compara situação ou método usa
    ``report_schema.internal_value``.

    Levanta ``ValueError`` se falta a aba ``Mapeamentos`` ou se o relatório
    está, inteiro ou em parte, no formato antigo em inglês; a mensagem diz
    como convertê-lo.
    """
    workbook = openpyxl.load_workbook(report_path, read_only=True, data_only=True)
    try:
        name = report_schema.find_sheet(list(workbook.sheetnames), "mappings")
        if name is None:
            raise ValueError(
                f"{report_path}: o relatório não tem a aba "
                f"{report_schema.sheet_name('mappings')!r}."
            )
        raw = list(workbook[name].iter_rows(values_only=True))
    finally:
        workbook.close()
    return raw, report_schema.internal_header_index(raw[0], "mappings")


def score_report(
    report_path: str | Path,
    client_path: str | Path,
    key: AnswerKey,
) -> Score:
    """Pontua um relatório de mapeamento arquivado. Não roda o mapeador."""
    vacuous = _vacuous_client_columns(Path(client_path))
    raw, column = read_mappings(report_path)

    result = Score(client=key.client, present=key.denominator)
    # A linha de dados n é a coluna do modelo n (KNOWN_ISSUES 2.8).
    for position, cells in enumerate(raw[1:], start=1):
        row = _score_row(position, cells, column, key, vacuous)
        result.rows.append(row)
        if row.outcome is Outcome.FABRICATED:
            result.vacuous_targets.append(row)
        elif row.client_column in vacuous:
            result.vacuous_proposals.append(row)
    return result


def _score_row(
    position: int,
    cells: tuple,
    column: dict[str, int],
    key: AnswerKey,
    vacuous: frozenset[int],
) -> Row:
    """Lê uma linha da aba de mapeamentos e classifica a coluna do modelo dela."""
    chosen = cells[column["client_column_number"]]
    chosen = None if chosen in (None, "") else int(chosen)
    status = str(report_schema.internal_value("mappings", "status", cells[column["status"]]))
    method = str(
        report_schema.internal_value("mappings", "method", cells[column["method"]]) or ""
    )
    confidence = cells[column["final_confidence"]]
    # Relatórios antigos não têm a coluna prova_aritmetica.
    proof = cells[column["arithmetic_proof"]] if "arithmetic_proof" in column else None
    return Row(
        model_column=position,
        model_name=str(cells[column["model_column"]]),
        client_column=chosen,
        status=status,
        method=method,
        final_confidence=float(confidence) if isinstance(confidence, (int, float)) else None,
        arithmetic_proof=None if proof in (None, "") else str(proof),
        outcome=classify(
            chosen_client_column=chosen,
            expected_client_column=key.present.get(position),
            chosen_is_vacuous=chosen in vacuous,
            delivered=status == "accepted",
        ),
    )


def method_census(score: Score) -> Counter[str]:
    """Quantos aceites há por método, com os métodos no vocabulário interno."""
    return Counter(r.method for r in score.accepted)
