"""Tests for the evaluation harness.

The harness measures; it must not drift. These pin the invariants that make
its numbers trustworthy, and the baseline it must reproduce.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from mapeval.excel import (
    column_letter_to_index,
    index_to_column_letter,
    parse_coluna_annotation,
)
from mapeval.outcomes import Outcome, classify
from mapeval.score import score_report
from mapeval.truth import KEY_CORRECTIONS, load_answer_key

ROOT = Path(__file__).resolve().parent.parent
KEYED = {
    "datasul": ("datasul_maio.xlsx", "mapping_datasul_full.xlsx", "de_para_datasul.xlsx"),
    "oracle": ("oracle.xlsx", "mapping_oracle_full.xlsx", "de_para_oracle.xlsx"),
}
requires_workbooks = pytest.mark.skipif(
    not (ROOT / "de_para_oracle.xlsx").exists(),
    reason="client workbooks not present",
)


@pytest.mark.parametrize(
    ("letters", "index"),
    [("A", 1), ("Z", 26), ("AA", 27), ("AZ", 52), ("BA", 53), ("BV", 74), ("FY", 181), ("GZ", 208)],
)
def test_column_letters_round_trip(letters: str, index: int) -> None:
    assert column_letter_to_index(letters) == index
    assert index_to_column_letter(index) == letters


def test_annotation_tolerates_the_trailing_space_in_the_real_key() -> None:
    # de_para_datasul.xlsx row 1 really does contain 'Coluna BV ' with a
    # trailing space; a naive parser silently drops that column.
    assert parse_coluna_annotation("Coluna BV ") == 74


def test_annotation_ignores_cells_that_are_not_annotations() -> None:
    assert parse_coluna_annotation(None) is None
    assert parse_coluna_annotation("Base CBS") is None


def test_a_vacuous_target_is_fabrication_even_when_the_key_agrees() -> None:
    # An accepted mapping onto a column with no data reaches the customer's
    # workbook looking mapped and silently blank. It is wrong regardless of
    # what the key says, so vacuity is checked before anything else.
    assert classify(
        model_column=1,
        chosen_client_column=5,
        expected_client_column=5,
        chosen_is_vacuous=True,
        in_key=True,
    ) is Outcome.FABRICATED


def test_absence_is_a_correct_answer_not_a_miss() -> None:
    assert classify(
        model_column=1,
        chosen_client_column=None,
        expected_client_column=None,
        chosen_is_vacuous=False,
        in_key=True,
    ) is Outcome.CORRECTLY_ABSENT


def test_unkeyed_columns_are_never_credited() -> None:
    for chosen in (None, 5):
        assert classify(
            model_column=1,
            chosen_client_column=chosen,
            expected_client_column=None,
            chosen_is_vacuous=False,
            in_key=False,
        ) is Outcome.UNDETERMINED


@requires_workbooks
@pytest.mark.parametrize(("client", "expected"), [("datasul", 30), ("oracle", 26)])
def test_recall_denominator_is_the_present_set_never_227(client: str, expected: int) -> None:
    key = load_answer_key(ROOT / KEYED[client][2], client)
    assert key.denominator == expected != 227


@requires_workbooks
def test_harness_reproduces_the_corrected_baseline() -> None:
    total = 0
    for client, (client_file, report_file, key_file) in KEYED.items():
        key = load_answer_key(ROOT / key_file, client)
        score = score_report(ROOT / report_file, ROOT / client_file, key)
        total += score.count(Outcome.CORRECT)
    assert total == 37


@requires_workbooks
def test_the_four_key_corrections_are_worth_exactly_four_columns() -> None:
    # If this drifts, either the reports changed or a correction is wrong.
    # Nothing else may separate the strict and corrected figures.
    strict = corrected = 0
    for client, (client_file, report_file, key_file) in KEYED.items():
        for apply_corrections, bucket in ((False, "strict"), (True, "corrected")):
            key = load_answer_key(ROOT / key_file, client, apply_corrections=apply_corrections)
            score = score_report(ROOT / report_file, ROOT / client_file, key)
            if bucket == "strict":
                strict += score.count(Outcome.CORRECT)
            else:
                corrected += score.count(Outcome.CORRECT)
    assert (strict, corrected) == (33, 37)
    assert sum(len(v) for v in KEY_CORRECTIONS.values()) == 4


@requires_workbooks
def test_the_two_known_datasul_fabrications_are_detected() -> None:
    key = load_answer_key(ROOT / "de_para_datasul.xlsx", "datasul")
    score = score_report(
        ROOT / "mapping_datasul_full.xlsx", ROOT / "datasul_maio.xlsx", key
    )
    fabricated = {row.model_column for row in score.vacuous_targets}
    assert fabricated == {32, 188}
