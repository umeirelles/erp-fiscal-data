"""Avaliação do mapeador de colunas de notas fiscais.

Pontua um relatório de mapeamento contra os gabaritos de-para escritos pelo
próprio cliente. Não altera nenhum mapeamento; só mede.

Duas regras valem para todo número que este pacote mostra:

1. O modelo base é, de propósito, um superconjunto. Um cliente tem, com
   razão, só uma minoria das 227 colunas do modelo, então 227 nunca é
   denominador aqui. O recall é sempre medido contra as colunas que o
   cliente de fato tem.
2. Ausência é resposta certa. A falha que importa é a fabricação - declarar
   uma coluna do modelo para a qual o cliente não tem dado -, por isso os
   cinco resultados nunca são reduzidos a um único número de acurácia.

Os relatórios são lidos no esquema em português, por meio de
``report_schema``; um relatório no formato antigo, em inglês, é recusado com
a indicação de como convertê-lo.
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
