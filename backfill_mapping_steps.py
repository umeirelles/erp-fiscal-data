"""Add the named analysis steps to a report written before they existed.

Reports produced before ``mapping_steps.py`` carry the evidence but not the
names: they record the catalog that matched, the arithmetic proof, the header
score and whether the resolver ran, but never say which of those carried the
decision. This reconstructs ``supported_by`` / ``decided_by`` / ``vetoed_by``
from what is already stored, without re-running the mapper.

That matters for more than cost. Re-running is not free of consequences: the
resolver is non-deterministic, so a fresh run churns *review* rows an analyst
may be part-way through, and for sap_procosa it would destroy 26 recorded
reviewer decisions outright.

Two of the seven steps cannot always be recovered. ``profile`` and
``identifier`` are folded into a single stored ``data_confidence``, so where
that score exceeds the generic-profile ceiling with no catalog and no proof to
explain it, something objective ran and the report does not say which. Those
rows are marked ``unrecorded`` rather than guessed - a wrong provenance label
is worse than an honest gap, because the whole point of these columns is to be
able to trust what the tool says it did.

Arithmetic is recovered rather than read: tax trios are discovered from the
client's own columns, so the proof can be recomputed offline for free. That is
what lets sap_procosa - whose report predates the ``arithmetic_proof`` column
entirely - still get its arithmetic support named.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import openpyxl

from invoice_mapper import (
    SheetTable,
    client_column_is_vacuous,
    discover_client_tax_trios,
    semantic_header_conflict,
    trio_corroboration,
    _trio_proof_text,
)
from mapping_steps import deciding_step, format_steps

#: An objective step ran and the report is too old to say which.
UNRECORDED = "unrecorded"

#: Above this, something other than generic shape similarity is in play - it is
#: the ceiling invoice_mapper puts on generic_profile_score.
GENERIC_PROFILE_CEILING = 0.84

#: What catalog evidence must score to count as deciding rather than merely
#: agreeing. invoice_mapper.catalog_evidence_is_distinctive applies the same
#: bar to a live run; the stored confidence already carries the specificity
#: discount that pushes collision-prone domains such as CST below it.
DISTINCTIVE_CATALOG_SCORE = 0.90

NEW_COLUMNS = ("decided_by", "supported_by", "vetoed_by")


def _number(value: object) -> float:
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0.0


def _header_step(book: openpyxl.Workbook) -> str:
    """Which header scorer produced this report, per its own run metadata."""
    if "Run Metadata" not in book.sheetnames:
        return "lexical"
    for row in book["Run Metadata"].values:
        if row and str(row[0]) == "embedding_model":
            return "lexical" if str(row[1]).strip().lower() == "disabled" else "semantic"
    return "lexical"


def backfill(report_path: Path, client_path: Path, *, dry_run: bool = False) -> dict[str, int]:
    book = openpyxl.load_workbook(report_path)
    sheet = book["Mappings"]
    header = [cell.value for cell in sheet[1]]
    if any(name in header for name in NEW_COLUMNS):
        raise SystemExit(f"{report_path.name} already carries the step columns")

    client = SheetTable.from_excel(client_path, 0, 0)
    trios = discover_client_tax_trios(client)
    header_step = _header_step(book)
    index = {name: position for position, name in enumerate(header) if name}

    # Match a fresh report's layout so old and new files read the same way.
    anchor = next(
        (index[name] + 1 for name in ("arithmetic_proof", "reviewer_status") if name in index),
        len(header) + 1,
    )
    sheet.insert_cols(anchor, amount=len(NEW_COLUMNS))
    for offset, name in enumerate(NEW_COLUMNS):
        sheet.cell(1, anchor + offset).value = name

    def stored(row: int, name: str) -> object:
        return sheet.cell(row, index[name] + 1).value if name in index else None

    tally = {"rows": 0, UNRECORDED: 0, "arithmetic": 0, "vetoed": 0}
    for excel_row in range(2, sheet.max_row + 1):
        model_column = stored(excel_row, "model_column")
        if model_column in (None, ""):
            continue
        raw_client = stored(excel_row, "client_column_number")
        if raw_client in (None, ""):
            continue
        client_index = int(_number(raw_client)) - 1
        if client_index < 0 or client_index >= len(client.columns):
            continue
        tally["rows"] += 1

        steps: list[str] = []
        #: Evidence that agrees but is too weak to carry the decision.
        corroborating: list[str] = []
        if _number(stored(excel_row, "llm_final_confidence")) > 0:
            steps.append("resolver")
        if _number(stored(excel_row, "name_confidence")) > 0:
            steps.append(header_step)
        # Match the live path, which scores catalog evidence through
        # catalog_evidence_is_distinctive and zeroes anything that does not
        # clear it. A two-digit CST covers its table completely and still says
        # little, because the same codes appear across PIS, COFINS and IPI -
        # which is what the specificity discount already encodes in the stored
        # confidence. Crediting raw coverage here would make a backfilled
        # report disagree with a freshly generated one about the same evidence.
        catalog_name = stored(excel_row, "catalog_name")
        catalog_score = _number(stored(excel_row, "catalog_confidence"))
        # The old 'method' field records which branch the mapper actually took,
        # which is better evidence than re-deriving it. Where it says catalog,
        # the catalog decided and no threshold of ours should overrule it.
        # Otherwise the catalog is incidental agreement, and has to clear the
        # same distinctiveness bar a live run applies - a two-digit CST covers
        # its table completely and still says little, because the same codes
        # appear across PIS, COFINS and IPI.
        if catalog_name not in (None, ""):
            if str(stored(excel_row, "method")).strip() == "catalog" or (
                catalog_score >= DISTINCTIVE_CATALOG_SCORE
            ):
                steps.append("catalog")
            elif catalog_score > 0:
                corroborating.append("catalog")

        # Recomputed, not read: trios come from the client's own columns, so
        # this works even where the report has no arithmetic_proof column.
        proof = _trio_proof_text(trio_corroboration(trios, str(model_column), client_index))
        if proof and "does not single out" not in proof:
            steps.append("arithmetic")
            tally["arithmetic"] += 1

        # Something objective beat generic shape similarity. If the catalog
        # score accounts for it exactly, that is the explanation and there is
        # nothing unrecorded - the two are the same number because the catalog
        # is what produced it.
        data_score = _number(stored(excel_row, "data_confidence"))
        explained_by_catalog = catalog_score > 0 and abs(data_score - catalog_score) < 1e-6
        objective_ran = data_score > GENERIC_PROFILE_CEILING
        if objective_ran and not explained_by_catalog and "catalog" not in steps and "arithmetic" not in steps:
            steps.append(UNRECORDED)
            tally[UNRECORDED] += 1
        elif explained_by_catalog and "catalog" not in steps:
            steps.append("catalog")
            if "catalog" in corroborating:
                corroborating.remove("catalog")

        column = client.columns[client_index]
        veto = ""
        if str(stored(excel_row, "status")).strip() != "accepted":
            if client_column_is_vacuous(client, column):
                veto = "vacuity"
            elif semantic_header_conflict(str(model_column), column.name) is not None:
                veto = "family_conflict"
        if veto:
            tally["vetoed"] += 1

        ranked = [step for step in steps if step != UNRECORDED]
        decided = deciding_step(ranked)
        if UNRECORDED in steps and decided in ("", header_step, "resolver"):
            # Something objective outranked the header match; we cannot say what.
            decided = UNRECORDED
        rendered = format_steps(ranked)
        for weak in corroborating:
            rendered = f"{rendered} + {weak} (agrees)" if rendered else f"{weak} (agrees)"
        if UNRECORDED in steps:
            rendered = f"{rendered} + {UNRECORDED}" if rendered else UNRECORDED

        sheet.cell(excel_row, anchor).value = decided
        sheet.cell(excel_row, anchor + 1).value = rendered
        sheet.cell(excel_row, anchor + 2).value = veto

    if not dry_run:
        book.save(report_path)
    book.close()
    return tally


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("report", help="mapping report to backfill in place")
    parser.add_argument("client", help="the client export it was produced from")
    parser.add_argument("--dry-run", action="store_true", help="report without writing")
    args = parser.parse_args(argv)

    tally = backfill(Path(args.report), Path(args.client), dry_run=args.dry_run)
    print(
        f"{args.report}: {tally['rows']} rows with a proposal, "
        f"{tally['arithmetic']} carrying arithmetic, "
        f"{tally[UNRECORDED]} {UNRECORDED}, {tally['vetoed']} vetoed"
        + ("  (dry run, nothing written)" if args.dry_run else "")
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
