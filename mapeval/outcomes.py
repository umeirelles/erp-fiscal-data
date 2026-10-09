"""O resultado de cada coluna do modelo na avaliação.

Os resultados nunca se reduzem a um número só: perder uma coluna e fabricar
outra são falhas de custo muito diferente, e numa saída fiscal uma 'Base CBS'
fabricada é muito pior do que uma vazia.
"""

from __future__ import annotations

import enum


class Outcome(enum.Enum):
    """Resultado de uma coluna do modelo.

    Os valores são identificadores internos e nunca vão para arquivo; a CLI
    imprime o rótulo em português de ``report_schema.ENUMS["outcome"]``.
    """

    #: O mapeador escolheu a coluna do cliente que o gabarito indica.
    CORRECT = "correct"
    #: O gabarito indica uma coluna e o mapeador não propôs nenhuma.
    MISSED = "missed"
    #: O gabarito indica uma coluna e o mapeador propôs outra.
    WRONG_COLUMN = "wrong_column"
    #: Entregue sobre uma coluna do cliente sem nenhum dado.
    FABRICATED = "fabricated"
    #: Coluna do modelo fora do gabarito: sem veredito, não conta a favor de nada.
    UNDETERMINED = "undetermined"


def classify(
    *,
    chosen_client_column: int | None,
    expected_client_column: int | None,
    chosen_is_vacuous: bool,
    delivered: bool = True,
) -> Outcome:
    """Classifica uma coluna do modelo; ``expected_client_column`` é None fora do gabarito.

    A coluna vazia é verificada antes de tudo: entregue, ela chega ao cliente
    parecendo mapeada e em branco, então é fabricação mesmo que o gabarito
    concorde. Só vale para o que é entregue, porque o apply não preenche uma
    proposta em revisão. Vazia é sem nenhuma célula preenchida, não toda
    zerada: uma operação isenta tem base de cálculo zero legítima.
    """
    if delivered and chosen_client_column is not None and chosen_is_vacuous:
        return Outcome.FABRICATED
    if expected_client_column is None:
        # O gabarito não diz nada aqui; inventar um veredito, mesmo o de
        # "ausente com acerto", inflaria todas as taxas.
        return Outcome.UNDETERMINED
    if chosen_client_column is None:
        return Outcome.MISSED
    if chosen_client_column == expected_client_column:
        return Outcome.CORRECT
    return Outcome.WRONG_COLUMN
