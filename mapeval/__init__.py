"""Evaluation harness for the invoice column mapper.

Scores a mapping report against the client-authored de-para answer keys.
Changes no mapping; it only measures.

Two rules govern every number this package reports:

1. The base model is a deliberate superset. A client legitimately carries
   only a minority of its 227 columns, so 227 is never a denominator here.
   Recall is always against the columns the client actually has.
2. Absence is a correct answer. The failure that matters is fabrication -
   claiming a model column the client has no data for - so the five
   outcomes are never collapsed into a single accuracy figure.
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
