"""Score a mapping report against an answer key."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

import openpyxl

from mapeval.outcomes import Outcome, classify
from mapeval.truth import AnswerKey

#: Methods whose acceptance does not depend on reading the client's header.
HEADER_INDEPENDENT_METHODS = frozenset({"catalog"})


@dataclass
class Row:
    model_column: int
    model_name: str
    client_column: int | None
    status: str
    method: str
    final_confidence: float | None
    outcome: Outcome
    #: A client-side arithmetic identity that places this column here.
    arithmetic_proof: str | None = None

    @property
    def header_independent(self) -> bool:
        """Whether anything other than the header supports this mapping.

        The generalization question. An acceptance resting only on the client's
        header string is one that may not survive an ERP whose vocabulary we
        have never seen, and the four sample clients are reference material
        rather than the deliverable. Arithmetic and official code domains both
        transfer; a header match does not.
        """
        return bool(self.arithmetic_proof) or self.method in HEADER_INDEPENDENT_METHODS


@dataclass
class Score:
    client: str
    rows: list[Row] = field(default_factory=list)
    present: int = 0
    client_columns: int = 0
    #: Delivered onto a column with no data. Blocking.
    vacuous_targets: list[Row] = field(default_factory=list)
    #: Proposed onto a column with no data but not delivered. Reported only.
    vacuous_proposals: list[Row] = field(default_factory=list)

    def count(self, outcome: Outcome) -> int:
        return sum(1 for row in self.rows if row.outcome is outcome)

    @property
    def recall_on_present(self) -> float | None:
        """Correct / the columns the client actually has. Never over 227."""
        return self.count(Outcome.CORRECT) / self.present if self.present else None

    @property
    def accepted(self) -> list[Row]:
        return [r for r in self.rows if r.status == "accepted"]

    @property
    def accept_precision_lower(self) -> float | None:
        """Undetermined accepts counted as wrong, so a new unverified accept
        lowers the bound rather than leaving it flatteringly unchanged."""
        accepts = self.accepted
        if not accepts:
            return None
        good = sum(1 for r in accepts if r.outcome is Outcome.CORRECT)
        return good / len(accepts)

    @property
    def header_independent_share(self) -> float | None:
        accepts = self.accepted
        if not accepts:
            return None
        return sum(1 for r in accepts if r.header_independent) / len(accepts)

    @property
    def correct_pairs(self) -> set[tuple[int, int]]:
        """Compared as a set, never a count: losing one column while gaining
        another leaves the total unchanged and is still a regression."""
        return {
            (r.model_column, r.client_column)
            for r in self.rows
            if r.outcome is Outcome.CORRECT and r.client_column is not None
        }


@lru_cache(maxsize=None)
def _vacuous_client_columns(client_path: str | Path, sheet: int = 0) -> frozenset[int]:
    """Client columns with zero populated rows.

    Deliberately not all-zero: datasul has 200 all-zero columns and six of
    oracle's are client-authored truth, so an all-zero guard would destroy
    verified-correct mappings.

    Cached: scanning every cell of an 8.9MB export is the harness's only
    real cost, and the same client is scored repeatedly.
    """
    workbook = openpyxl.load_workbook(client_path, read_only=True, data_only=True)
    try:
        worksheet = workbook[workbook.sheetnames[sheet]]
        rows = worksheet.iter_rows(values_only=True)
        header = next(rows)
        populated = [0] * len(header)
        for row in rows:
            for index, value in enumerate(row[: len(header)]):
                if value is not None and str(value).strip() != "":
                    populated[index] += 1
    finally:
        workbook.close()
    return frozenset(i + 1 for i, count in enumerate(populated) if count == 0)


def score_report(
    report_path: str | Path,
    client_path: str | Path,
    key: AnswerKey,
) -> Score:
    """Score an archived mapping report. Runs no mapping and costs nothing."""
    vacuous = _vacuous_client_columns(Path(client_path))

    workbook = openpyxl.load_workbook(report_path, read_only=True, data_only=True)
    try:
        raw = list(workbook["Mappings"].iter_rows(values_only=True))
    finally:
        workbook.close()
    column = {name: i for i, name in enumerate(raw[0])}

    result = Score(client=key.client, present=key.denominator)
    for position, raw_row in enumerate(raw[1:], start=1):
        chosen = raw_row[column["client_column_number"]]
        chosen = int(chosen) if chosen not in (None, "") else None
        expected = key.present.get(position)
        confidence = raw_row[column["final_confidence"]]
        row = Row(
            model_column=position,
            model_name=str(raw_row[column["model_column"]]),
            client_column=chosen,
            status=str(raw_row[column["status"]]),
            method=str(raw_row[column["method"]] or ""),
            final_confidence=float(confidence) if isinstance(confidence, (int, float)) else None,
            arithmetic_proof=(
                str(raw_row[column["arithmetic_proof"]])
                if "arithmetic_proof" in column
                and raw_row[column["arithmetic_proof"]] not in (None, "")
                else None
            ),
            outcome=classify(
                model_column=position,
                chosen_client_column=chosen,
                expected_client_column=expected,
                chosen_is_vacuous=chosen in vacuous if chosen is not None else False,
                in_key=position in key.present,
                delivered=str(raw_row[column["status"]]) == "accepted",
            ),
        )
        result.rows.append(row)
        if row.outcome is Outcome.FABRICATED:
            result.vacuous_targets.append(row)
        elif chosen is not None and chosen in vacuous:
            result.vacuous_proposals.append(row)
    return result


def method_census(score: Score) -> Counter[str]:
    return Counter(r.method for r in score.accepted)
