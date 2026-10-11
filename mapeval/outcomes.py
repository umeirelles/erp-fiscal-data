"""Classifica o resultado do mapeamento de cada coluna.

As categorias separam uma coluna não encontrada de uma escolha errada ou
de uma coluna vazia aceita como origem. Essa distinção permite avaliar o
impacto de cada erro, que uma taxa única de acerto não mostraria.
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
    #: Mapeamento aceito cuja coluna de origem está vazia.
    FABRICATED = "fabricated"
    #: Campo fora do gabarito; não há referência para confirmar a escolha.
    UNDETERMINED = "undetermined"


def classify(
    *,
    chosen_client_column: int | None,
    expected_client_column: int | None,
    chosen_is_vacuous: bool,
    delivered: bool = True,
) -> Outcome:
    """Compara a escolha com o gabarito e verifica se houve aceitação de uma coluna vazia.

    ``expected_client_column=None`` indica que o campo não consta no gabarito.
    Antes dessa comparação, uma coluna vazia entregue é classificada como
    fabricação, mesmo que o gabarito aponte para ela. Propostas ainda em revisão
    não entram nessa categoria. Uma coluna preenchida com zeros contém dados
    e não é considerada vazia.
    """
    if delivered and chosen_client_column is not None and chosen_is_vacuous:
        return Outcome.FABRICATED
    if expected_client_column is None:
        # Fora do gabarito, não é possível confirmar nem a escolha nem a ausência.
        # Contabilizar esses casos como acertos aumentaria as métricas sem validação.
        return Outcome.UNDETERMINED
    if chosen_client_column is None:
        return Outcome.MISSED
    if chosen_client_column == expected_client_column:
        return Outcome.CORRECT
    return Outcome.WRONG_COLUMN
