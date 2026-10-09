"""Avaliação do mapeador: pontua um relatório de mapeamento contra o gabarito de-para do cliente.

Só mede; nunca altera um mapeamento. Duas regras valem para todo número daqui:
o recall é medido contra as colunas que o cliente de fato tem, nunca contra as
227 do modelo, e os resultados nunca se reduzem a uma acurácia única, porque
fabricar uma coluna é muito pior do que deixá-la vazia.
"""

from mapeval.excel import column_letter_to_index, index_to_column_letter
from mapeval.outcomes import Outcome, classify
from mapeval.truth import AnswerKey, load_answer_key

__all__ = [
    "AnswerKey",
    "Outcome",
    "classify",
    "column_letter_to_index",
    "index_to_column_letter",
    "load_answer_key",
]
