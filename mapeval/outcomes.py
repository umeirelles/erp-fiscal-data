"""The five outcomes, which must never be collapsed.

Today's reporting cannot tell a correct absence from a miss, nor a wrong
column from a fabrication. Those are different failures with different costs:
on tax output a fabricated 'Base CBS' is far worse than an empty one.
"""

from __future__ import annotations

import enum


class Outcome(enum.Enum):
    #: In the present-set, and the mapper picked that client column.
    CORRECT = "correct"
    #: No counterpart exists and the mapper said no_match. A correct answer.
    CORRECTLY_ABSENT = "correctly_absent"
    #: A counterpart exists and the mapper said no_match.
    MISSED = "missed"
    #: A counterpart exists and the mapper picked a different column.
    WRONG_COLUMN = "wrong_column"
    #: Mapped, but the target client column carries no data at all.
    FABRICATED = "fabricated"
    #: Not in the key and not adjudicated. Credited to nothing, printed in full.
    UNDETERMINED = "undetermined"


#: Outcomes that may never get worse between runs.
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
    """Classify one model column's outcome.

    ``chosen_is_vacuous`` means the selected client column has zero populated
    rows. Delivered, that is fabrication by construction - the downstream app
    receives a column that looks mapped and is silently blank - so it is
    checked before anything else.

    ``delivered`` gates that. apply_invoice_mapping.py populates only accepted
    and reviewer-confirmed rows, so a *review* proposal onto a vacuous column
    never reaches the customer. It is a latent risk worth reporting (one
    --include-review away) but it is not a fabrication, and conflating the two
    overstates the delivered risk by an order of magnitude: the offline run
    proposes 35 such rows while delivering none of them.

    Vacuity is *not* all-zero. An exempt operation has a zero base, and six of
    oracle's all-zero columns are client-authored ground truth.
    """
    if delivered and chosen_client_column is not None and chosen_is_vacuous:
        return Outcome.FABRICATED

    if not in_key:
        if chosen_client_column is None:
            # Absent and unclaimed. Correct under partial coverage, but the key
            # does not say so, and inventing that confirmation would inflate
            # every rate in this harness.
            return Outcome.UNDETERMINED
        return Outcome.UNDETERMINED

    if chosen_client_column is None:
        return Outcome.MISSED if expected_client_column is not None else Outcome.CORRECTLY_ABSENT
    if expected_client_column is None:
        return Outcome.FABRICATED
    return Outcome.CORRECT if chosen_client_column == expected_client_column else Outcome.WRONG_COLUMN
