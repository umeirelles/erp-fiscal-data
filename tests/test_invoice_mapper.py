from __future__ import annotations

from dataclasses import dataclass
from types import SimpleNamespace

import numpy as np
import pandas as pd

import pytest

from invoice_mapper import (
    MappingResult,
    summarize_unmatched_columns,
    _MIN_ROLE_PREFIX,
    _ROLE_CONCEPT_WORDS,
    expand_truncated_role_words,
    trio_corroboration,
    discover_client_tax_trios,
    _trio_proof_text,
    summarize_adjudication_queue,
    MappingResult,
    client_column_is_vacuous,
    ColumnRef,
    DataMappingDecision,
    MapperConfig,
    OpenAIDataResolver,
    OpenAIEmbeddingHeaderScorer,
    SheetTable,
    LexicalHeaderScorer,
    header_semantic_role,
    header_tax_families,
    map_columns,
    line_calculation_evidence,
    profile_similarity,
    sample_values,
    semantic_header_conflict,
    semantic_header_signature,
    summarize_values,
    tax_calculation_evidence,
    underspecified_header_match,
    write_results,
)


def table(headers: list[str], rows: list[list[object]]) -> SheetTable:
    occurrences: dict[str, int] = {}
    columns: list[ColumnRef] = []
    for index, name in enumerate(headers):
        occurrences[name] = occurrences.get(name, 0) + 1
        columns.append(
            ColumnRef(
                id=f"column_{index + 1:03d}",
                index=index,
                name=name,
                occurrence=occurrences[name],
            )
        )
    return SheetTable(columns=columns, data=pd.DataFrame(rows))


@dataclass
class FixedScorer:
    matrix: np.ndarray

    def scores(self, model_headers, client_headers) -> np.ndarray:
        return self.matrix


class FixedResolver:
    def __init__(self, decisions: dict[str, DataMappingDecision]) -> None:
        self.decisions = decisions
        self.calls: list[str] = []

    def resolve(
        self,
        model_column,
        model_values,
        candidates,
        sample_size,
        relationship_evidence=None,
        catalog_evidence=None,
    ):
        self.calls.append(model_column.name)
        return self.decisions[model_column.name]


def test_high_confidence_name_mapping_is_not_sent_to_data_pass() -> None:
    model = table(["CFOP", "Base CBS"], [["5102", 100.0]])
    client = table(["CFOP", "CBS basis"], [["5102", 100.0]])
    scorer = FixedScorer(np.array([[1.0, 0.1], [0.1, 0.75]]))
    resolver = FixedResolver(
        {
            "Base CBS": DataMappingDecision(
                client_column_id="column_002",
                data_confidence=0.98,
                final_confidence=0.95,
                reason="Both contain compatible tax-base values.",
            )
        }
    )

    results = map_columns(
        model,
        client,
        scorer,
        resolver,
        MapperConfig(name_threshold=0.80),
    )

    assert results[0].client_column == "CFOP"
    assert results[0].method == "name"
    assert results[0].name_margin == 0.9
    assert results[1].client_column == "CBS basis"
    assert results[1].method == "data"
    assert resolver.calls == ["Base CBS"]


def test_data_pass_can_reject_all_candidates() -> None:
    model = table(["Chave de acesso"], [["35260112345678000123550010000000011000000010"]])
    client = table(["Fornecedor"], [["Empresa Ltda"]])
    scorer = FixedScorer(np.array([[0.20]]))
    resolver = FixedResolver(
        {
            "Chave de acesso": DataMappingDecision(
                client_column_id=None,
                data_confidence=0.05,
                final_confidence=0.05,
                reason="Names and value formats are incompatible.",
            )
        }
    )

    result = map_columns(model, client, scorer, resolver)[0]

    assert result.client_column is None
    assert result.status == "no_match"
    assert result.final_confidence == 0.0


def test_zero_confidence_candidate_is_treated_as_no_match() -> None:
    model = table(["Nº do NCM"], [["85167910"]])
    client = table(["MODALIDADE NF"], [["ENTRADA"]])
    scorer = FixedScorer(np.array([[0.30]]))
    resolver = FixedResolver(
        {
            "Nº do NCM": DataMappingDecision(
                client_column_id="column_001",
                data_confidence=0.0,
                final_confidence=0.0,
                reason="The values are incompatible.",
            )
        }
    )

    result = map_columns(model, client, scorer, resolver)[0]

    assert result.client_column is None
    assert result.status == "no_match"


def test_selected_client_column_is_not_reused() -> None:
    model = table(["Base ICMS", "Base IPI"], [[100.0, 50.0]])
    client = table(["Tax base", "IPI amount"], [[100.0, 5.0]])
    scorer = FixedScorer(np.array([[0.90, 0.10], [0.85, 0.70]]))

    results = map_columns(model, client, scorer, data_resolver=None)

    assert results[0].client_column == "Tax base"
    assert results[1].client_column == "IPI amount"


def test_review_proposal_does_not_reserve_source_column() -> None:
    model = table(
        ["Tipo de Imposto CBS", "Base CBS"],
        [["12345678", 100.0], ["87654321", 250.0]],
    )
    client = table(["CBS Base"], [["12345678"], ["87654321"]])
    scorer = FixedScorer(np.array([[0.89], [0.88]]))
    resolver = FixedResolver(
        {
            "Tipo de Imposto CBS": DataMappingDecision(
                client_column_id="column_001",
                data_confidence=0.95,
                final_confidence=0.95,
                reason="The values are numeric.",
            ),
            "Base CBS": DataMappingDecision(
                client_column_id="column_001",
                data_confidence=0.98,
                final_confidence=0.95,
                reason="The values are compatible tax bases.",
            ),
        }
    )

    results = map_columns(model, client, scorer, resolver)

    assert results[0].client_column == "CBS Base"
    assert results[0].status == "review"
    assert "semantic-role conflict" in results[0].reason
    assert results[1].client_column == "CBS Base"
    assert results[1].status == "review"
    assert resolver.calls == ["Tipo de Imposto CBS", "Base CBS"]


def test_generic_numeric_evidence_cannot_rescue_explicit_semantic_conflict() -> None:
    model = table(["Base IBS"], [[100.0], [250.0]])
    client = table(["IBSS Rate"], [[0.1], [0.2]])
    scorer = FixedScorer(np.array([[1.0]]))
    resolver = FixedResolver(
        {
            "Base IBS": DataMappingDecision(
                client_column_id="column_001",
                data_confidence=0.99,
                final_confidence=0.99,
                reason="Both columns are numeric.",
            )
        }
    )

    result = map_columns(model, client, scorer, resolver)[0]

    assert resolver.calls == ["Base IBS"]
    assert result.client_column is None
    assert result.status == "no_match"
    assert "distinctive row evidence" in result.reason


def test_semantic_header_conflict_is_generic_and_conservative() -> None:
    assert semantic_header_conflict("Base IBS", "IBSS Rate") == (
        "semantic-role conflict (base vs rate)"
    )
    assert semantic_header_conflict("Valor ICMS", "IPI Value") == (
        "tax-family conflict (icms vs ipi)"
    )
    assert semantic_header_conflict("Base ICMS", "ICMS Mont.base excl.") == (
        "semantic-role conflict (base vs exempt)"
    )
    assert semantic_header_conflict("CST PIS", "PIS Mont.") == (
        "semantic-role conflict (tax_status vs base)"
    )
    assert semantic_header_conflict(
        "Tipo de Imposto CBS", "Tax Classification Code for CBS and IBS"
    ) == "semantic-role conflict (tax_status vs tax_classification)"
    assert semantic_header_conflict("Nº item do documento", "SAP Nº do NF") == (
        "semantic-role conflict (item_id vs document_id)"
    )
    assert semantic_header_conflict("Aliquota ICMS_ST", "ICMS Taxa") == (
        "tax-qualifier conflict (st vs regular)"
    )
    assert semantic_header_conflict("Grupo de imposto", "Grupo de mercadorias") == (
        "semantic-role conflict (tax_group vs product_group)"
    )
    assert semantic_header_conflict("Chave de acesso", "X9") is None


def test_one_word_subset_header_is_not_decisive_by_itself() -> None:
    assert underspecified_header_match("Centro custo", "Centro")
    assert underspecified_header_match("Base ICMS", "ICMS")
    assert not underspecified_header_match("Centro custo", "Cost center")
    assert not underspecified_header_match("Empresa", "Company Name")


def test_duplicate_headers_keep_unique_column_ids(tmp_path) -> None:
    path = tmp_path / "duplicates.xlsx"
    pd.DataFrame([["Document Number", "Document Number"], [1, 2]]).to_excel(
        path, header=False, index=False
    )

    sheet = SheetTable.from_excel(path)

    assert [column.id for column in sheet.columns] == ["column_001", "column_002"]
    assert sheet.columns[0].display_name == "Document Number"
    assert sheet.columns[1].display_name == "Document Number (occurrence 2)"


def test_mapping_report_contains_reviewer_columns(tmp_path) -> None:
    model = table(["CFOP"], [["5102"]])
    client = table(["CFOP"], [["5102"]])
    result = map_columns(model, client, LexicalHeaderScorer(), data_resolver=None)
    output = tmp_path / "mapping.xlsx"

    write_results(result, output)

    columns = pd.read_excel(output, nrows=0).columns.tolist()
    assert columns[-3:] == [
        "reviewer_status",
        "correct_client_column",
        "reviewer_note",
    ]


def test_mapping_report_writes_run_metadata(tmp_path) -> None:
    model = table(["CFOP"], [["5102"]])
    client = table(["CFOP"], [["5102"]])
    result = map_columns(model, client, LexicalHeaderScorer(), data_resolver=None)
    output = tmp_path / "mapping.xlsx"

    write_results(result, output, metadata={"mapping_schema_version": 2})

    workbook = pd.ExcelFile(output)
    assert workbook.sheet_names == ["Mappings", "Run Metadata"]
    metadata = pd.read_excel(workbook, sheet_name="Run Metadata")
    assert metadata.to_dict("records") == [
        {"key": "mapping_schema_version", "value": 2}
    ]


def test_sheet_table_supports_an_explicit_header_row(tmp_path) -> None:
    path = tmp_path / "preamble.xlsx"
    pd.DataFrame(
        [["Invoice export", None], ["CFOP", "Amount"], ["5102", 100.0]]
    ).to_excel(path, header=False, index=False)

    parsed = SheetTable.from_excel(path, header_row=1)

    assert [column.name for column in parsed.columns] == ["CFOP", "Amount"]
    assert parsed.data.to_dict("records") == [{0: "5102", 1: 100}]


def test_sample_values_preserves_strings_and_removes_duplicates() -> None:
    values = pd.Series([None, "", "000051789", "000051789", "001", 12.5])

    assert sample_values(values, limit=3) == ["000051789", "001", "12.5"]


def test_sample_values_are_independent_of_row_order() -> None:
    values = pd.Series([0, 100, 25, 50, 75, 100, 125, 150, 175, 200])

    assert sample_values(values, limit=6) == sample_values(
        values.iloc[::-1].reset_index(drop=True), limit=6
    )


def test_semantic_header_signature_handles_translation_and_word_order() -> None:
    assert semantic_header_signature("Valor COFINS") == semantic_header_signature(
        "COFINS Value"
    )
    assert semantic_header_signature("Data de lançamento") == semantic_header_signature(
        "Posting date"
    )
    assert semantic_header_signature("Unidade de medida") == semantic_header_signature(
        "Unit of measure"
    )
    assert semantic_header_signature("ID parceiro") == semantic_header_signature(
        "Partner ID"
    )
    assert semantic_header_signature("Chave de acesso") == semantic_header_signature(
        "44-Digit Access Key"
    )
    assert semantic_header_signature("CST COFINS") == semantic_header_signature(
        "Tax Situation COFI"
    )
    assert semantic_header_signature("CST PIS") == semantic_header_signature(
        "Tax Situation PI"
    )
    assert semantic_header_signature("Ctg. nota fiscal") == semantic_header_signature(
        "Nota fiscal type"
    )
    assert semantic_header_signature("Valor líquido") == semantic_header_signature(
        "Net value"
    )
    assert semantic_header_signature("Valor IBS") == semantic_header_signature(
        "IBSS Value"
    )


def test_client_specific_header_pairs_are_not_encoded_as_equivalents() -> None:
    assert semantic_header_signature("Nº ID fiscal 1") != semantic_header_signature(
        "CNPJ/CPF"
    )
    assert semantic_header_signature("NºID fiscal regional") != semantic_header_signature(
        "State Tax Number"
    )
    assert semantic_header_signature("Isentas IPI") != semantic_header_signature(
        "IPI Base exc."
    )


def test_semantic_name_equivalents_are_resolved_in_first_pass() -> None:
    model = table(["Valor COFINS", "Data de lançamento"], [[0, "2026-01-01"]])
    client = table(["COFINS Value", "Posting date"], [[0, "2026-01-01"]])

    results = map_columns(model, client, LexicalHeaderScorer(), data_resolver=None)

    assert [(result.client_column, result.method) for result in results] == [
        ("COFINS Value", "name"),
        ("Posting date", "name"),
    ]


def test_close_runner_up_forces_row_data_pass() -> None:
    model = table(["Valor do imposto"], [[100.0], [250.0]])
    client = table(
        ["Tax value", "Tax amount"],
        [[100.0, 90.0], [250.0, 225.0]],
    )
    scorer = FixedScorer(np.array([[0.96, 0.94]]))
    resolver = FixedResolver(
        {
            "Valor do imposto": DataMappingDecision(
                client_column_id="column_001",
                data_confidence=0.95,
                final_confidence=0.95,
                reason="The names and values support Tax value.",
            )
        }
    )

    result = map_columns(model, client, scorer, resolver)[0]

    assert resolver.calls == ["Valor do imposto"]
    assert result.method == "data"
    assert result.name_margin == 0.02
    assert result.status == "review"
    assert result.llm_final_confidence == 0.95


def test_explicitly_conflicting_runner_up_does_not_block_header_acceptance() -> None:
    model = table(["Base CBS"], [[100.0]])
    client = table(["CBS Value", "CBS basis"], [[10.0, 100.0]])
    scorer = FixedScorer(np.array([[0.99, 0.96]]))

    result = map_columns(model, client, scorer, data_resolver=None)[0]

    assert result.client_column == "CBS basis"
    assert result.status == "accepted"
    assert result.name_margin == 0.96


def test_tax_montante_abbreviation_is_a_base_role() -> None:
    assert semantic_header_conflict("Base ICMS", "ICMS Monta") is None
    assert semantic_header_conflict("Base PIS", "PIS Mont.") is None
    assert semantic_header_conflict("Valor PIS", "PIS Mont.") == (
        "semantic-role conflict (value vs base)"
    )


def test_semantic_roles_use_generic_aliases_and_context() -> None:
    assert header_semantic_role("Aliq. COFINS") == "rate"
    assert header_semantic_role("Vlr ICMS Com") == "value"
    assert header_semantic_role("Vlr Base IPI") == "base"
    assert header_semantic_role("TOTAL DO ITEM") == "value"
    assert header_semantic_role("Lei COFINS") == "tax_rule"
    assert header_semantic_role("Direito fi") == "tax_rule"
    assert header_semantic_role("Unidade de medida") == "unit_of_measure"
    assert header_semantic_role("COD_MEDIDA") == "unit_of_measure"
    assert header_tax_families("Tax Situation COFI") == frozenset({"cofins"})
    assert header_tax_families("IBSS Value") == frozenset({"ibs"})
    assert semantic_header_conflict("Valor líquido", "TOTAL DO ITEM") is None
    assert semantic_header_conflict("Lei COFINS", "Aliq. COFINS") == (
        "semantic-role conflict (tax_rule vs rate)"
    )


def test_same_role_header_is_retained_for_review_when_resolver_returns_null() -> None:
    model = table(["Unidade de medida"], [["KG"], ["UN"]])
    client = table(["COD_MEDIDA"], [["KG"], ["UN"]])
    scorer = FixedScorer(np.array([[0.846]]))
    resolver = FixedResolver(
        {
            "Unidade de medida": DataMappingDecision(
                client_column_id=None,
                data_confidence=0.0,
                final_confidence=0.0,
                reason="No decision.",
            )
        }
    )

    result = map_columns(model, client, scorer, resolver)[0]

    assert result.client_column == "COD_MEDIDA"
    assert result.status == "review"


def test_low_name_generic_data_fallback_becomes_no_match() -> None:
    model = table(["Município do Local de Fim"], [["São Paulo"], ["Campinas"]])
    client = table(["Invoice Number"], [["1234567"], ["7654321"]])
    scorer = FixedScorer(np.array([[0.70]]))
    resolver = FixedResolver(
        {
            "Município do Local de Fim": DataMappingDecision(
                client_column_id="column_001",
                data_confidence=0.95,
                final_confidence=0.88,
                reason="Both columns contain strings.",
            )
        }
    )

    result = map_columns(model, client, scorer, resolver)[0]

    assert result.client_column is None
    assert result.status == "no_match"


def test_fiscal_math_relationships_handle_percent_and_decimal_rates() -> None:
    base = pd.Series([100.0, 200.0, 50.0, 300.0, 80.0] * 4)
    value = pd.Series([18.0, 36.0, 9.0, 54.0, 14.4] * 4)

    percent = tax_calculation_evidence(
        base, pd.Series([18.0] * 20), value
    )
    decimal = tax_calculation_evidence(
        base, pd.Series([0.18] * 20), value
    )
    line = line_calculation_evidence(
        pd.Series([2, 3, 4, 5, 6] * 4),
        pd.Series([10.0, 20.0, 7.5, 4.0, 3.0] * 4),
        pd.Series([20.0, 60.0, 30.0, 20.0, 18.0] * 4),
    )

    assert percent["score"] == 1.0
    assert percent["rate_representation"] == "percent"
    assert decimal["score"] == 1.0
    assert decimal["rate_representation"] == "decimal"
    assert line["score"] == 1.0


def test_small_relationship_samples_are_discounted() -> None:
    base = pd.Series([100.0, 200.0, 50.0, 300.0, 80.0])
    rate = pd.Series([18.0] * 5)
    value = base * 0.18

    evidence = tax_calculation_evidence(base, rate, value)

    assert evidence["fit_score"] == 1.0
    assert evidence["score"] == 0.5
    assert evidence["rows_tested"] == 5


def test_tax_relationship_reopens_an_inconsistent_exact_header_match() -> None:
    base = [100.0, 200.0, 50.0, 300.0, 80.0] * 4
    precise_rate = [10.0, 20.0, 5.0, 5.0, 10.0] * 4
    value = [10.0, 40.0, 2.5, 15.0, 8.0] * 4
    model = table(
        ["Base CBS", "Aliquota CBS", "Valor CBS"],
        [[base[i], precise_rate[i], value[i]] for i in range(20)],
    )
    client = table(
        ["CBS basis", "CBS Rate", "CBS Value", "Tax rate 4 decimals"],
        [[base[i], 0.0, value[i], precise_rate[i]] for i in range(20)],
    )
    scorer = FixedScorer(
        np.array(
            [
                [0.96, 0.20, 0.20, 0.30],
                [0.20, 0.96, 0.20, 0.70],
                [0.20, 0.20, 0.96, 0.20],
            ]
        )
    )
    resolver = FixedResolver(
        {
            "Aliquota CBS": DataMappingDecision(
                client_column_id="column_004",
                data_confidence=0.99,
                final_confidence=0.96,
                reason="The precise rate satisfies base times rate equals value.",
            )
        }
    )

    results = map_columns(model, client, scorer, resolver)

    assert resolver.calls == ["Aliquota CBS"]
    assert results[1].client_column == "Tax rate 4 decimals"
    assert results[1].status == "accepted"


def test_unique_tax_relationship_overrides_a_plausible_llm_alternative() -> None:
    correct_base = [100.0, 200.0, 300.0, 400.0, 500.0] * 4
    weaker_base = [100.0, 200.0, 300.0, 400.0, 625.0] * 4
    rate = [10.0] * 20
    value = [10.0, 20.0, 30.0, 40.0, 50.0] * 4
    model = table(
        ["Base ICMS", "Aliquota ICMS", "Valor ICMS"],
        [[correct_base[i], rate[i], value[i]] for i in range(20)],
    )
    client = table(
        ["X1", "X2", "ICMS Taxa", "ICMS Valor"],
        [[correct_base[i], weaker_base[i], rate[i], value[i]] for i in range(20)],
    )
    scorer = FixedScorer(
        np.array(
            [
                [0.84, 0.86, 0.20, 0.20],
                [0.20, 0.20, 0.96, 0.20],
                [0.20, 0.20, 0.20, 0.96],
            ]
        )
    )
    resolver = FixedResolver(
        {
            "Base ICMS": DataMappingDecision(
                client_column_id="column_002",
                data_confidence=0.92,
                final_confidence=0.92,
                reason="X2 looks like the more plausible numeric column.",
            )
        }
    )

    results = map_columns(model, client, scorer, resolver)

    assert results[0].client_column == "X1"
    assert results[0].status == "accepted"
    assert results[0].data_confidence == 1.0


def test_header_and_nonunique_tax_relationship_can_jointly_accept_a_mapping() -> None:
    base = [100.0, 200.0, 300.0, 400.0, 500.0] * 4
    rate = [10.0] * 20
    value = [10.0, 20.0, 30.0, 40.0, 50.0] * 4
    model = table(
        ["Base IPI", "Aliquota IPI", "Valor IPI"],
        [[base[i], rate[i], value[i]] for i in range(20)],
    )
    client = table(
        ["IPI Monta", "Broad total", "IPI Taxa", "IPI Valor"],
        [[base[i], base[i], rate[i], value[i]] for i in range(20)],
    )
    scorer = FixedScorer(
        np.array(
            [
                [0.88, 0.50, 0.20, 0.20],
                [0.20, 0.20, 0.96, 0.20],
                [0.20, 0.20, 0.20, 0.96],
            ]
        )
    )
    resolver = FixedResolver(
        {
            "Base IPI": DataMappingDecision(
                client_column_id="column_001",
                data_confidence=0.99,
                final_confidence=0.89,
                reason="The arithmetic supports IPI Monta.",
            )
        }
    )

    result = map_columns(model, client, scorer, resolver)[0]

    assert result.client_column == "IPI Monta"
    assert result.status == "accepted"
    assert result.final_confidence == 0.952


def test_ambiguous_names_with_all_zero_rows_remain_for_review() -> None:
    model = table(["Valor do imposto"], [[0], [0]])
    client = table(["Tax value", "Tax amount"], [[0, 0], [0, 0]])
    scorer = FixedScorer(np.array([[0.89, 0.88]]))
    resolver = FixedResolver(
        {
            "Valor do imposto": DataMappingDecision(
                client_column_id="column_001",
                data_confidence=0.1,
                final_confidence=0.89,
                reason="The row values are all zero.",
            )
        }
    )

    result = map_columns(model, client, scorer, resolver)[0]

    assert result.client_column == "Tax value"
    assert result.status == "review"
    assert "zero" in result.reason


def test_weak_rows_do_not_replace_a_clear_near_threshold_name_match() -> None:
    model = table(["Nº do NCM"], [["85167910"], ["73211100"]])
    client = table(
        ["NCM from material", "Other classification"],
        [[None, "1301600"], [None, "1301700"]],
    )
    scorer = FixedScorer(np.array([[0.89, 0.80]]))
    resolver = FixedResolver(
        {
            "Nº do NCM": DataMappingDecision(
                client_column_id="column_002",
                data_confidence=0.98,
                final_confidence=0.95,
                reason="The numeric shapes look similar.",
            )
        }
    )

    result = map_columns(model, client, scorer, resolver)[0]

    assert result.client_column == "NCM from material"
    assert result.data_confidence == 0.0
    assert result.final_confidence == 0.89
    assert result.status == "review"
    assert "preserve" in result.reason


def test_exact_distinctive_overlap_can_resolve_a_supported_null_decision() -> None:
    model = table(["Grupo de produtos"], [["A01"], ["B02"]])
    client = table(
        ["Product Group", "Product Description"],
        [["A01", "Item A"], ["B02", "Item B"]],
    )
    scorer = FixedScorer(np.array([[0.87, 0.70]]))
    resolver = FixedResolver(
        {
            "Grupo de produtos": DataMappingDecision(
                client_column_id=None,
                data_confidence=0.85,
                final_confidence=0.85,
                reason="Product Group is plausible, but confidence is limited.",
            )
        }
    )

    result = map_columns(model, client, scorer, resolver)[0]

    assert result.client_column == "Product Group"
    assert result.status == "accepted"
    assert "overlap" in result.reason


def test_all_zero_profile_is_marked_as_weak_evidence() -> None:
    profile = summarize_values(pd.Series([0, 0.0, "0", None]))

    assert profile["evidence_quality"] == "all_zero"
    assert profile["zero_fraction"] == 1.0


def test_profiles_validate_nfe_check_digits() -> None:
    valid = summarize_values(
        pd.Series(
            [
                "35260112345678000123550010000000011000000011",
                "35260198765432000123550010000000021000000020",
            ]
        )
    )
    invalid = summarize_values(
        pd.Series(
            [
                "35260112345678000123550010000000011000000010",
                "35260198765432000123550010000000021000000021",
            ]
        )
    )

    assert valid["valid_nfe_key_fraction"] == 1.0
    assert invalid["valid_nfe_key_fraction"] == 0.0


def test_profile_similarity_uses_generic_value_shape_and_ignores_weak_rows() -> None:
    tax_ids = summarize_values(pd.Series(["12345678000199", "98765432000188"]))
    same_shape = summarize_values(pd.Series(["11111111000111", "22222222000122"]))
    words = summarize_values(pd.Series(["Entrada", "Saída"]))
    zeros = summarize_values(pd.Series([0, 0]))

    assert profile_similarity(tax_ids, same_shape) > profile_similarity(tax_ids, words)
    assert profile_similarity(tax_ids, zeros) == 0.0


def test_data_candidate_pool_includes_row_shape_matches_outside_name_top_k() -> None:
    model = table(
        ["Opaque tax identifier"],
        [["12345678000199"], ["98765432000188"]],
    )
    client = table(
        ["Likely by name", "Description", "Registration"],
        [["Entrada", "Material A", "12345678000199"],
         ["Saída", "Material B", "98765432000188"]],
    )
    scorer = FixedScorer(np.array([[0.89, 0.80, 0.10]]))
    resolver = FixedResolver(
        {
            "Opaque tax identifier": DataMappingDecision(
                client_column_id="column_003",
                data_confidence=0.98,
                final_confidence=0.95,
                reason="The identifier value shapes match.",
            )
        }
    )

    result = map_columns(
        model,
        client,
        scorer,
        resolver,
        MapperConfig(candidate_count=1),
    )[0]

    assert result.client_column == "Registration"
    assert result.method == "data"
    assert result.status == "review"


def test_distinctive_rows_can_override_a_bogus_client_header() -> None:
    access_keys = [
        "35260112345678000123550010000000011000000011",
        "35260198765432000123550010000000021000000020",
    ]
    model = table(["Chave de acesso"], [[value] for value in access_keys])
    client = table(
        ["Invoice reference", "X9"],
        [["ABC", access_keys[0]], ["DEF", access_keys[1]]],
    )
    scorer = FixedScorer(np.array([[0.89, 0.10]]))
    resolver = FixedResolver(
        {
            "Chave de acesso": DataMappingDecision(
                client_column_id="column_002",
                data_confidence=0.99,
                final_confidence=0.97,
                reason="X9 contains distinctive 44-digit NF-e keys.",
            )
        }
    )

    result = map_columns(
        model,
        client,
        scorer,
        resolver,
        MapperConfig(candidate_count=1),
    )[0]

    assert result.client_column == "X9"
    assert result.name_confidence == 0.10
    assert result.data_confidence == 1.0
    assert result.llm_data_confidence == 0.99
    assert result.status == "review"


def test_openai_embedding_scorer_batches_headers_and_scores_cosine() -> None:
    vectors = {
        "Brazilian invoice spreadsheet column: CFOP": [1.0, 0.0],
        "Brazilian invoice spreadsheet column: Base ICMS": [0.0, 1.0],
        "Brazilian invoice spreadsheet column: CFO": [0.9, 0.1],
        "Brazilian invoice spreadsheet column: ICMS Base": [0.1, 0.9],
    }

    class Embeddings:
        def create(self, *, model, input, encoding_format):
            return SimpleNamespace(
                data=[SimpleNamespace(embedding=vectors[text]) for text in input]
            )

    client = SimpleNamespace(embeddings=Embeddings())
    scorer = OpenAIEmbeddingHeaderScorer(client, model="embedding-test")

    scores = scorer.scores(["CFOP", "Base ICMS"], ["CFO", "ICMS Base"])

    assert scores.shape == (2, 2)
    assert scores[0, 0] > scores[0, 1]
    assert scores[1, 1] > scores[1, 0]


def test_openai_data_resolver_uses_structured_response() -> None:
    expected = DataMappingDecision(
        client_column_id="column_001",
        data_confidence=0.97,
        final_confidence=0.93,
        reason="The values are four-digit CFOP codes.",
    )

    class Responses:
        def __init__(self):
            self.arguments = None

        def parse(self, **kwargs):
            self.arguments = kwargs
            return SimpleNamespace(output_parsed=expected)

    responses = Responses()
    client = SimpleNamespace(responses=responses)
    resolver = OpenAIDataResolver(client, model="llm-test")
    model_column = ColumnRef("column_001", 0, "CFOP", 1)
    client_column = ColumnRef("column_001", 0, "Cod. Fiscal", 1)

    decision = resolver.resolve(
        model_column,
        pd.Series(["5102", "6102"]),
        [(client_column, pd.Series(["5102", "6102"]), 0.61)],
        sample_size=2,
    )

    assert decision == expected
    assert responses.arguments["model"] == "llm-test"
    assert responses.arguments["store"] is False
    assert responses.arguments["temperature"] == 0
    assert "reasoning" not in responses.arguments
    assert responses.arguments["text_format"] is DataMappingDecision
    assert '"samples": ["5102", "6102"]' in responses.arguments["input"]


def test_data_resolver_preserves_name_confidence_when_rows_are_all_zero() -> None:
    model_decision = DataMappingDecision(
        client_column_id="column_001",
        data_confidence=0.2,
        final_confidence=0.3,
        reason="The zero values do not add evidence.",
    )

    class Responses:
        def parse(self, **kwargs):
            return SimpleNamespace(output_parsed=model_decision)

    resolver = OpenAIDataResolver(SimpleNamespace(responses=Responses()), model="llm-test")
    model_column = ColumnRef("model_001", 0, "Valor CBS", 1)
    client_column = ColumnRef("column_001", 0, "CBS Value", 1)

    decision = resolver.resolve(
        model_column,
        pd.Series([0, 0, 0]),
        [(client_column, pd.Series([0, 0, 0]), 0.88)],
        sample_size=3,
    )

    assert decision.final_confidence == 0.88


@pytest.mark.parametrize(
    ("header", "role"),
    [
        ("Tipo de Imposto DIF_ALIQ", "tax_status"),
        ("Base DIF_ALIQ", "base"),
        ("Aliquota DIF_ALIQ", "rate"),
        ("Valor DIF_ALIQ", "value"),
        ("Isentas DIF_ALIQ", "exempt"),
        ("Outras DIF_ALIQ", "other"),
        ("Base Reduzida DIF_ALIQ", "reduced_base"),
    ],
)
def test_a_family_name_does_not_smuggle_a_rate_role(header: str, role: str) -> None:
    # DIF_ALIQ tokenises to include "aliq", which the alias table expands to
    # "aliquota". Five of these seven model columns reported role "rate", so
    # semantic_header_conflict misfired across the whole block.
    assert header_semantic_role(header) == role


@pytest.mark.parametrize(
    "header",
    ["Aliq. PIS", "Alíq. ICMS", "ICMS Taxa", "Tx.imposto 4 c.decs.", "Tax rate (Tax/Base)"],
)
def test_real_rate_headers_keep_the_rate_role(header: str) -> None:
    # The guard above must stay narrow. "Aliq." really is the rate word in the
    # Datasul exports, and "Tax rate (Tax/Base)" is a rate despite naming a
    # base - a broader guard demoted it, which is why the rule keys on whether
    # the rate signal rests on the abbreviation alone.
    assert header_semantic_role(header) == "rate"


def test_a_column_with_no_data_is_vacuous_but_a_column_of_zeros_is_not() -> None:
    # The distinction is load-bearing. datasul carries 200 all-zero columns and
    # six of oracle's are client-authored ground truth, so a guard keyed on
    # all-zero would destroy verified-correct mappings; a guard keyed on
    # emptiness removes only mappings that ship a silently blank column.
    table = SheetTable(
        columns=[
            ColumnRef(id="column_001", index=0, name="Empty", occurrence=1),
            ColumnRef(id="column_002", index=1, name="Blanks", occurrence=1),
            ColumnRef(id="column_003", index=2, name="Zeros", occurrence=1),
        ],
        data=pd.DataFrame(
            {"Empty": [None, None, None], "Blanks": ["", "  ", None], "Zeros": [0, 0, 0]}
        ),
    )
    assert client_column_is_vacuous(table, table.columns[0])
    assert client_column_is_vacuous(table, table.columns[1])
    assert not client_column_is_vacuous(table, table.columns[2])


def test_a_trio_is_discovered_from_the_client_alone() -> None:
    # No model, no prior acceptance, no vocabulary beyond family and role.
    # valor = base x aliquota is tax law, so a trio proven here is evidence
    # that transfers to an ERP nobody has seen.
    base = [1000.0 + index for index in range(60)]
    rate = [18.0] * 60
    sheet = table(
        ["ICMS Base", "ICMS Taxa", "ICMS Valor", "Ruido"],
        [
            [base[i], rate[i], round(base[i] * 0.18, 2), i]
            for i in range(60)
        ],
    )
    trios = discover_client_tax_trios(sheet)
    assert ("icms", ()) in trios
    trio = trios[("icms", ())][0]
    assert (trio["base"], trio["rate"], trio["value"]) == (0, 1, 2)
    assert trio["rows_tested"] >= 30
    assert trio["fit_score"] >= 0.97
    assert trio["alternatives"] == 1


def test_a_trio_needs_enough_rows_to_be_proof() -> None:
    # A perfect fit over a handful of rows proves nothing; the real corpus
    # contains 12-row degenerate fits indistinguishable from genuine ones.
    base = [1000.0 + index for index in range(10)]
    sheet = table(
        ["IPI Base", "IPI Taxa", "IPI Valor"],
        [[base[i], 5.0, round(base[i] * 0.05, 2)] for i in range(10)],
    )
    assert discover_client_tax_trios(sheet) == {}


def test_corroboration_matches_the_slot_not_merely_the_family() -> None:
    base = [1000.0 + index for index in range(60)]
    sheet = table(
        ["ICMS Base", "ICMS Taxa", "ICMS Valor"],
        [[base[i], 18.0, round(base[i] * 0.18, 2)] for i in range(60)],
    )
    trios = discover_client_tax_trios(sheet)
    assert trio_corroboration(trios, "Base ICMS", 0) is not None
    # right family, wrong slot
    assert trio_corroboration(trios, "Base ICMS", 1) is None
    # a role the identity says nothing about
    assert trio_corroboration(trios, "Isentas ICMS", 0) is None


def test_the_role_vocabulary_is_prefix_unambiguous() -> None:
    # This is the property that makes truncation expansion safe by
    # construction rather than by tuning: no prefix of any role word reaches
    # two different concepts, so a truncation cannot resolve two ways. A
    # future addition that breaks this must fail here rather than silently
    # start mis-labelling roles.
    seen: dict[str, str] = {}
    for concept, words in _ROLE_CONCEPT_WORDS.items():
        for word in words:
            for size in range(_MIN_ROLE_PREFIX, len(word)):
                prefix = word[:size]
                assert seen.setdefault(prefix, concept) == concept, (
                    f"prefix {prefix!r} reaches both {seen[prefix]} and {concept}"
                )


@pytest.mark.parametrize(
    ("header", "role"),
    [
        ("Vlr Isen ICM", "exempt"),
        ("Vlr Out ICMS", "other"),
        ("Vlr Isen IPI", "exempt"),
        ("Vlr Outr IPI", "other"),
    ],
)
def test_truncated_role_words_still_name_their_role(header: str, role: str) -> None:
    # Datasul truncates headers to 12 characters, so the role word arrives cut
    # short. These four read as "value", which made semantic_header_conflict
    # veto the CORRECT pair and put them beyond acceptance at any confidence.
    assert header_semantic_role(header) == role


def test_expansion_leaves_full_words_and_short_tokens_alone() -> None:
    assert expand_truncated_role_words({"outras"}) == {"outras"}
    assert expand_truncated_role_words({"ou"}) == {"ou"}          # too short
    assert expand_truncated_role_words({"icms"}) == {"icms"}      # not a role word
    # additive: the original token survives alongside the expansion
    assert expand_truncated_role_words({"isen"}) == {"isen", "isentas"}


def _unmatched(results: dict[int, MappingResult], client: SheetTable, model: SheetTable):
    return summarize_unmatched_columns(
        model_table=model,
        client_table=client,
        results=results,
        similarities=LexicalHeaderScorer().scores(
            [c.name for c in model.columns], [c.name for c in client.columns]
        ),
        reference_catalogs=None,
        client_tax_trios={},
        sample_size=8,
    )


def test_unmatched_means_not_consumed_not_merely_unreferenced() -> None:
    # The distinction decides whether the report finds real misses. Defining
    # unmatched as "never referenced" hides every column a review proposal
    # touched, and measured against the client answer keys that hides more
    # than half of the truths the mapper failed to deliver.
    client = table(["Base ICMS", "Vlr Frete", "Ruido"], [[10, 20, 30]] * 40)
    model = table(["Base ICMS", "Frete"], [[10, 20]] * 40)
    results = {
        0: MappingResult(
            model_column_number=1, model_column="Base ICMS",
            client_column_number=1, client_column="Base ICMS",
            name_confidence=1.0, name_runner_up_confidence=0.0, name_margin=1.0,
            data_confidence=None, final_confidence=1.0, method="name",
            status="accepted", reason="",
        ),
        1: MappingResult(
            model_column_number=2, model_column="Frete",
            client_column_number=2, client_column="Vlr Frete",
            name_confidence=0.8, name_runner_up_confidence=0.0, name_margin=0.8,
            data_confidence=None, final_confidence=0.8, method="name",
            status="review", reason="",
        ),
    }
    rows = {row.client_column_number: row for row in _unmatched(results, client, model)}
    assert 1 not in rows                                   # consumed
    assert rows[2].disposition == "proposed_not_accepted"  # proposed, not delivered
    assert rows[3].disposition == "never_referenced"


def test_nothing_is_omitted_only_demoted() -> None:
    # Two candidate cut rules were each measured to destroy a real answer, so
    # a column that matches nothing is still written, with a sentence saying
    # why it was set aside.
    client = table(["Vazio", "Ruido"], [[None, 7]] * 40)
    model = table(["Frete"], [[1]] * 40)
    rows = _unmatched({}, client, model)
    assert {r.client_column_number for r in rows} == {1, 2}
    empty = next(r for r in rows if r.client_column_number == 1)
    assert empty.review_priority == "low"
    assert "No values in this column." in empty.signal_detail


def test_shortlist_leads_and_the_rest_follow_in_column_order() -> None:
    client = table(["A", "B", "C"], [[None, 5, None]] * 40)
    model = table(["Frete"], [[1]] * 40)
    rows = _unmatched({}, client, model)
    priorities = [r.review_priority for r in rows]
    assert priorities == sorted(priorities, key=lambda p: p != "shortlist")


def test_two_disjoint_blocks_for_one_tax_are_both_kept() -> None:
    # datasul runs an apuracao PIS block and an escrituracao PIS block. Neither
    # 'Ap' nor the bare name is a recognised qualifier, so both collapse to the
    # scope ('pis', ()) and the argmax kept only the larger - discarding the
    # block the model's own slots actually want.
    base_a = [1000.0 + index for index in range(60)]
    base_b = [500.0 + index for index in range(60)]
    sheet = table(
        ["Base PIS Ap", "Aliq. PIS", "Vlr PIS Ap", "Base Pis", "Aliq. PIS", "Valor Pis"],
        [
            [base_a[i], 1.65, round(base_a[i] * 0.0165, 2),
             base_b[i], 0.65, round(base_b[i] * 0.0065, 2)]
            for i in range(60)
        ],
    )
    trios = discover_client_tax_trios(sheet)
    blocks = {
        (t["base"], t["rate"], t["value"]) for t in trios[("pis", ())]
    }
    assert (0, 1, 2) in blocks and (3, 4, 5) in blocks
    assert all(t["alternatives"] == 2 for t in trios[("pis", ())])


def test_a_proof_says_so_when_it_cannot_single_out_a_column() -> None:
    # The identity corroborates a scope, not a column. Where two disjoint
    # blocks each satisfy it, an unqualified proof on the losing block reads as
    # an endorsement - which is how datasul m213 -> c96 came to carry one.
    lone = {"tax_scope": "icms", "rows_tested": 500, "fit_score": 0.99, "alternatives": 1}
    rival = {"tax_scope": "pis", "rows_tested": 5501, "fit_score": 0.9996, "alternatives": 2}
    assert "does not single out" not in _trio_proof_text(lone)
    assert "2 disjoint pis blocks" in _trio_proof_text(rival)
    assert "does not single out this column" in _trio_proof_text(rival)


def test_rate_aliases_for_one_block_are_not_counted_as_rival_blocks() -> None:
    # sap_terumo names every rate twice - 'ICMS Taxa' and 'Tax rate ICMS' -
    # and both close on the same base and value. Sharing a column makes them
    # one block described twice, not two readings of the model's slots.
    base = [1000.0 + index for index in range(60)]
    sheet = table(
        ["ICMS Base", "ICMS Taxa", "ICMS Valor", "Tax rate ICMS"],
        [[base[i], 18.0, round(base[i] * 0.18, 2), 18.0] for i in range(60)],
    )
    trios = discover_client_tax_trios(sheet)
    assert len(trios[("icms", ())]) >= 2
    assert all(t["alternatives"] == 1 for t in trios[("icms", ())])


def test_valor_contabil_is_the_document_total_not_a_bookkeeping_field() -> None:
    # SPED EFD registro C100 field VL_DOC: in the fiscal register "valor
    # contabil" is the document total. datasul's own rows prove it - Vlr
    # Contabil == Vlr Total + Valor IPI holds on 6,737 of 6,800 rows (0.9907)
    # while plain equality with Vlr Total holds on 51%.
    target = semantic_header_signature("Val.Total NF")
    for header in ("Vlr Contábil", "Valor Contabil", "Val Contab", "VL CONTABIL"):
        assert semantic_header_signature(header) == target, header


def test_the_value_word_is_what_spares_the_bookkeeping_false_friends() -> None:
    # The rewrite needs a value word in front. Without that anchor it would
    # swallow the chart-of-accounts vocabulary that shares the same adjective.
    target = semantic_header_signature("Val.Total NF")
    for header in ("Conta Contábil", "Lançamento Contábil", "Data Contábil",
                   "Contábil", "Plano de Contas Contábil", "Centro de Custo Contábil"):
        assert semantic_header_signature(header) != target, header


def _queue_result(model_number, model_name, client_number, client_name,
                  final, margin=0.0, status="review", **kw):
    return MappingResult(
        model_column_number=model_number, model_column=model_name,
        client_column_number=client_number, client_column=client_name,
        name_confidence=final, name_runner_up_confidence=final - margin,
        name_margin=margin, data_confidence=None, final_confidence=final,
        method="data", status=status, reason="", **kw)


def test_the_queue_holds_only_proposals_the_gate_did_not_deliver() -> None:
    sheet = table(["A", "B"], [["x", "y"], ["x", "y"]])
    rows = summarize_adjudication_queue(
        client_table=sheet,
        results=[
            _queue_result(1, "One", 1, "A", 0.95, status="accepted"),
            _queue_result(2, "Two", 2, "B", 0.88),
            _queue_result(3, "Three", None, None, 0.0, status="no_match"),
        ],
        final_threshold=0.90, sample_size=8,
    )
    assert [r.model_column_number for r in rows] == [2]
    assert rows[0].gate_distance == pytest.approx(0.02)


def test_a_proposal_onto_an_empty_column_can_never_be_confirmed() -> None:
    # Confirming it ships a blank column under a reviewer's signature, so it
    # cannot hold a top slot however well the header matched. sap_terumo's
    # 'FCI number' matches 'Nº FCI' at 0.98 and is empty on all 727 rows.
    sheet = table(["Full", "Empty"], [["x", None], ["y", None]])
    rows = summarize_adjudication_queue(
        client_table=sheet,
        results=[
            _queue_result(1, "Weak but real", 1, "Full", 0.70),
            _queue_result(2, "Strong but empty", 2, "Empty", 0.98),
        ],
        final_threshold=0.90, sample_size=8,
    )
    assert [r.model_column for r in rows] == ["Weak but real", "Strong but empty"]
    assert rows[-1].rows_with_value == 0
    assert "no populated rows" in rows[-1].why_held


def test_margin_breaks_ties_only_inside_the_clamped_band() -> None:
    # The clamp pins a whole class of rows to one confidence, so the confidence
    # column stops ordering them. Outside the band confidence must still win,
    # because the margin's apparent lead is one client's artefact.
    sheet = table(["A", "B", "C"], [["x", "y", "z"], ["x", "y", "z"]])
    rows = summarize_adjudication_queue(
        client_table=sheet,
        results=[
            _queue_result(1, "Clamped, thin margin", 1, "A", 0.8999, margin=0.01),
            _queue_result(2, "Clamped, wide margin", 2, "B", 0.8999, margin=0.40),
            _queue_result(3, "Higher confidence", 3, "C", 0.8999 + 0.01, margin=0.0),
        ],
        final_threshold=0.90, sample_size=8,
    )
    assert [r.model_column_number for r in rows] == [3, 2, 1]


def test_the_queue_says_when_the_branch_rather_than_the_evidence_held_a_row() -> None:
    sheet = table(["A"], [["x"], ["x"]])
    rows = summarize_adjudication_queue(
        client_table=sheet,
        results=[_queue_result(1, "One", 1, "A", 0.8999)],
        final_threshold=0.90, sample_size=8,
    )
    assert "cannot auto-accept" in rows[0].why_held
