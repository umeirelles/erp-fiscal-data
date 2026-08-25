from __future__ import annotations

from datetime import time

import pandas as pd
import pytest
from openpyxl import load_workbook

from apply_invoice_mapping import MAPPING_METADATA, build_mapped_workbook


def write_workbook(path, headers: list[str], rows: list[list[object]]) -> None:
    pd.DataFrame([headers, *rows]).to_excel(path, header=False, index=False)


def mapping_rows() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "model_column_number": 1,
                "model_column": "CFOP",
                "client_column_number": 2,
                "client_column": "CFOP source",
                "name_confidence": 0.98,
                "name_runner_up_confidence": 0.70,
                "name_margin": 0.28,
                "data_confidence": None,
                "llm_data_confidence": None,
                "llm_final_confidence": None,
                "final_confidence": 0.98,
                "method": "name",
                "status": "accepted",
                "reason": "Clear header match.",
                "reviewer_status": "",
                "correct_client_column": "",
                "reviewer_note": "",
            },
            {
                "model_column_number": 2,
                "model_column": "Valor COFINS",
                "client_column_number": 3,
                "client_column": "COFI value",
                "name_confidence": 0.82,
                "name_runner_up_confidence": 0.80,
                "name_margin": 0.02,
                "data_confidence": 0.91,
                "llm_data_confidence": 0.93,
                "llm_final_confidence": 0.89,
                "final_confidence": 0.88,
                "method": "data",
                "status": "review",
                "reason": "Names and numeric values are compatible.",
                "reviewer_status": "",
                "correct_client_column": "",
                "reviewer_note": "Check this value field.",
            },
            {
                "model_column_number": 3,
                "model_column": "Nº do NCM",
                "client_column_number": None,
                "client_column": None,
                "name_confidence": 0.40,
                "name_runner_up_confidence": 0.38,
                "name_margin": 0.02,
                "data_confidence": 0.10,
                "llm_data_confidence": 0.12,
                "llm_final_confidence": 0.08,
                "final_confidence": 0.0,
                "method": "data",
                "status": "no_match",
                "reason": "No automatic match.",
                "reviewer_status": "incorrect",
                "correct_client_column": "Corrected NCM",
                "reviewer_note": "Reviewer supplied the source column.",
            },
        ]
    )


def test_build_mapped_workbook_defaults_to_confirmed_mappings(tmp_path) -> None:
    model_path = tmp_path / "model.xlsx"
    client_path = tmp_path / "client.xlsx"
    mapping_path = tmp_path / "mapping.xlsx"
    output_path = tmp_path / "mapped.xlsx"

    write_workbook(
        model_path,
        ["CFOP", "Valor COFINS", "Nº do NCM"],
        [["5102", 10.0, "85167910"]],
    )
    write_workbook(
        client_path,
        ["Other", "CFOP source", "COFI value", "Corrected NCM"],
        [["A", "5102", 12.5, "85167910"], ["B", "6102", 7.5, "73211100"]],
    )
    mapping_rows().to_excel(mapping_path, index=False)

    build_mapped_workbook(
        client_path=client_path,
        mapping_path=mapping_path,
        output_path=output_path,
        model_path=model_path,
    )

    raw = pd.read_excel(output_path, sheet_name="Mapped Data", header=None, dtype=object)
    assert raw.iloc[0].tolist() == [
        "__mapping_info__",
        "CFOP",
        "Valor COFINS",
        "Nº do NCM",
    ]
    assert raw.iloc[1 : len(MAPPING_METADATA) + 1, 0].tolist() == [
        label for label, _ in MAPPING_METADATA
    ]

    client_column_row = 1 + [label for label, _ in MAPPING_METADATA].index(
        "client_column"
    )
    assert raw.iloc[client_column_row, 1] == "CFOP source"
    assert pd.isna(raw.iloc[client_column_row, 2])
    assert raw.iloc[client_column_row, 3] == "Corrected NCM"

    first_data_row = len(MAPPING_METADATA) + 1
    assert raw.iloc[first_data_row, 1] == "5102"
    assert pd.isna(raw.iloc[first_data_row, 2])
    assert raw.iloc[first_data_row, 3] == "85167910"
    assert raw.iloc[first_data_row + 1, 1] == "6102"
    assert pd.isna(raw.iloc[first_data_row + 1, 2])
    assert raw.iloc[first_data_row + 1, 3] == "73211100"

    workbook = load_workbook(output_path)
    worksheet = workbook["Mapped Data"]
    assert worksheet.freeze_panes == f"B{len(MAPPING_METADATA) + 2}"
    confidence_excel_row = 2 + [label for label, _ in MAPPING_METADATA].index(
        "name_confidence"
    )
    assert worksheet.cell(confidence_excel_row, 2).number_format == "0.00%"
    llm_confidence_excel_row = 2 + [
        label for label, _ in MAPPING_METADATA
    ].index("llm_final_confidence")
    assert worksheet.cell(llm_confidence_excel_row, 3).value == 0.89
    assert worksheet.cell(llm_confidence_excel_row, 3).number_format == "0.00%"


def test_include_review_populates_unconfirmed_proposal(tmp_path) -> None:
    model_path = tmp_path / "model.xlsx"
    client_path = tmp_path / "client.xlsx"
    mapping_path = tmp_path / "mapping.xlsx"
    output_path = tmp_path / "mapped.xlsx"
    write_workbook(
        model_path,
        ["CFOP", "Valor COFINS", "Nº do NCM"],
        [["5102", 10.0, "85167910"]],
    )
    write_workbook(
        client_path,
        ["Other", "CFOP source", "COFI value", "Corrected NCM"],
        [["A", "5102", 12.5, "85167910"]],
    )
    mapping_rows().to_excel(mapping_path, index=False)

    build_mapped_workbook(
        client_path=client_path,
        mapping_path=mapping_path,
        output_path=output_path,
        model_path=model_path,
        accepted_only=False,
    )

    raw = pd.read_excel(output_path, sheet_name="Mapped Data", header=None, dtype=object)
    first_data_row = len(MAPPING_METADATA) + 1
    assert raw.iloc[first_data_row, 2] == 12.5


def test_include_review_allows_duplicate_unconfirmed_proposals(tmp_path) -> None:
    model_path = tmp_path / "model.xlsx"
    client_path = tmp_path / "client.xlsx"
    mapping_path = tmp_path / "mapping.xlsx"
    output_path = tmp_path / "mapped.xlsx"
    write_workbook(model_path, ["Base CBS", "Base IBS"], [[100.0, 100.0]])
    write_workbook(client_path, ["Generic base"], [[125.0]])
    mapping = mapping_rows().iloc[:2].copy()
    mapping["model_column_number"] = [1, 2]
    mapping["model_column"] = ["Base CBS", "Base IBS"]
    mapping["client_column_number"] = [1, 1]
    mapping["client_column"] = ["Generic base", "Generic base"]
    mapping["status"] = ["review", "review"]
    mapping["reviewer_status"] = ["", ""]
    mapping.to_excel(mapping_path, index=False)

    build_mapped_workbook(
        client_path=client_path,
        mapping_path=mapping_path,
        output_path=output_path,
        model_path=model_path,
        accepted_only=False,
    )

    raw = pd.read_excel(output_path, sheet_name="Mapped Data", header=None, dtype=object)
    first_data_row = len(MAPPING_METADATA) + 1
    assert raw.iloc[first_data_row, 1:].tolist() == [125, 125]


def test_mapped_workbook_preserves_excel_time_values(tmp_path) -> None:
    model_path = tmp_path / "model.xlsx"
    client_path = tmp_path / "client.xlsx"
    mapping_path = tmp_path / "mapping.xlsx"
    output_path = tmp_path / "mapped.xlsx"
    write_workbook(model_path, ["Criado às:"], [[time(8, 0, 0)]])
    write_workbook(client_path, ["Created at"], [[None]])
    client_workbook = load_workbook(client_path)
    client_workbook.active["A2"] = time(15, 30, 11)
    client_workbook.active["A2"].number_format = "hh:mm:ss"
    client_workbook.save(client_path)
    mapping = mapping_rows().iloc[[0]].copy()
    mapping["model_column"] = "Criado às:"
    mapping["client_column_number"] = 1
    mapping["client_column"] = "Created at"
    mapping.to_excel(mapping_path, index=False)

    build_mapped_workbook(
        client_path=client_path,
        mapping_path=mapping_path,
        output_path=output_path,
        model_path=model_path,
    )

    worksheet = load_workbook(output_path, data_only=True)["Mapped Data"]
    cell = worksheet.cell(len(MAPPING_METADATA) + 2, 2)
    assert cell.value == time(15, 30, 11)
    assert cell.number_format == "hh:mm:ss"


def test_accepted_only_leaves_unreviewed_review_mapping_blank(tmp_path) -> None:
    model_path = tmp_path / "model.xlsx"
    client_path = tmp_path / "client.xlsx"
    mapping_path = tmp_path / "mapping.xlsx"
    output_path = tmp_path / "mapped.xlsx"
    write_workbook(
        model_path,
        ["CFOP", "Valor COFINS", "Nº do NCM"],
        [["5102", 10.0, "85167910"]],
    )
    write_workbook(
        client_path,
        ["Other", "CFOP source", "COFI value", "Corrected NCM"],
        [["A", "5102", 12.5, "85167910"]],
    )
    mapping = mapping_rows()
    mapping.loc[2, "reviewer_status"] = "no_mapping_exists"
    # The fixture row carries a correction for the 'incorrect' status it had.
    # Only 'incorrect' consumes one, so it has to go with the status.
    mapping.loc[2, "correct_client_column"] = ""
    mapping.to_excel(mapping_path, index=False)

    build_mapped_workbook(
        client_path=client_path,
        mapping_path=mapping_path,
        output_path=output_path,
        model_path=model_path,
        accepted_only=True,
    )

    raw = pd.read_excel(output_path, sheet_name="Mapped Data", header=None, dtype=object)
    first_data_row = len(MAPPING_METADATA) + 1
    assert raw.iloc[first_data_row, 1] == "5102"
    assert pd.isna(raw.iloc[first_data_row, 2])
    assert pd.isna(raw.iloc[first_data_row, 3])


def test_incorrect_review_requires_a_corrected_column(tmp_path) -> None:
    model_path = tmp_path / "model.xlsx"
    client_path = tmp_path / "client.xlsx"
    mapping_path = tmp_path / "mapping.xlsx"
    output_path = tmp_path / "mapped.xlsx"
    write_workbook(model_path, ["CFOP", "Valor COFINS", "Nº do NCM"], [[1, 2, 3]])
    write_workbook(
        client_path,
        ["Other", "CFOP source", "COFI value", "Corrected NCM"],
        [["A", "5102", 12.5, "85167910"]],
    )
    mapping = mapping_rows()
    mapping.loc[2, "correct_client_column"] = ""
    mapping.to_excel(mapping_path, index=False)

    with pytest.raises(ValueError, match="correct_client_column is blank"):
        build_mapped_workbook(
            client_path=client_path,
            mapping_path=mapping_path,
            output_path=output_path,
            model_path=model_path,
        )


def test_unreviewed_accepted_semantic_conflict_is_rejected(tmp_path) -> None:
    model_path = tmp_path / "model.xlsx"
    client_path = tmp_path / "client.xlsx"
    mapping_path = tmp_path / "mapping.xlsx"
    output_path = tmp_path / "mapped.xlsx"
    write_workbook(model_path, ["Base ICMS_ST"], [[100.0]])
    write_workbook(client_path, ["ICMS Base"], [[100.0]])
    mapping = mapping_rows().iloc[[0]].copy()
    mapping["model_column"] = "Base ICMS_ST"
    mapping["client_column_number"] = 1
    mapping["client_column"] = "ICMS Base"
    mapping.to_excel(mapping_path, index=False)

    with pytest.raises(ValueError, match="violates current invariants"):
        build_mapped_workbook(
            client_path=client_path,
            mapping_path=mapping_path,
            output_path=output_path,
            model_path=model_path,
        )


def test_mapping_run_metadata_detects_changed_inputs(tmp_path) -> None:
    model_path = tmp_path / "model.xlsx"
    client_path = tmp_path / "client.xlsx"
    mapping_path = tmp_path / "mapping.xlsx"
    output_path = tmp_path / "mapped.xlsx"
    write_workbook(model_path, ["CFOP"], [["5102"]])
    write_workbook(client_path, ["CFOP source"], [["5102"]])
    mapping = mapping_rows().iloc[[0]].copy()
    mapping["client_column_number"] = 1
    mapping["client_column"] = "CFOP source"
    with pd.ExcelWriter(mapping_path, engine="openpyxl") as writer:
        mapping.to_excel(writer, sheet_name="Mappings", index=False)
        pd.DataFrame(
            [["client_sha256", "not-the-current-hash"]],
            columns=["key", "value"],
        ).to_excel(writer, sheet_name="Run Metadata", index=False)

    with pytest.raises(ValueError, match="differs from the file used"):
        build_mapped_workbook(
            client_path=client_path,
            mapping_path=mapping_path,
            output_path=output_path,
            model_path=model_path,
        )


def test_a_correction_outside_the_incorrect_branch_is_refused(tmp_path) -> None:
    # Only the 'incorrect' branch reads correct_client_column. On 'correct' the
    # row still ships, carrying the mapper's column rather than the reviewer's,
    # so a mis-picked status put WRONG client data into the base model instead
    # of leaving it blank - and exited 0.
    model_path = tmp_path / "model.xlsx"
    client_path = tmp_path / "client.xlsx"
    mapping_path = tmp_path / "mapping.xlsx"
    write_workbook(model_path, ["CFOP", "Valor COFINS", "Nº do NCM"], [["5102", 10.0, "85167910"]])
    write_workbook(client_path, ["Other", "CFOP source", "COFI value", "Corrected NCM"],
                   [["A", "5102", 12.5, "85167910"]])
    for status in ("correct", "no_mapping_exists", ""):
        mapping = mapping_rows()
        mapping.loc[0, "reviewer_status"] = status
        mapping.loc[0, "correct_client_column"] = 4
        mapping.to_excel(mapping_path, index=False)
        with pytest.raises(ValueError, match="Only 'incorrect' consumes a correction"):
            build_mapped_workbook(
                client_path=client_path,
                mapping_path=mapping_path,
                output_path=tmp_path / "out.xlsx",
                model_path=model_path,
            )


def test_confirming_a_row_that_names_no_column_is_refused(tmp_path) -> None:
    # 'correct' asserts the proposal is right, so there must be a proposal.
    # Without the guard the row is confirmed onto nothing and ships a blank
    # column under a reviewer's signature, exit code 0.
    model_path = tmp_path / "model.xlsx"
    client_path = tmp_path / "client.xlsx"
    mapping_path = tmp_path / "mapping.xlsx"
    write_workbook(model_path, ["CFOP", "Valor COFINS", "Nº do NCM"], [["5102", 10.0, "85167910"]])
    write_workbook(client_path, ["Other", "CFOP source", "COFI value", "Corrected NCM"],
                   [["A", "5102", 12.5, "85167910"]])
    mapping = mapping_rows()
    mapping.loc[2, "reviewer_status"] = "correct"
    mapping.loc[2, "correct_client_column"] = ""
    mapping.to_excel(mapping_path, index=False)
    with pytest.raises(ValueError, match="client_column_number is blank"):
        build_mapped_workbook(
            client_path=client_path,
            mapping_path=mapping_path,
            output_path=tmp_path / "out.xlsx",
            model_path=model_path,
        )


def test_columns_with_no_data_are_hidden_not_removed(tmp_path) -> None:
    # A client fills a minority of the model and that is the design. But it
    # leaves the delivered columns scattered: datasul's 19 run from AI to GW
    # with 101 empty columns between two of them. Hiding puts them side by
    # side without removing anything or changing a value.
    import openpyxl
    from openpyxl.utils import get_column_letter
    model_path = tmp_path / "model.xlsx"
    client_path = tmp_path / "client.xlsx"
    mapping_path = tmp_path / "mapping.xlsx"
    output_path = tmp_path / "mapped.xlsx"
    write_workbook(model_path, ["CFOP", "Valor COFINS", "Nº do NCM"], [["5102", 10.0, "85167910"]])
    write_workbook(client_path, ["Other", "CFOP source", "COFI value", "Corrected NCM"],
                   [["A", "5102", 12.5, "85167910"]])
    mapping_rows().to_excel(mapping_path, index=False)
    build_mapped_workbook(client_path=client_path, mapping_path=mapping_path,
                          output_path=output_path, model_path=model_path)
    book = openpyxl.load_workbook(output_path)
    sheet = book["Mapped Data"]
    hidden = {
        get_column_letter(c): sheet.column_dimensions[get_column_letter(c)].hidden
        for c in range(2, 5)
    }
    # The fixture delivers two: 'CFOP' accepted, and 'Nº do NCM' via a
    # reviewer correction - which is the whole point of the review loop, and
    # is how sap_procosa gets 62 delivered from 45 accepts. Only the
    # unconfirmed 'Valor COFINS' review row carries no data.
    assert hidden["B"] is False
    assert hidden["C"] is True
    assert hidden["D"] is False
    # hiding must not remove anything - all three model columns still there
    header = next(sheet.iter_rows(max_row=1, values_only=True))
    assert list(header[1:4]) == ["CFOP", "Valor COFINS", "Nº do NCM"]


def test_coverage_sheet_separates_absence_from_a_pending_decision(tmp_path) -> None:
    # The mapped sheet cannot answer "what is not here and should be", because
    # every column it fills is accepted by construction. An empty column means
    # either the client has no such concept - a complete answer - or that a
    # candidate is waiting on a reviewer. Only the second is work.
    import openpyxl
    model_path = tmp_path / "model.xlsx"
    client_path = tmp_path / "client.xlsx"
    mapping_path = tmp_path / "mapping.xlsx"
    output_path = tmp_path / "mapped.xlsx"
    write_workbook(model_path, ["CFOP", "Valor COFINS", "Nº do NCM"], [["5102", 10.0, "85167910"]])
    write_workbook(client_path, ["Other", "CFOP source", "COFI value", "Corrected NCM"],
                   [["A", "5102", 12.5, "85167910"]])
    mapping_rows().to_excel(mapping_path, index=False)
    build_mapped_workbook(client_path=client_path, mapping_path=mapping_path,
                          output_path=output_path, model_path=model_path)
    book = openpyxl.load_workbook(output_path)
    assert "Coverage" in book.sheetnames
    rows = list(book["Coverage"].values)
    header = list(rows[0])
    data = [dict(zip(header, r)) for r in rows[1:]]
    states = [r["state"] for r in data]
    # sorted so the work comes first: delivered, then pending, then absence
    assert states == ["delivered", "delivered", "awaiting decision"]
    assert data[0]["model_column"] == "CFOP" and data[0]["excel_column"] == "B"
    # a reviewer correction counts as delivered and names the column it landed on
    assert data[1]["model_column"] == "Nº do NCM"
    assert data[1]["client_column"] == "Corrected NCM"
    # the pending row must still name the candidate a reviewer has to rule on
    assert data[2]["client_column"] == "COFI value"
