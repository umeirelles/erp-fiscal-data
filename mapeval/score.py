"""Pontua um relatório de mapeamento contra um gabarito.

O relatório está no esquema em português. A leitura passa por
``report_schema``: os cabeçalhos viram os nomes internos e os valores de
situação e método voltam ao vocabulário interno (``accepted``, ``catalog``
...) antes de qualquer comparação.
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

#: Métodos cuja aceitação não depende de ler o cabeçalho do cliente. Valores
#: internos: ``catalog`` aparece como ``catalogo`` no relatório em português.
HEADER_INDEPENDENT_METHODS = frozenset({"catalog"})


@dataclass
class Row:
    model_column: int
    model_name: str
    client_column: int | None
    #: Situação no vocabulário interno (``accepted``, ``review``, ``no_match``).
    status: str
    #: Método no vocabulário interno (``name``, ``data``, ``catalog`` ...).
    method: str
    final_confidence: float | None
    outcome: Outcome
    #: Identidade aritmética, nos dados do próprio cliente, que põe esta coluna aqui.
    arithmetic_proof: str | None = None

    @property
    def header_independent(self) -> bool:
        """Se algo além do cabeçalho sustenta este mapeamento.

        É a pergunta da generalização. Uma aceitação apoiada só no texto do
        cabeçalho do cliente pode não sobreviver a um ERP cujo vocabulário
        nunca vimos, e os quatro clientes de amostra são material de
        referência, não o entregável. Aritmética e domínios de códigos
        oficiais se transferem; um casamento de cabeçalho, não.
        """
        # Uma prova confirma o *escopo* de um tributo. Quando o cliente tem
        # dois blocos disjuntos para o mesmo tributo, os dois satisfazem a
        # identidade, então a prova vale para qualquer um e não distingue
        # nenhum - e o mapeador diz isso na própria frase, com o marcador
        # "não distingue esta coluna", que proof_is_decisive reconhece.
        # Contar uma prova assim como evidência independente deixaria um cara
        # ou coroa entre dois blocos se passar por aritmética. Hoje quatro
        # aceites de datasul (m53, m54, m55 e m184) têm uma prova assim, e
        # esta regra os mantém fora da métrica.
        decisive = report_schema.proof_is_decisive(self.arithmetic_proof)
        return decisive or self.method in HEADER_INDEPENDENT_METHODS


@dataclass
class Score:
    client: str
    rows: list[Row] = field(default_factory=list)
    present: int = 0
    client_columns: int = 0
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
        """Mapeamentos corretos que o sistema a jusante de fato recebe.

        O apply_invoice_mapping.py preenche só as linhas aceitas e as
        confirmadas pelo revisor, então um acerto deixado em *revisão* fica
        invisível para o cliente. Reportar só o recall da escolha exagera a
        entrega.
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
        """Coluna certa encontrada e depois retida pelo limiar de aceite.

        Não é artefato da pontuação: cada uma destas é uma coluna que o
        escritório poderia ter entregue e não entregou. Ficam ordenadas por
        quão perto chegaram de ser entregues, porque é nessa ordem que um
        revisor deve trabalhá-las.
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
        """Mapeamentos aceitos que o gabarito não consegue julgar, nem para um lado nem para o outro.

        É o ponto cego, e ele é estrutural, não um defeito: com o gabarito
        cobrindo 56 das 227 colunas do modelo, ``classify`` não tem como
        dizer se um aceite fora do gabarito está certo, e inventar um
        veredito é exatamente o que Outcome.UNDETERMINED existe para impedir.
        Só que uma mudança que inventa mapeamentos fora do gabarito faz este
        número crescer enquanto ``fabrications`` - a única coisa que o código
        de saída lê - continua em zero. Uma regra ajustada à mão de
        ICSP<-ICMS soma +3 corretos e 0 fabricações, sai com código 0 e grava
        alíquotas erradas em milhares de linhas. Imprimir a contagem torna o
        silêncio visível; accept_precision_lower já leva isso em conta.
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
        """Comparados como conjunto, nunca como contagem: perder uma coluna e
        ganhar outra deixa o total igual e ainda assim é uma regressão."""
        return {
            (r.model_column, r.client_column)
            for r in self.rows
            if r.outcome is Outcome.CORRECT and r.client_column is not None
        }


@lru_cache(maxsize=None)
def _vacuous_client_columns(client_path: str | Path, sheet: int = 0) -> frozenset[int]:
    """Colunas do cliente sem nenhuma linha preenchida.

    De propósito, não é "toda zerada": datasul tem 200 colunas totalmente
    zeradas e seis das de oracle são gabarito do cliente, então uma trava de
    coluna zerada destruiria mapeamentos verificados como corretos.

    Fica em cache: varrer todas as células de uma exportação de 8,9 MB é o
    único custo real da avaliação, e o mesmo cliente é pontuado várias vezes.
    """
    workbook = openpyxl.load_workbook(client_path, read_only=True, data_only=True)
    try:
        worksheet = workbook[workbook.sheetnames[sheet]]
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
    interno do campo: posição da coluna}. As células continuam cruas: quem
    compara situação ou método converte o valor com
    ``report_schema.internal_value``.

    Levanta ``ValueError`` se o relatório não tem a aba ``Mapeamentos`` ou
    se está no formato antigo, em inglês, inteiro ou em parte (por exemplo,
    a aba ``Mappings`` ou o cabeçalho ``model_column_number``); a mensagem
    diz como converter um relatório antigo.
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
    """Pontua um relatório de mapeamento arquivado. Não roda mapeamento e não custa nada."""
    vacuous = _vacuous_client_columns(Path(client_path))

    raw, column = read_mappings(report_path)

    result = Score(client=key.client, present=key.denominator)
    for position, raw_row in enumerate(raw[1:], start=1):
        chosen = raw_row[column["client_column_number"]]
        chosen = int(chosen) if chosen not in (None, "") else None
        expected = key.present.get(position)
        confidence = raw_row[column["final_confidence"]]
        # Situação e método no vocabulário interno: "aceito" vira "accepted",
        # "catalogo" vira "catalog".
        status = str(report_schema.internal_value("mappings", "status", raw_row[column["status"]]))
        method = str(
            report_schema.internal_value("mappings", "method", raw_row[column["method"]]) or ""
        )
        row = Row(
            model_column=position,
            model_name=str(raw_row[column["model_column"]]),
            client_column=chosen,
            status=status,
            method=method,
            final_confidence=float(confidence) if isinstance(confidence, (int, float)) else None,
            arithmetic_proof=(
                str(raw_row[column["arithmetic_proof"]])
                if "arithmetic_proof" in column
                and raw_row[column["arithmetic_proof"]] not in (None, "")
                else None
            ),
            outcome=classify(
                model_column=position,
                chosen_client_column=chosen,
                expected_client_column=expected,
                chosen_is_vacuous=chosen in vacuous if chosen is not None else False,
                in_key=position in key.present,
                delivered=status == "accepted",
            ),
        )
        result.rows.append(row)
        if row.outcome is Outcome.FABRICATED:
            result.vacuous_targets.append(row)
        elif chosen is not None and chosen in vacuous:
            result.vacuous_proposals.append(row)
    return result


def method_census(score: Score) -> Counter[str]:
    """Quantos aceites há por método, com os métodos no vocabulário interno."""
    return Counter(r.method for r in score.accepted)
