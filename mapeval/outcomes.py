"""Os cinco resultados, que nunca podem ser reduzidos a um número só.

Os relatórios de hoje não distinguem uma ausência correta de uma coluna
perdida, nem uma coluna errada de uma fabricação. São falhas diferentes, com
custos diferentes: numa saída fiscal, uma 'Base CBS' fabricada é muito pior
do que uma vazia.
"""

from __future__ import annotations

import enum


class Outcome(enum.Enum):
    """Resultado de uma coluna do modelo na avaliação.

    Os valores ficam em inglês porque são identificadores: nunca são gravados
    em arquivo. A CLI imprime o rótulo em português de
    ``report_schema.ENUMS["outcome"]``.
    """

    #: Está no conjunto presente, e o mapeador escolheu aquela coluna do cliente.
    CORRECT = "correct"
    #: Não existe coluna correspondente e o mapeador respondeu ``no_match``
    #: (``sem_correspondencia`` no relatório). Resposta certa.
    CORRECTLY_ABSENT = "correctly_absent"
    #: Existe coluna correspondente e o mapeador respondeu ``no_match``.
    MISSED = "missed"
    #: Existe coluna correspondente e o mapeador escolheu outra.
    WRONG_COLUMN = "wrong_column"
    #: Mapeada, mas a coluna do cliente escolhida não tem dado nenhum.
    FABRICATED = "fabricated"
    #: Fora do gabarito e sem veredito. Não conta a favor de nada e aparece
    #: inteira na contagem.
    UNDETERMINED = "undetermined"


#: Resultados que nunca podem piorar de uma execução para outra.
REGRESSION_GUARDED = (Outcome.CORRECT, Outcome.FABRICATED)


def classify(
    *,
    model_column: int,
    chosen_client_column: int | None,
    expected_client_column: int | None,
    chosen_is_vacuous: bool,
    in_key: bool,
    delivered: bool = True,
) -> Outcome:
    """Classifica o resultado de uma coluna do modelo.

    ``chosen_is_vacuous`` indica que a coluna do cliente escolhida não tem
    nenhuma linha preenchida. Se ela for entregue, isso é fabricação por
    construção - o sistema que recebe a planilha ganha uma coluna que parece
    mapeada e está em branco, sem aviso -, por isso essa condição é
    verificada antes de qualquer outra.

    ``delivered`` condiciona essa regra. O apply_invoice_mapping.py preenche
    só as linhas aceitas e as confirmadas pelo revisor, então uma proposta em
    *revisão* sobre uma coluna vazia nunca chega ao cliente. É um risco
    latente que vale reportar (está a um --include-review de distância), mas
    não é fabricação, e confundir as duas coisas exagera o risco entregue em
    uma ordem de grandeza: a execução offline propõe 35 linhas assim e não
    entrega nenhuma.

    Coluna vazia *não* é coluna toda zerada. Uma operação isenta tem base de
    cálculo zero, e seis das colunas totalmente zeradas de oracle são
    gabarito escrito pelo próprio cliente.
    """
    if delivered and chosen_client_column is not None and chosen_is_vacuous:
        return Outcome.FABRICATED

    if not in_key:
        if chosen_client_column is None:
            # Ausente e não reivindicada. Estaria certo com cobertura parcial,
            # mas o gabarito não diz isso, e inventar essa confirmação
            # inflaria todas as taxas desta avaliação.
            return Outcome.UNDETERMINED
        return Outcome.UNDETERMINED

    if chosen_client_column is None:
        return Outcome.MISSED if expected_client_column is not None else Outcome.CORRECTLY_ABSENT
    if expected_client_column is None:
        return Outcome.FABRICATED
    return Outcome.CORRECT if chosen_client_column == expected_client_column else Outcome.WRONG_COLUMN
