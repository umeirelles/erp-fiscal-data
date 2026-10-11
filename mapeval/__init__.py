"""Avalia relatórios de mapeamento com os gabaritos de-para dos clientes.

A avaliação apenas lê os arquivos. O recall usa como denominador as colunas
anotadas no gabarito, e os resultados distinguem acertos, perdas, escolhas
erradas e mapeamentos aceitos para colunas vazias. Uma única taxa de acerto
esconderia diferenças importantes entre esses resultados.
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
