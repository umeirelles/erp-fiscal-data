"""Create a model-shaped workbook from a client export and a mapping report."""

from __future__ import annotations

import argparse
import math
import sys
from datetime import time
from pathlib import Path
from typing import Sequence

import pandas as pd
from openpyxl.styles import Font, PatternFill
from openpyxl.utils import get_column_letter

from invoice_mapper import ColumnRef, SheetTable, file_sha256, semantic_header_conflict


MAPPING_METADATA = [
    ("client_column_number", "client_column_number"),
    ("client_column", "client_column"),
    ("status", "status"),
    ("method", "method"),
    ("name_confidence", "name_confidence"),
    ("name_runner_up_confidence", "name_runner_up_confidence"),
    ("name_margin", "name_margin"),
    ("data_confidence", "data_confidence"),
    ("llm_data_confidence", "llm_data_confidence"),
    ("llm_final_confidence", "llm_final_confidence"),
    ("catalog_name", "catalog_name"),
    ("catalog_coverage", "catalog_coverage"),
    ("catalog_confidence", "catalog_confidence"),
    ("final_confidence", "final_confidence"),
    ("decided_by", "decided_by"),
    ("supported_by", "supported_by"),
    ("vetoed_by", "vetoed_by"),
    ("arithmetic_proof", "arithmetic_proof"),
    ("reason", "reason"),
    ("reviewer_status", "reviewer_status"),
    ("reviewer_note", "reviewer_note"),
]

#: Excel row where client data starts: header, then the metadata banner.
#: Exported because this offset has already moved twice - 17 to 19 when the
#: LLM audit scores were added, and again here - and anything downstream that
#: hardcodes it breaks silently.
FIRST_DATA_EXCEL_ROW = len(MAPPING_METADATA) + 2

CONFIDENCE_ROWS = {
    "name_confidence",
    "name_runner_up_confidence",
    "name_margin",
    "data_confidence",
    "llm_data_confidence",
    "llm_final_confidence",
    "catalog_coverage",
    "catalog_confidence",
    "final_confidence",
}

REQUIRED_MAPPING_COLUMNS = {
    "model_column_number",
    "model_column",
    "client_column_number",
    "client_column",
    "name_confidence",
    "name_runner_up_confidence",
    "name_margin",
    "data_confidence",
    "final_confidence",
    "method",
    "status",
    "reason",
}

REVIEW_STATUSES = {"", "correct", "incorrect", "no_mapping_exists"}


def _sheet_argument(value: str) -> str | int:
    return int(value) if value.isdigit() else value


def _clean_text(value: object) -> str:
    return "" if pd.isna(value) else str(value).strip()


def _column_from_number(client: SheetTable, value: object) -> ColumnRef | None:
    if pd.isna(value) or _clean_text(value) == "":
        return None
    number = float(value)
    if not number.is_integer():
        raise ValueError(f"Client column number must be an integer, got {value!r}")
    index = int(number) - 1
    if index < 0 or index >= len(client.columns):
        raise ValueError(
            f"Client column number {int(number)} is outside 1..{len(client.columns)}"
        )
    return client.columns[index]


def _corrected_column(client: SheetTable, value: object) -> ColumnRef:
    text = _clean_text(value)
    if not text:
        raise ValueError("reviewer_status is incorrect but correct_client_column is blank")

    try:
        number = float(text)
    except ValueError:
        number = math.nan
    if not math.isnan(number):
        column = _column_from_number(client, number)
        if column is None:
            raise ValueError("Corrected client column cannot be blank")
        return column

    display_matches = [
        column for column in client.columns if column.display_name.casefold() == text.casefold()
    ]
    if len(display_matches) == 1:
        return display_matches[0]

    name_matches = [
        column for column in client.columns if column.name.casefold() == text.casefold()
    ]
    if len(name_matches) == 1:
        return name_matches[0]
    if len(name_matches) > 1:
        raise ValueError(
            f"Corrected header {text!r} is duplicated; use its 1-based column number "
            "or occurrence display name"
        )
    raise ValueError(f"Corrected client column {text!r} was not found")


def _ordered_mapping(mapping_path: str | Path, model: SheetTable) -> pd.DataFrame:
    mapping = pd.read_excel(mapping_path, dtype=object)
    missing = REQUIRED_MAPPING_COLUMNS - set(mapping.columns)
    if missing:
        raise ValueError(f"Mapping report is missing columns: {sorted(missing)}")

    mapping = mapping.copy()
    mapping["model_column_number"] = pd.to_numeric(
        mapping["model_column_number"], errors="raise"
    ).astype(int)
    if mapping["model_column_number"].duplicated().any():
        raise ValueError("Mapping report contains duplicate model_column_number values")

    expected_numbers = list(range(1, len(model.columns) + 1))
    actual_numbers = sorted(mapping["model_column_number"].tolist())
    if actual_numbers != expected_numbers:
        raise ValueError(
            f"Mapping report must contain every model column 1..{len(model.columns)} exactly once"
        )

    mapping = mapping.set_index("model_column_number").loc[expected_numbers].reset_index()
    for row, model_column in zip(mapping.to_dict("records"), model.columns, strict=True):
        if _clean_text(row["model_column"]) != model_column.name:
            raise ValueError(
                f"Model column {model_column.index + 1} differs between model and mapping: "
                f"{model_column.name!r} != {_clean_text(row['model_column'])!r}"
            )

    for optional in [
        # Reports written before these columns existed must still apply.
        "decided_by",
        "supported_by",
        "vetoed_by",
        "arithmetic_proof",
        "catalog_name",
        "catalog_coverage",
        "catalog_confidence",
        "llm_data_confidence",
        "llm_final_confidence",
        "reviewer_status",
        "correct_client_column",
        "reviewer_note",
    ]:
        if optional not in mapping.columns:
            mapping[optional] = ""
    return mapping


def _run_metadata(mapping_path: str | Path) -> dict[str, str]:
    workbook = pd.ExcelFile(mapping_path)
    if "Run Metadata" not in workbook.sheet_names:
        return {}
    frame = pd.read_excel(workbook, sheet_name="Run Metadata", dtype=object)
    if not {"key", "value"} <= set(frame.columns):
        raise ValueError("Run Metadata sheet must contain key and value columns")
    if frame["key"].duplicated().any():
        raise ValueError("Run Metadata contains duplicate keys")
    return {
        _clean_text(row["key"]): _clean_text(row["value"])
        for row in frame.to_dict("records")
        if _clean_text(row["key"])
    }


def _validate_input_hashes(
    metadata: dict[str, str],
    *,
    client_path: str | Path,
    model_path: str | Path,
) -> None:
    for key, path in (
        ("client_sha256", client_path),
        ("model_sha256", model_path),
    ):
        expected = metadata.get(key)
        if expected and expected != file_sha256(path):
            raise ValueError(
                f"{Path(path).name} differs from the file used to generate the mapping report"
            )


def _effective_columns(
    mapping: pd.DataFrame,
    client: SheetTable,
    model: SheetTable,
    accepted_only: bool,
) -> list[ColumnRef | None]:
    selected: list[ColumnRef | None] = []
    used: set[int] = set()

    for row, model_column in zip(
        mapping.to_dict("records"), model.columns, strict=True
    ):
        reviewer_status = _clean_text(row["reviewer_status"]).casefold()
        if reviewer_status not in REVIEW_STATUSES:
            raise ValueError(
                f"Unknown reviewer_status {row['reviewer_status']!r} for "
                f"model column {row['model_column']!r}"
            )

        # A correction is only ever consumed on the ``incorrect`` branch. On
        # every other branch it is silently discarded, and on ``correct`` that
        # discard is worse than a dropped answer: the row still ships, carrying
        # the *mapper's* column rather than the reviewer's, so a reviewer who
        # mis-picks the status puts wrong client data into the base model
        # instead of leaving it blank. The mirror mistake - ``incorrect`` with
        # a blank correction - has always raised. Make the pairing symmetric.
        correction = _clean_text(row["correct_client_column"])
        if correction and reviewer_status != "incorrect":
            raise ValueError(
                f"correct_client_column {correction!r} is set on model column "
                f"{row['model_column']!r} but reviewer_status is "
                f"{row['reviewer_status']!r}. Only 'incorrect' consumes a "
                "correction; any other status would discard it silently."
            )

        confirmed = False
        if reviewer_status == "no_mapping_exists":
            column = None
        elif reviewer_status == "incorrect":
            column = _corrected_column(client, row["correct_client_column"])
            confirmed = True
        elif reviewer_status == "correct":
            column = _column_from_number(client, row["client_column_number"])
            # 'correct' asserts the proposal is right, so there must be a
            # proposal. Without this the row is confirmed onto nothing and
            # ships a blank column under a reviewer's signature, exit code 0.
            if column is None:
                raise ValueError(
                    f"reviewer_status is 'correct' for model column "
                    f"{row['model_column']!r} but client_column_number is blank. "
                    "Use 'no_mapping_exists' to confirm that no counterpart "
                    "exists, or 'incorrect' with correct_client_column."
                )
            confirmed = True
        elif accepted_only and reviewer_status != "correct" and row["status"] != "accepted":
            column = None
        else:
            column = _column_from_number(client, row["client_column_number"])
            confirmed = row["status"] == "accepted"

        if (
            column is not None
            and row["status"] == "accepted"
            and reviewer_status == ""
        ):
            conflict = semantic_header_conflict(model_column.name, column.name)
            if conflict is not None:
                raise ValueError(
                    f"Accepted mapping {model_column.name!r} -> {column.display_name!r} "
                    f"violates current invariants: {conflict}. Review it explicitly before applying."
                )

        proposed = _column_from_number(client, row["client_column_number"])
        proposed_name = _clean_text(row["client_column"])
        if proposed is not None and proposed_name and proposed.display_name != proposed_name:
            raise ValueError(
                f"Mapping report does not match the client workbook at column "
                f"{proposed.index + 1}: {proposed_name!r} != {proposed.display_name!r}"
            )

        # Unconfirmed review proposals may intentionally reference the same source
        # column. Only accepted or reviewer-confirmed mappings must be one-to-one.
        if column is not None and confirmed:
            if column.index in used:
                raise ValueError(
                    f"Client column {column.display_name!r} is mapped more than once after "
                    "reviewer corrections"
                )
            used.add(column.index)
        selected.append(column)
    return selected


#: What an empty model column means. Only the middle one is anybody's work:
#: "not in this client's export" is a complete and correct answer, and a run
#: that leaves most of the model empty can be entirely right.
STATE_DELIVERED = "delivered"
STATE_PENDING = "awaiting decision"
STATE_ABSENT = "not in this client's export"


def _write_coverage_sheet(
    book,
    *,
    model: SheetTable,
    selected: Sequence[ColumnRef | None],
    statuses: Sequence[str],
    mapping: pd.DataFrame,
) -> None:
    """An index of where every model column stands, sorted by what needs doing.

    The mapped sheet answers "is the delivered data right". It cannot answer
    "what is not here and should be", because every column it fills is accepted
    by construction. This sheet does, and it is the one to read first: a model
    column is delivered, awaiting a decision, or genuinely not something this
    client sends.
    """
    sheet = book.create_sheet("Coverage")
    headers = [
        "state", "model_column_number", "model_column", "excel_column",
        "client_column_number", "client_column", "final_confidence", "reason",
    ]
    sheet.append(headers)

    order = {STATE_DELIVERED: 0, STATE_PENDING: 1, STATE_ABSENT: 2}
    rows = []
    for index, (model_column, column) in enumerate(zip(model.columns, selected)):
        status = statuses[index] if index < len(statuses) else ""
        if column is not None:
            state = STATE_DELIVERED
        elif status == "review":
            state = STATE_PENDING
        else:
            state = STATE_ABSENT
        proposed = mapping["client_column_number"][index]
        proposed_name = mapping["client_column"][index]
        confidence = mapping["final_confidence"][index]
        rows.append((
            order[state], index + 1,
            [
                state, index + 1, model_column.name,
                get_column_letter(index + 2),
                "" if column is not None else ("" if pd.isna(proposed) else proposed),
                column.display_name if column is not None
                else ("" if pd.isna(proposed_name) else proposed_name),
                "" if pd.isna(confidence) else confidence,
                "" if pd.isna(mapping["reason"][index]) else mapping["reason"][index],
            ],
        ))
    for _, _, values in sorted(rows, key=lambda item: (item[0], item[1])):
        sheet.append(values)

    sheet.freeze_panes = "A2"
    sheet.auto_filter.ref = f"A1:H{sheet.max_row}"
    for letter, width in (("A", 26), ("B", 10), ("C", 34), ("D", 8),
                          ("E", 10), ("F", 30), ("G", 12), ("H", 78)):
        sheet.column_dimensions[letter].width = width
    header_fill = PatternFill(fill_type="solid", fgColor="E8F0FE")
    for cell in sheet[1]:
        cell.fill = header_fill
        cell.font = Font(bold=True)
    for row in sheet.iter_rows(min_row=2, min_col=7, max_col=7):
        row[0].number_format = "0.00%"


def build_mapped_workbook(
    client_path: str | Path,
    mapping_path: str | Path,
    output_path: str | Path,
    model_path: str | Path = "model.xlsx",
    model_sheet: str | int = 0,
    client_sheet: str | int = 0,
    model_header_row: int = 0,
    client_header_row: int = 0,
    accepted_only: bool = True,
) -> None:
    """Apply confirmed mappings and write metadata rows followed by client data."""
    metadata = _run_metadata(mapping_path)
    _validate_input_hashes(
        metadata,
        client_path=client_path,
        model_path=model_path,
    )
    model = SheetTable.from_excel(model_path, model_sheet, model_header_row)
    client = SheetTable.from_excel(client_path, client_sheet, client_header_row)
    mapping = _ordered_mapping(mapping_path, model)
    selected = _effective_columns(mapping, client, model, accepted_only)

    effective_numbers = [column.index + 1 if column is not None else "" for column in selected]
    effective_names = [column.display_name if column is not None else "" for column in selected]
    metadata_overrides = {
        "client_column_number": effective_numbers,
        "client_column": effective_names,
    }

    metadata_rows: list[list[object]] = []
    for label, mapping_field in MAPPING_METADATA:
        if mapping_field in metadata_overrides:
            values = metadata_overrides[mapping_field]
        else:
            values = ["" if pd.isna(value) else value for value in mapping[mapping_field]]
        metadata_rows.append([label, *values])

    row_count = len(client.data)
    mapped_series = []
    for column in selected:
        if column is None:
            mapped_series.append(pd.Series([pd.NA] * row_count, dtype=object))
        else:
            mapped_series.append(client.series(column).reset_index(drop=True))
    mapped_data = pd.concat(mapped_series, axis=1)
    mapped_data.columns = [column.name for column in model.columns]
    mapped_data.insert(0, "__mapping_info__", "")

    output_columns = ["__mapping_info__", *[column.name for column in model.columns]]
    metadata_frame = pd.DataFrame(metadata_rows, columns=output_columns)
    output_frame = pd.concat([metadata_frame, mapped_data], axis=0, ignore_index=True)

    output = Path(output_path)
    if output.suffix.lower() != ".xlsx":
        raise ValueError("Output path must end in .xlsx")
    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        output_frame.to_excel(writer, sheet_name="Mapped Data", index=False)
        worksheet = writer.book["Mapped Data"]
        first_data_excel_row = FIRST_DATA_EXCEL_ROW
        worksheet.freeze_panes = f"B{first_data_excel_row}"

        metadata_fill = PatternFill(fill_type="solid", fgColor="E8F0FE")
        for excel_row in range(2, first_data_excel_row):
            for cell in worksheet[excel_row]:
                cell.fill = metadata_fill

        label_to_excel_row = {
            label: index + 2 for index, (label, _) in enumerate(MAPPING_METADATA)
        }
        for label in CONFIDENCE_ROWS:
            excel_row = label_to_excel_row[label]
            for excel_column in range(2, len(output_columns) + 1):
                worksheet.cell(excel_row, excel_column).number_format = "0.00%"

        # pandas serializes datetime.time objects as strings. Restore true Excel
        # time cells so mapped values retain their type and remain sortable.
        for model_index, column in enumerate(selected):
            if column is None:
                continue
            output_excel_column = model_index + 2
            for row_offset, value in enumerate(client.series(column)):
                if isinstance(value, time):
                    cell = worksheet.cell(
                        first_data_excel_row + row_offset,
                        output_excel_column,
                    )
                    cell.value = value
                    cell.number_format = "hh:mm:ss"

        worksheet.column_dimensions["A"].width = 34
        for excel_column in range(2, min(len(output_columns), 30) + 1):
            worksheet.column_dimensions[get_column_letter(excel_column)].width = 18

        # A client fills a minority of the model and that is the design, not a
        # shortfall - the model is a superset covering every field any client
        # might send. But it leaves the delivered columns scattered across 227:
        # datasul's 19 run from AI to GW with 101 empty columns between two of
        # them. Hiding the columns that carry no data puts them side by side.
        # Nothing is removed, no value changes, one click unhides, and anything
        # reading the file programmatically is unaffected.
        statuses = [
            _clean_text(value).casefold() for value in mapping["status"]
        ]
        delivered_fill = PatternFill(fill_type="solid", fgColor="D7E9DC")
        for model_index, column in enumerate(selected):
            letter = get_column_letter(model_index + 2)
            if column is None:
                worksheet.column_dimensions[letter].hidden = True
            else:
                worksheet.column_dimensions[letter].width = 18
                worksheet.cell(1, model_index + 2).fill = delivered_fill

        _write_coverage_sheet(
            writer.book, model=model, selected=selected, statuses=statuses,
            mapping=mapping,
        )

    print(f"Wrote {output}")
    print(
        f"model_columns={len(model.columns)} client_rows={row_count} "
        f"mapped_columns={sum(column is not None for column in selected)}"
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Apply a mapping report and create a model-shaped client workbook."
    )
    parser.add_argument("client", help="Client .xlsx export")
    parser.add_argument("mapping", help="Mapping report produced by invoice_mapper.py")
    parser.add_argument("--model", default="model.xlsx", help="Canonical model workbook")
    parser.add_argument("--output", required=True, help="Output .xlsx workbook")
    parser.add_argument("--model-sheet", default="0", help="Model sheet index or name")
    parser.add_argument("--client-sheet", default="0", help="Client sheet index or name")
    parser.add_argument(
        "--model-header-row", type=int, default=1, help="1-based model header row"
    )
    parser.add_argument(
        "--client-header-row", type=int, default=1, help="1-based client header row"
    )
    parser.add_argument(
        "--accepted-only",
        action="store_true",
        help="Deprecated: accepted and reviewer-confirmed mappings are already the default",
    )
    parser.add_argument(
        "--include-review",
        action="store_true",
        help="Also populate unconfirmed review proposals in an explicitly unsafe review workbook",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.accepted_only and args.include_review:
        raise SystemExit("--accepted-only and --include-review cannot be used together")
    if args.model_header_row < 1 or args.client_header_row < 1:
        raise SystemExit("--model-header-row and --client-header-row must be positive")
    try:
        build_mapped_workbook(
            client_path=args.client,
            mapping_path=args.mapping,
            output_path=args.output,
            model_path=args.model,
            model_sheet=_sheet_argument(args.model_sheet),
            client_sheet=_sheet_argument(args.client_sheet),
            model_header_row=args.model_header_row - 1,
            client_header_row=args.client_header_row - 1,
            accepted_only=not args.include_review,
        )
    except (ValueError, OSError) as exc:
        raise SystemExit(str(exc)) from exc
    return 0


if __name__ == "__main__":
    sys.exit(main())
