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
from mapeval.score import Row, score_report
from mapeval.truth import KEY_CORRECTIONS, load_answer_key

ROOT = Path(__file__).resolve().parent.parent
KEYED = {
    "datasul": (
        "data/clients/datasul_maio.xlsx",
        "data/reports/mapping_datasul_full.xlsx",
        "data/keys/de_para_datasul.xlsx",
    ),
    "oracle": (
        "data/clients/oracle.xlsx",
        "data/reports/mapping_oracle_full.xlsx",
        "data/keys/de_para_oracle.xlsx",
    ),
}
requires_workbooks = pytest.mark.skipif(
    not (ROOT / "data/keys/de_para_oracle.xlsx").exists(),
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


def test_a_delivered_vacuous_target_is_fabrication_even_when_the_key_agrees() -> None:
    # An accepted mapping onto a column with no data reaches the customer's
    # workbook looking mapped and silently blank. It is wrong regardless of
    # what the key says, so vacuity is checked before anything else.
    assert classify(
        model_column=1,
        chosen_client_column=5,
        expected_client_column=5,
        chosen_is_vacuous=True,
        in_key=True,
        delivered=True,
    ) is Outcome.FABRICATED


def test_an_undelivered_vacuous_proposal_is_not_a_fabrication() -> None:
    # A review row is never populated by apply_invoice_mapping, so it cannot
    # reach the customer. Conflating the two made the offline run report 35
    # fabrications where it delivers none.
    assert classify(
        model_column=1,
        chosen_client_column=5,
        expected_client_column=5,
        chosen_is_vacuous=True,
        in_key=True,
        delivered=False,
    ) is Outcome.CORRECT


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
    assert total == 43


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
    # The literals move whenever the mapper improves; the DELTA is the
    # invariant, and it is what this test exists to protect.
    assert corrected - strict == 4
    assert (strict, corrected) == (39, 43)
    assert sum(len(v) for v in KEY_CORRECTIONS.values()) == 4


@requires_workbooks
@pytest.mark.parametrize(("client", "report"), [
    ("datasul", "data/reports/mapping_datasul_full.xlsx"),
    ("oracle", "data/reports/mapping_oracle_full.xlsx"),
])
def test_no_report_delivers_a_column_with_no_data(client: str, report: str) -> None:
    # datasul shipped two of these - m32 <- c280 and m188 <- c326, both
    # accepted on header similarity onto columns with zero populated rows out
    # of 6,804. The vacuity guard removed them; this keeps them gone.
    key = load_answer_key(ROOT / KEYED[client][2], client)
    score = score_report(ROOT / report, ROOT / KEYED[client][0], key)
    assert [
        (row.model_column, row.client_column) for row in score.vacuous_targets
    ] == []


@requires_workbooks
def test_delivered_recall_is_never_above_picking_recall() -> None:
    # The invariant that makes the two numbers safe to print side by side:
    # delivery is a subset of picking, so a run can never deliver a column it
    # did not first find.
    for client, (client_file, report_file, key_file) in KEYED.items():
        key = load_answer_key(ROOT / key_file, client)
        score = score_report(ROOT / report_file, ROOT / client_file, key)
        assert score.delivered_correct <= score.count(Outcome.CORRECT)
        assert len(score.withheld_correct) == (
            score.count(Outcome.CORRECT) - score.delivered_correct
        )


@requires_workbooks
def test_the_accept_gate_withholds_eleven_correct_mappings() -> None:
    # Picking recall is 42/56 but the customer receives 31. The gap is the
    # single largest loss in the pipeline and it is invisible to any metric
    # that scores the pick alone, so it is pinned here.
    found = delivered = 0
    for client, (client_file, report_file, key_file) in KEYED.items():
        key = load_answer_key(ROOT / key_file, client)
        score = score_report(ROOT / report_file, ROOT / client_file, key)
        found += score.count(Outcome.CORRECT)
        delivered += score.delivered_correct
    # The gap is the invariant this test is named for; the totals move as the
    # mapper improves and both must move together or something regressed.
    assert found - delivered == 11
    assert (found, delivered) == (43, 32)


@requires_workbooks
def test_every_withheld_correct_mapping_sits_below_the_accept_threshold() -> None:
    # Confirms the gap is the confidence gate and not some other refusal:
    # if a withheld row ever clears 0.90, something else is blocking it and
    # this analysis no longer explains the loss.
    for client, (client_file, report_file, key_file) in KEYED.items():
        key = load_answer_key(ROOT / key_file, client)
        score = score_report(ROOT / report_file, ROOT / client_file, key)
        for row in score.withheld_correct:
            assert (row.final_confidence or 0.0) < 0.90, (
                f"{client} m{row.model_column} withheld at {row.final_confidence}"
            )


def test_a_non_discriminating_proof_is_not_independent_evidence() -> None:
    # Two disjoint blocks for one tax both satisfy valor = base x aliquota, so
    # a proof drawn from either singles out neither. Crediting it would let a
    # coin-flip between blocks read as arithmetic evidence.
    def row(proof: str) -> Row:
        return Row(model_column=213, model_name="Aliquota PIS", client_column=96,
                   status="accepted", method="data", final_confidence=0.98,
                   outcome=Outcome.CORRECT, arithmetic_proof=proof)
    assert row("valor = base x aliquota holds for pis on 500 rows at fit 0.99").header_independent
    assert not row(
        "valor = base x aliquota holds for pis on 5501 rows at fit 0.9996 - but this "
        "client has 2 disjoint pis blocks that each satisfy it, so this does not "
        "single out this column"
    ).header_independent


@requires_workbooks
def test_accepts_the_key_cannot_check_are_counted_and_visible() -> None:
    # The exit code reads fabrications only, and an out-of-key accept onto a
    # POPULATED column is UNDETERMINED, not FABRICATED - so a rule that invents
    # mappings outside the key exits 0. This pins the number that exposes it.
    for client, (client_file, report_file, key_file) in KEYED.items():
        key = load_answer_key(ROOT / key_file, client)
        score = score_report(ROOT / report_file, ROOT / client_file, key)
        assert len(score.undetermined_accepts) == (
            len(score.accepted)
            - sum(1 for r in score.accepted if r.outcome is not Outcome.UNDETERMINED)
        )
        # and it is exactly what separates the precision bound from 100%
        good = sum(1 for r in score.accepted if r.outcome is Outcome.CORRECT)
        assert good + len(score.undetermined_accepts) <= len(score.accepted)
