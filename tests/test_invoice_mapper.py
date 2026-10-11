"""Testa as decisões, as mensagens e os arquivos produzidos pelo mapeador.

As decisões são verificadas pelos valores internos em inglês; as mensagens,
pelas constantes em português; e os arquivos, pelo schema de saída.
Cabeçalhos como ``COFINS Value`` e ``Posting date`` exercitam os sinônimos.
As respostas dos fake resolvers permanecem em inglês para representar as
justificativas retornadas pela IA.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from types import SimpleNamespace

import numpy as np
import pandas as pd

import pytest

import report_schema as rs
from invoice_mapper import (
    MAPPING_SCHEMA_VERSION,
    PROOF_NOT_DECISIVE_SUFFIX,
    PROOF_TEXT,
    REASON_CATALOG_DECISION,
    REASON_CONFLICT_REVIEW,
    REASON_OFFLINE,
    REASON_OVERLAP,
    REASON_PASS_ONE_ACCEPTED,
    REASON_PRESERVE_BEST,
    REASON_UNSUPPORTED_NO_MATCH,
    REASON_VACUITY,
    RESOLVER_DISAGREES,
    SIGNAL_CATALOG,
    SIGNAL_CHECKDIGIT,
    SIGNAL_EMPTY,
    SIGNAL_TRIO,
    WHY_HELD_BELOW_THRESHOLD,
    WHY_HELD_NAME_ONLY,
    WHY_HELD_VACUOUS,
    AdjudicationRow,
    ColumnRef,
    DataMappingDecision,
    LexicalHeaderScorer,
    MapperConfig,
    MappingResult,
    OpenAIDataResolver,
    OpenAIEmbeddingHeaderScorer,
    SheetTable,
    UnmatchedColumn,
    _MIN_ROLE_PREFIX,
    _ROLE_CONCEPT_WORDS,
    _supporting_step_ids,
    _trio_proof_text,
    _unmatched_signals,
    _unique_distinctive_catalog_decision,
    build_parser,
    client_column_is_vacuous,
    describe_header_conflict_pt,
    discover_client_tax_trios,
    expand_truncated_role_words,
    header_semantic_role,
    header_tax_families,
    line_calculation_evidence,
    main,
    map_columns,
    profile_similarity,
    sample_values,
    semantic_header_conflict,
    semantic_header_signature,
    summarize_adjudication_queue,
    summarize_unmatched_columns,
    summarize_values,
    tax_calculation_evidence,
    trio_corroboration,
    underspecified_header_match,
    write_results,
)


def table(headers: list[str], rows: list[list[object]]) -> SheetTable:
    """Monta uma SheetTable em memória, contando as ocorrências de cabeçalhos repetidos."""
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
    """Fake scorer que retorna uma matriz fixa de scores de cabeçalho."""

    matrix: np.ndarray

    def scores(self, model_headers, client_headers) -> np.ndarray:
        return self.matrix


class FixedResolver:
    """Fake resolver que retorna decisões predefinidas e registra as chamadas recebidas."""

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
    # A decisão usa o conflito em inglês; a justificativa mostra o mesmo
    # conflito em português.
    assert results[0].vetoed_by == "family_conflict"
    assert results[0].reason == REASON_CONFLICT_REVIEW.format(
        header_conflict="tipos de campo incompatíveis (situação tributária vs base de cálculo)"
    )
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
    assert result.reason == REASON_UNSUPPORTED_NO_MATCH


@pytest.mark.parametrize(
    ("left", "right", "english", "portuguese"),
    [
        ("Base IBS", "IBSS Rate", "semantic-role conflict (base vs rate)",
         "tipos de campo incompatíveis (base de cálculo vs alíquota)"),
        ("Valor ICMS", "IPI Value", "tax-family conflict (icms vs ipi)",
         "tributos diferentes (ICMS vs IPI)"),
        ("Base ICMS", "ICMS Mont.base excl.", "semantic-role conflict (base vs exempt)",
         "tipos de campo incompatíveis (base de cálculo vs isentas)"),
        ("CST PIS", "PIS Mont.", "semantic-role conflict (tax_status vs base)",
         "tipos de campo incompatíveis (situação tributária vs base de cálculo)"),
        ("Tipo de Imposto CBS", "Tax Classification Code for CBS and IBS",
         "semantic-role conflict (tax_status vs tax_classification)",
         "tipos de campo incompatíveis (situação tributária vs classificação tributária)"),
        ("Nº item do documento", "SAP Nº do NF", "semantic-role conflict (item_id vs document_id)",
         "tipos de campo incompatíveis (número do item vs número do documento)"),
        ("Aliquota ICMS_ST", "ICMS Taxa", "tax-qualifier conflict (st vs regular)",
         "tratamentos tributários diferentes (ST vs normal)"),
        ("ICMS Taxa", "Aliquota ICMS_ST", "tax-qualifier conflict (regular vs st)",
         "tratamentos tributários diferentes (normal vs ST)"),
        ("Grupo de imposto", "Grupo de mercadorias", "semantic-role conflict (tax_group vs product_group)",
         "tipos de campo incompatíveis (grupo de imposto vs grupo de produto)"),
        ("Valor ICMS", "Valor COFINS e PIS", "tax-family conflict (icms vs cofins, pis)",
         "tributos diferentes (ICMS vs COFINS, PIS)"),
        ("Lei COFINS", "Aliq. COFINS", "semantic-role conflict (tax_rule vs rate)",
         "tipos de campo incompatíveis (lei tributária vs alíquota)"),
        ("Chave de acesso", "X9", None, None),
        ("Base ICMS", "ICMS Monta", None, None),
        ("Valor líquido", "TOTAL DO ITEM", None, None),
        ("Base ICMS_ST", "ICMS Base", "tax-qualifier conflict (st vs regular)",
         "tratamentos tributários diferentes (ST vs normal)"),
        ("Valor PIS", "Valor COFINS", "tax-family conflict (pis vs cofins)",
         "tributos diferentes (PIS vs COFINS)"),
        ("Valor IBS", "Valor CBS", "tax-family conflict (ibs vs cbs)",
         "tributos diferentes (IBS vs CBS)"),
        ("Base ICMS", "Valor ICMS", "semantic-role conflict (base vs value)",
         "tipos de campo incompatíveis (base de cálculo vs valor)"),
        ("Alíquota IPI", "Valor IPI", "semantic-role conflict (rate vs value)",
         "tipos de campo incompatíveis (alíquota vs valor)"),
        ("Vlr Isen ICM", "Vlr Outr ICM", "semantic-role conflict (exempt vs other)",
         "tipos de campo incompatíveis (isentas vs outras)"),
        ("CST ICMS", "Valor ICMS", "semantic-role conflict (tax_status vs value)",
         "tipos de campo incompatíveis (situação tributária vs valor)"),
        ("CFOP", "CFOP source", None, None),
        ("Base PIS/COFINS", "Base PIS", None, None),
    ],
)
def test_header_conflicts_preserve_fiscal_meaning_in_both_languages(
    left, right, english, portuguese,
) -> None:
    # Uma tabela cobre o texto enviado à IA e a descrição exibida ao revisor.
    assert semantic_header_conflict(left, right) == english
    assert describe_header_conflict_pt(left, right) == portuguese


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

    # Em memória o sufixo de ocorrência fica em inglês: o nome de exibição é
    # comparado pelo apply e só vira "(ocorrência 2)" nas células gravadas.
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
        "parecer_revisor",
        "coluna_cliente_correta",
        "observacao_revisor",
    ]


def test_mapping_report_writes_run_metadata(tmp_path) -> None:
    model = table(["CFOP"], [["5102"]])
    client = table(["CFOP"], [["5102"]])
    result = map_columns(model, client, LexicalHeaderScorer(), data_resolver=None)
    output = tmp_path / "mapping.xlsx"

    write_results(
        result,
        output,
        metadata={
            "mapping_schema_version": MAPPING_SCHEMA_VERSION,
            "embedding_model": "offline",
            "reference_manifest_sha256": "disabled",
        },
    )

    workbook = pd.ExcelFile(output)
    assert workbook.sheet_names == ["Mapeamentos", "Metadados da Execução"]
    metadata = pd.read_excel(workbook, sheet_name="Metadados da Execução")
    assert metadata.to_dict("records") == [
        {"chave": "versao_esquema_mapeamento", "valor": 3},
        {"chave": "modelo_embedding", "valor": "offline"},
        {"chave": "sha256_manifesto_referencia", "valor": "desativado"},
    ]


def test_the_report_schema_version_is_the_portuguese_one() -> None:
    assert MAPPING_SCHEMA_VERSION == rs.SCHEMA_VERSION_PT == 3


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

    assert resolver.calls == []  # A relação aritmética já resolve a escolha localmente.
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
    # Abaixo do threshold, a justificativa é a prosa da IA, que passa intacta.
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
    assert result.reason == REASON_PRESERVE_BEST


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
    assert result.reason == REASON_OVERLAP


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


def test_data_resolver_preserves_the_raw_response_when_rows_are_all_zero() -> None:
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

    assert decision.model_dump() == model_decision.model_dump()


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
    # DIF_ALIQ gera "aliq", que vira "aliquota"; a palavra de posição precisa vencer.
    assert header_semantic_role(header) == role


@pytest.mark.parametrize(
    "header",
    ["Aliq. PIS", "Alíq. ICMS", "ICMS Taxa", "Tx.imposto 4 c.decs.", "Tax rate (Tax/Base)"],
)
def test_real_rate_headers_keep_the_rate_role(header: str) -> None:
    # A proteção acima é estreita: só cede quando a alíquota vem de uma
    # abreviação, então "Tax rate (Tax/Base)" continua alíquota.
    assert header_semantic_role(header) == "rate"


def test_a_column_with_no_data_is_vacuous_but_a_column_of_zeros_is_not() -> None:
    # Zero é dado fiscal comum; só uma coluna sem nenhuma célula preenchida é vazia.
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
    # Só as colunas do cliente: sem modelo e sem aceite anterior.
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
    # Poucas linhas não bastam para confirmar a relação, mesmo com aderência perfeita.
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
    # família certa, posição errada
    assert trio_corroboration(trios, "Base ICMS", 1) is None
    # um papel sobre o qual a identidade não diz nada
    assert trio_corroboration(trios, "Isentas ICMS", 0) is None


def test_the_role_vocabulary_is_prefix_unambiguous() -> None:
    # Nenhum prefixo de palavra de papel alcança dois conceitos; senão um
    # truncamento teria duas leituras.
    seen: dict[str, str] = {}
    for concept, words in _ROLE_CONCEPT_WORDS.items():
        for word in words:
            for size in range(_MIN_ROLE_PREFIX, len(word)):
                prefix = word[:size]
                assert seen.setdefault(prefix, concept) == concept, (
                    f"o prefixo {prefix!r} alcança {seen[prefix]} e {concept}"
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
    # Os ERPs truncam os cabeçalhos; a palavra de papel cortada ainda nomeia o papel.
    assert header_semantic_role(header) == role


def test_expansion_leaves_full_words_and_short_tokens_alone() -> None:
    assert expand_truncated_role_words({"outras"}) == {"outras"}
    assert expand_truncated_role_words({"ou"}) == {"ou"}          # curta demais
    assert expand_truncated_role_words({"icms"}) == {"icms"}      # não é palavra de papel
    # aditiva: a palavra original continua ao lado da expansão
    assert expand_truncated_role_words({"isen"}) == {"isen", "isentas"}


def _unmatched(results: dict[int, MappingResult], client: SheetTable, model: SheetTable):
    """Colunas do cliente sem uso, sem catálogos e sem trios."""
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
    # Uma coluna só proposta em revisão não chega ao cliente, então conta como sem uso.
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
    assert 1 not in rows                                   # consumida
    assert rows[2].disposition == "proposed_not_accepted"  # proposta, não entregue
    assert rows[3].disposition == "never_referenced"


def test_nothing_is_omitted_only_demoted() -> None:
    # Uma coluna que não apresenta correspondência continua listada, com o motivo.
    client = table(["Vazio", "Ruido"], [[None, 7]] * 40)
    model = table(["Frete"], [[1]] * 40)
    rows = _unmatched({}, client, model)
    assert {r.client_column_number for r in rows} == {1, 2}
    empty = next(r for r in rows if r.client_column_number == 1)
    assert empty.review_priority == "low"
    assert empty.signal_detail == SIGNAL_EMPTY == "Esta coluna não tem valores."


def test_signal_details_are_portuguese_with_a_decimal_comma() -> None:
    # Os ids de sinal ficam internos (traduzidos na gravação); o detalhe sai
    # em português, com rótulos fiscais legíveis e vírgula decimal.
    base = [1000.0 + index for index in range(60)]
    sheet = table(
        ["ICMS Base", "ICMS Taxa", "ICMS Valor"],
        [[base[i], 18.0, round(base[i] * 0.18, 2)] for i in range(60)],
    )
    trios = discover_client_tax_trios(sheet)
    slots = {
        int(trio[slot]): slot for trio in trios[("icms", ())] for slot in ("base", "rate", "value")
    }
    column = sheet.columns[1]
    values = sheet.series(column)
    signals, detail = _unmatched_signals(
        column=column,
        profile=summarize_values(values),
        values=values,
        reference_catalogs=None,
        trio_slots=slots,
    )
    assert signals == ["arithmetic"]
    assert detail == SIGNAL_TRIO.format(slot="alíquota")
    assert detail == "participa como alíquota de um trio comprovado de valor = base x alíquota"

    cnpjs = pd.Series(["11.222.333/0001-81"] * 7 + ["123"])
    signals, detail = _unmatched_signals(
        column=ColumnRef("column_001", 0, "CNPJ", 1),
        profile=summarize_values(cnpjs),
        values=cnpjs,
        reference_catalogs=None,
        trio_slots={},
    )
    assert signals == ["cnpj_checkdigit"]
    assert detail == SIGNAL_CHECKDIGIT.format(fraction="87,5%", kind="CNPJ")


def test_shortlist_leads_and_the_rest_follow_in_column_order() -> None:
    client = table(["A", "B", "C"], [[None, 5, None]] * 40)
    model = table(["Frete"], [[1]] * 40)
    rows = _unmatched({}, client, model)
    priorities = [r.review_priority for r in rows]
    assert priorities == sorted(priorities, key=lambda p: p != "shortlist")


def test_two_disjoint_blocks_for_one_tax_are_both_kept() -> None:
    # Dois blocos disjuntos do mesmo escopo são guardados, não só o maior.
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
    # A identidade confirma um escopo, não uma coluna: com dois blocos
    # disjuntos, a prova diz que não distingue a coluna.
    lone = {"tax_scope": "icms", "rows_tested": 500, "fit_score": 0.99, "alternatives": 1}
    rival = {"tax_scope": "pis", "rows_tested": 5501, "fit_score": 0.9996, "alternatives": 2}
    assert _trio_proof_text(lone) == (
        "valor = base x alíquota se confirma para ICMS em 500 linhas, com aderência 0,99"
    )
    assert _trio_proof_text(rival) == (
        "valor = base x alíquota se confirma para PIS em 5501 linhas, com aderência 0,9996"
        " - mas este cliente tem 2 blocos disjuntos de PIS que satisfazem a equação, "
        "portanto ela não distingue esta coluna"
    )


def test_rate_aliases_for_one_block_are_not_counted_as_rival_blocks() -> None:
    # Duas alíquotas com a mesma base e o mesmo valor são um bloco descrito
    # duas vezes, não dois blocos.
    base = [1000.0 + index for index in range(60)]
    sheet = table(
        ["ICMS Base", "ICMS Taxa", "ICMS Valor", "Tax rate ICMS"],
        [[base[i], 18.0, round(base[i] * 0.18, 2), 18.0] for i in range(60)],
    )
    trios = discover_client_tax_trios(sheet)
    assert len(trios[("icms", ())]) >= 2
    assert all(t["alternatives"] == 1 for t in trios[("icms", ())])


def test_valor_contabil_is_the_document_total_not_a_bookkeeping_field() -> None:
    # No livro fiscal (VL_DOC do registro C100 da EFD), "valor contábil" é o
    # total do documento.
    target = semantic_header_signature("Val.Total NF")
    for header in ("Vlr Contábil", "Valor Contabil", "Val Contab", "VL CONTABIL"):
        assert semantic_header_signature(header) == target, header


def test_the_value_word_is_what_spares_the_bookkeeping_false_friends() -> None:
    # A troca exige a palavra de valor na frente; o plano de contas usa o mesmo adjetivo.
    target = semantic_header_signature("Val.Total NF")
    for header in ("Conta Contábil", "Lançamento Contábil", "Data Contábil",
                   "Contábil", "Plano de Contas Contábil", "Centro de Custo Contábil"):
        assert semantic_header_signature(header) != target, header


def _queue_result(model_number, model_name, client_number, client_name,
                  final, margin=0.0, status="review", **kw):
    """Uma proposta mínima para a fila de revisão."""
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
    assert rows[0].why_held == WHY_HELD_BELOW_THRESHOLD == "confiança abaixo do threshold de aceitação"


def test_a_proposal_onto_an_empty_column_can_never_be_confirmed() -> None:
    # Confirmá-la entregaria uma coluna em branco, então ela vai para o fim da
    # fila, por melhor que seja o cabeçalho.
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
    assert rows[-1].why_held == WHY_HELD_VACUOUS


def test_margin_breaks_ties_only_inside_the_clamped_band() -> None:
    # No teto, a confiança deixa de ordenar as linhas e a margem desempata;
    # fora dele, a ordenação usa a confiança.
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
    assert rows[0].why_held == WHY_HELD_NAME_ONLY


def test_the_resolver_disagreement_uses_a_decimal_comma() -> None:
    sheet = table(["A"], [["x"], ["y"]])
    rows = summarize_adjudication_queue(
        client_table=sheet,
        results=[_queue_result(1, "One", 1, "A", 0.88, llm_final_confidence=0.75)],
        final_threshold=0.90, sample_size=8,
    )
    assert rows[0].resolver_disagrees == RESOLVER_DISAGREES.format(llm_final="0,75")
    assert rows[0].resolver_disagrees == "confiança informada pelo resolver de IA: 0,75"


def test_the_report_names_the_step_that_carried_each_decision() -> None:
    # As etapas saem das mesmas pontuações em que a decisão foi tomada.
    base = [1000.0 + index for index in range(60)]
    model = table(["Base ICMS", "Aliquota ICMS", "Valor ICMS", "Série"], [])
    client = table(
        ["Base ICMS", "Aliq. ICMS", "Valor ICMS", "SERIE"],
        [[base[i], 18.0, round(base[i] * 0.18, 2), "1"] for i in range(60)],
    )
    results = map_columns(model, client, LexicalHeaderScorer(), None)
    by_name = {r.model_column: r for r in results}
    icms = by_name["Base ICMS"]
    # O trio apoia a escolha, mas o aceite veio do cabeçalho.
    assert icms.supported_by == "lexical + arithmetic"
    assert icms.decided_by == "lexical"
    assert icms.reason == REASON_PASS_ONE_ACCEPTED
    # uma coluna que só tem o cabeçalho diz isso, e só isso
    serie = by_name["Série"]
    assert serie.supported_by == "lexical"
    assert serie.decided_by == "lexical"


def test_an_offline_run_never_claims_the_semantic_step() -> None:
    # A etapa de cabeçalho é uma propriedade do avaliador.
    model = table(["Série"], [])
    client = table(["SERIE"], [["1"], ["2"]])
    results = map_columns(model, client, LexicalHeaderScorer(), None)
    assert all("semantic" not in (r.supported_by or "") for r in results)
    assert all("resolver" not in (r.supported_by or "") for r in results)


def test_a_non_discriminating_proof_is_not_reported_as_arithmetic_support() -> None:
    # Uma prova que não distingue a coluna confirma o escopo, não o mapeamento.
    steps = _supporting_step_ids(
        header_step="lexical",
        name_confidence=0.9,
        arithmetic_decisive=False,
    )
    assert steps == ["lexical"]


def test_a_decisive_proof_is_reported_as_arithmetic_support() -> None:
    steps = _supporting_step_ids(
        header_step="lexical", name_confidence=0.9, arithmetic_decisive=True
    )
    assert steps == ["lexical", "arithmetic"]


# ---------------------------------------------------------------------------
# Mensagens do relatório em português
# ---------------------------------------------------------------------------


def _cfop_catalogs():
    """Catálogo de CFOP em memória, para não depender de reference_data/."""
    from pathlib import Path

    from reference_catalog import CatalogSpec, LoadedCatalog, ReferenceCatalogs

    spec = CatalogSpec(
        name="cfop",
        file="cfop.csv",
        width=4,
        normalizer="cfop",
        specificity=1.0,
        source_version="teste",
        source_url="https://example.invalid/cfop",
        sha256="0" * 64,
    )
    codes = frozenset({"5102", "6102", "5405", "1102", "2102", "6108"})
    return ReferenceCatalogs(Path("teste"), {"cfop": LoadedCatalog(spec=spec, codes=codes)})


def test_a_catalog_decision_explains_itself_in_portuguese() -> None:
    # Seis dos sete códigos distintos são oficiais: 85,7%, com vírgula
    # decimal, e o catálogo pelo nome legível (CFOP), não pelo id interno.
    values = pd.Series(["5102", "6102", "5405", "1102", "2102", "6108", "5949"] * 6)
    column = ColumnRef("column_001", 0, "Cod. Fiscal", 1)
    evidence = _cfop_catalogs().analyze(values, "cfop")

    decision = _unique_distinctive_catalog_decision(
        candidates=[(column, values, 0.42)],
        catalog_evidence={0: [evidence]},
    )

    assert decision is not None
    assert decision.client_column_id == "column_001"
    assert decision.reason == REASON_CATALOG_DECISION.format(
        catalog="CFOP",
        distinct_coverage="85,7%",
        unique_formatted_count=7,
        coverage="85,7%",
    )
    assert decision.reason.startswith(
        "Na versão consultada da tabela oficial de CFOP, 85,7% dos 7 códigos distintos"
    )


def test_a_vacuous_proposal_names_the_column_in_portuguese() -> None:
    # A segunda ocorrência de 'Nº FCI' corresponde exatamente pelo cabeçalho e
    # está vazia: a trava de coluna vazia recusa o aceite e a justificativa
    # cita a coluna com o sufixo em português; em memória ele segue em inglês.
    model = table(["Nº FCI"], [["A1"], ["B2"]])
    client = table(["Outra", "Nº FCI", "Nº FCI"], [["x", "F1", None], ["y", "F2", None]])
    scorer = FixedScorer(np.array([[0.10, 0.50, 0.98]]))
    resolver = FixedResolver(
        {
            "Nº FCI": DataMappingDecision(
                client_column_id="column_003",
                data_confidence=0.9,
                final_confidence=0.95,
                reason="Same header.",
            )
        }
    )

    result = map_columns(model, client, scorer, resolver)[0]

    assert result.client_column == "Nº FCI (occurrence 2)"
    assert result.status == "review"
    assert result.vetoed_by == "vacuity"
    assert result.reason == REASON_VACUITY.format(client_column="Nº FCI (ocorrência 2)")
    assert result.reason.startswith("'Nº FCI (ocorrência 2)' não tem linhas preenchidas")


def test_an_offline_review_explains_itself_in_portuguese() -> None:
    model = table(["Base ICMS"], [[100.0]])
    client = table(["ICMS"], [[100.0]])
    result = map_columns(model, client, FixedScorer(np.array([[0.70]])), None)[0]
    assert result.method == "name_only"
    assert result.reason == REASON_OFFLINE


# ---------------------------------------------------------------------------
# Gravação no schema em português e ida e volta pelo report_schema
# ---------------------------------------------------------------------------

#: Colunas do revisor, acrescentadas em branco ao fim da aba Mapeamentos.
REVIEWER_FIELDS = ("reviewer_status", "correct_client_column", "reviewer_note")


def _result(**fields) -> MappingResult:
    base = dict(
        name_runner_up_confidence=0.0, name_margin=0.0, data_confidence=None,
        final_confidence=0.0, method="data", status="review", reason="",
    )
    base.update(fields)
    return MappingResult(**base)


def _varied_results() -> list[MappingResult]:
    """Linhas que cobrem cada situação, método, veto, catálogo, etapa e forma de nome de exibição."""
    decisive = _trio_proof_text(
        {"tax_scope": "icms", "rows_tested": 5570, "fit_score": 0.998, "alternatives": 1}
    )
    rival = _trio_proof_text(
        {"tax_scope": "pis", "rows_tested": 70, "fit_score": 0.9857, "alternatives": 2}
    )
    return [
        _result(
            model_column_number=1, model_column="CFOP", client_column_number=3,
            client_column="Cod. Fiscal", name_confidence=0.61, final_confidence=0.9,
            data_confidence=0.8571, method="catalog", status="accepted",
            reason=REASON_CATALOG_DECISION.format(
                catalog="CFOP", distinct_coverage="85,7%", unique_formatted_count=7,
                coverage="85,7%",
            ),
            catalog_name="cfop", catalog_coverage=0.8571, catalog_distinct_coverage=0.8571,
            catalog_confidence=0.8571, supported_by="lexical + catalog", decided_by="catalog",
        ),
        _result(
            model_column_number=2, model_column="Valor ICMS", client_column_number=12,
            client_column="Valor ICMS (occurrence 2)", name_confidence=1.0,
            name_runner_up_confidence=0.5, name_margin=0.5, final_confidence=1.0,
            method="name", status="accepted", reason=REASON_PASS_ONE_ACCEPTED,
            arithmetic_proof=decisive, supported_by="lexical + arithmetic",
            decided_by="arithmetic",
        ),
        _result(
            model_column_number=3, model_column="Base PIS", client_column_number=4,
            client_column="Unnamed column 4", name_confidence=0.88,
            name_runner_up_confidence=0.8, name_margin=0.08, final_confidence=0.88,
            method="name_only", reason=REASON_OFFLINE, arithmetic_proof=rival,
            supported_by="lexical", decided_by="lexical",
        ),
        _result(
            model_column_number=4, model_column="Município", client_column_number=7,
            client_column="Cidade", name_confidence=0.7, final_confidence=0.89,
            data_confidence=0.95, reason="The resolver's own prose stays in English.",
            catalog_name="ibge_municipality", catalog_coverage=0.97, catalog_confidence=0.97,
            supported_by="resolver + semantic + profile + identifier", decided_by="identifier",
            llm_data_confidence=0.9, llm_final_confidence=0.8,
        ),
        _result(
            model_column_number=5, model_column="Nº FCI", client_column_number=9,
            client_column="Nº FCI (occurrence 2)", name_confidence=0.98, name_margin=0.48,
            final_confidence=0.98, data_confidence=0.0,
            reason=REASON_VACUITY.format(client_column="Nº FCI (ocorrência 2)"),
            supported_by="resolver + semantic", decided_by="semantic", vetoed_by="vacuity",
            llm_data_confidence=0.9, llm_final_confidence=0.95,
        ),
        _result(
            model_column_number=6, model_column="Tipo de Imposto CBS", client_column_number=2,
            client_column="CBS Base", name_confidence=0.89, final_confidence=0.95,
            data_confidence=0.95,
            reason=REASON_CONFLICT_REVIEW.format(
                header_conflict=describe_header_conflict_pt("Tipo de Imposto CBS", "CBS Base")
            ),
            supported_by="semantic + profile", decided_by="profile",
            vetoed_by="family_conflict", catalog_name="ibs_cbs_classification",
        ),
        _result(
            model_column_number=7, model_column="cClassTrib", client_column_number=5,
            client_column="Class", name_confidence=0.5, final_confidence=0.5,
            method="data_error", reason="A comparação de dados falhou: RuntimeError: falhou",
        ),
        _result(
            model_column_number=8, model_column="Campo Inexistente", client_column_number=None,
            client_column=None, name_confidence=0.0, method="data", status="no_match",
            reason=REASON_UNSUPPORTED_NO_MATCH,
        ),
    ]


def _varied_unmatched() -> list[UnmatchedColumn]:
    def row(number, name, disposition, priority, signals, detail):
        return UnmatchedColumn(
            client_column_number=number, client_column=name, disposition=disposition,
            review_priority=priority, rows_with_value=40, distinct_values=12,
            signals=signals, signal_detail=detail, closest_model_column="Base ICMS",
            closest_name_confidence=0.4567,
        )

    return [
        row(1, "Base ICMS (occurrence 2)", "never_referenced", "shortlist", "arithmetic,catalog",
            SIGNAL_TRIO.format(slot="base de cálculo") + "; "
            + SIGNAL_CATALOG.format(distinct_coverage="88,2%", unique_formatted_count=17,
                                    catalog="CFOP")),
        row(2, "CNPJ", "proposed_not_accepted", "shortlist", "cnpj_checkdigit",
            SIGNAL_CHECKDIGIT.format(fraction="97,5%", kind="CNPJ")),
        row(3, "Chave", "never_referenced", "shortlist", "nfe_key_checkdigit",
            SIGNAL_CHECKDIGIT.format(fraction="100,0%", kind="chave de acesso da NF-e")),
        row(4, "CPF", "never_referenced", "shortlist", "cpf_checkdigit",
            SIGNAL_CHECKDIGIT.format(fraction="90,0%", kind="CPF")),
        row(5, "Unnamed column 5", "never_referenced", "low", "", SIGNAL_EMPTY),
        row(6, "Data", "never_referenced", "shortlist", "date", "contém datas"),
    ]


def _varied_queue() -> list[AdjudicationRow]:
    def row(rank, number, name, client_number, client_name, why, disagrees="", proof=""):
        return AdjudicationRow(
            rank=rank, model_column_number=number, model_column=name,
            proposed_client_column_number=client_number, proposed_client_column=client_name,
            final_confidence=0.8999, gate_distance=0.0001, name_margin=0.05,
            rows_with_value=0 if why == WHY_HELD_VACUOUS else 40,
            resolver_disagrees=disagrees, arithmetic_proof=proof,
            client_sample_values="1 | 2 | 3", why_held=why,
        )

    return [
        row(1, 3, "Base PIS", 4, "Unnamed column 4", WHY_HELD_NAME_ONLY,
            proof=_varied_results()[2].arithmetic_proof),
        row(2, 4, "Município", 7, "Cidade", WHY_HELD_BELOW_THRESHOLD,
            disagrees=RESOLVER_DISAGREES.format(llm_final="0,75")),
        row(3, 5, "Nº FCI", 9, "Nº FCI (occurrence 2)", WHY_HELD_VACUOUS),
    ]


def _is_missing(value) -> bool:
    return value is None or value == "" or (isinstance(value, float) and math.isnan(value))


def _assert_same_cell(expected, actual, where) -> None:
    if _is_missing(expected) or _is_missing(actual):
        assert _is_missing(expected) and _is_missing(actual), (where, expected, actual)
    elif isinstance(expected, (int, float)) and not isinstance(expected, bool):
        assert math.isclose(float(expected), float(actual)), (where, expected, actual)
    else:
        assert expected == actual, (where, expected, actual)


def _assert_round_trip(rows, frame: pd.DataFrame, table_name: str, extra=()) -> None:
    """Verifica se gravar e ler os resultados preserva os valores de ``asdict``.

    Campos estruturados retornam à representação interna. Mensagens e valores
    numéricos devem ser preservados durante a conversão pelo ``report_schema``.
    """
    internal = rs.to_internal_frame(frame, table_name)
    expected = [asdict(row) for row in rows]
    fields = list(expected[0]) + list(extra)
    assert list(internal.columns) == fields
    assert len(internal) == len(expected)
    for position, record in enumerate(expected):
        for field in fields:
            _assert_same_cell(
                record.get(field, ""), internal[field].iloc[position], (table_name, position, field)
            )


def test_write_results_round_trips_through_report_schema(tmp_path) -> None:
    results = _varied_results()
    unmatched = _varied_unmatched()
    queue = _varied_queue()
    output = tmp_path / "relatorio.xlsx"

    write_results(
        results, output, metadata={"mapping_schema_version": MAPPING_SCHEMA_VERSION},
        unmatched=unmatched, queue=queue,
    )

    workbook = pd.ExcelFile(output)
    assert workbook.sheet_names == [
        "Mapeamentos", "Metadados da Execução", "Colunas do Cliente sem Uso", "Fila de Revisão",
    ]
    _assert_round_trip(
        results, pd.read_excel(workbook, sheet_name="Mapeamentos"), "mappings",
        extra=REVIEWER_FIELDS,
    )
    _assert_round_trip(
        unmatched, pd.read_excel(workbook, sheet_name="Colunas do Cliente sem Uso"), "unmatched"
    )
    _assert_round_trip(queue, pd.read_excel(workbook, sheet_name="Fila de Revisão"), "queue")


def test_the_written_cells_are_in_portuguese(tmp_path) -> None:
    output = tmp_path / "relatorio.xlsx"
    write_results(
        _varied_results(), output, unmatched=_varied_unmatched(), queue=_varied_queue()
    )
    mappings = pd.read_excel(output, sheet_name="Mapeamentos")
    assert list(mappings.columns) == list(rs.COLUMNS["mappings"].values())
    assert list(mappings["situacao"]) == [
        "aceito", "aceito", "revisao", "revisao", "revisao", "revisao", "revisao",
        "sem_correspondencia",
    ]
    assert list(mappings["metodo"]) == [
        "catalogo", "nome", "somente_nome", "dados", "dados", "dados", "erro_dados", "dados",
    ]
    assert list(mappings["coluna_cliente"].fillna("")) == [
        "Cod. Fiscal", "Valor ICMS (ocorrência 2)", "Coluna sem nome 4", "Cidade",
        "Nº FCI (ocorrência 2)", "CBS Base", "Class", "",
    ]
    assert list(mappings["apoiado_por"].fillna("")) == [
        "lexico + catalogo", "lexico + aritmetica", "lexico",
        "resolvedor_ia + semantico + perfil + identificador", "resolvedor_ia + semantico",
        "semantico + perfil", "", "",
    ]
    assert list(mappings["decidido_por"].fillna("")) == [
        "catalogo", "aritmetica", "lexico", "identificador", "semantico", "perfil", "", "",
    ]
    assert list(mappings["vetado_por"].fillna("")) == [
        "", "", "", "", "coluna_vazia", "conflito_tributario", "", "",
    ]
    assert list(mappings["nome_catalogo"].fillna("")) == [
        "cfop", "", "", "municipio_ibge", "", "cclasstrib", "", "",
    ]
    # O texto da prova é preservado; leitores usam os campos de evidência.
    proofs = list(mappings["prova_aritmetica"].fillna(""))
    assert proofs == [result.arithmetic_proof or "" for result in _varied_results()]

    unmatched = pd.read_excel(output, sheet_name="Colunas do Cliente sem Uso")
    assert list(unmatched.columns) == list(rs.COLUMNS["unmatched"].values())
    assert list(unmatched["sinais"].fillna("")) == [
        "aritmetica,catalogo", "dv_cnpj", "dv_chave_nfe", "dv_cpf", "", "data",
    ]
    assert set(unmatched["disposicao"]) == {"nunca_referenciada", "proposta_nao_aceita"}
    assert set(unmatched["prioridade_revisao"]) == {"prioritaria", "baixa"}
    assert list(unmatched["coluna_cliente"])[0] == "Base ICMS (ocorrência 2)"
    assert list(unmatched["coluna_cliente"])[4] == "Coluna sem nome 5"

    queue = pd.read_excel(output, sheet_name="Fila de Revisão")
    assert list(queue.columns) == list(rs.COLUMNS["queue"].values())
    assert list(queue["coluna_cliente_proposta"]) == [
        "Coluna sem nome 4", "Cidade", "Nº FCI (ocorrência 2)",
    ]
    assert list(queue["motivo_retencao"]) == [
        WHY_HELD_NAME_ONLY, WHY_HELD_BELOW_THRESHOLD, WHY_HELD_VACUOUS,
    ]


def test_empty_auxiliary_sheets_keep_their_portuguese_headers(tmp_path) -> None:
    # Uma lista vazia ainda grava a aba: ela afirma que nada ficou de fora. A
    # linha em branco que leva os cabeçalhos sai com células vazias, então a
    # leitura devolve só o cabeçalho.
    output = tmp_path / "relatorio.xlsx"
    write_results(_varied_results(), output, unmatched=[], queue=[])

    workbook = pd.ExcelFile(output)
    assert workbook.sheet_names == [
        "Mapeamentos", "Colunas do Cliente sem Uso", "Fila de Revisão",
    ]
    for sheet, table_name in (
        ("Colunas do Cliente sem Uso", "unmatched"),
        ("Fila de Revisão", "queue"),
    ):
        frame = pd.read_excel(workbook, sheet_name=sheet)
        assert list(frame.columns) == list(rs.COLUMNS[table_name].values())
        assert frame.empty
        internal = rs.to_internal_frame(frame, table_name)
        assert list(internal.columns) == list(rs.COLUMNS[table_name])


def test_a_csv_report_uses_the_portuguese_schema_too(tmp_path) -> None:
    results = _varied_results()
    output = tmp_path / "relatorio.csv"

    write_results(results, output)

    frame = pd.read_csv(output)
    assert list(frame.columns) == list(rs.COLUMNS["mappings"].values())
    assert frame["situacao"].iloc[0] == "aceito"
    _assert_round_trip(results, frame, "mappings", extra=REVIEWER_FIELDS)


def test_an_unknown_output_extension_is_refused_in_portuguese(tmp_path) -> None:
    with pytest.raises(ValueError, match="A saída deve terminar em .xlsx ou .csv"):
        write_results(_varied_results(), tmp_path / "relatorio.txt")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _write_sheet(path, rows) -> None:
    pd.DataFrame(rows).to_excel(path, header=False, index=False)


def test_the_cli_help_is_in_portuguese() -> None:
    parser = build_parser()
    text = parser.format_help()
    assert text.startswith("uso: ")
    assert "argumentos posicionais:" in text
    assert "opções:" in text
    assert "mostra esta mensagem de ajuda e sai" in text
    for english in ("usage:", "positional arguments", "options:", "show this help"):
        assert english not in text, english
    # Toda opção tem ajuda, inclusive as que não tinham nenhuma.
    assert all(action.help for action in parser._actions)
    # As flags continuam em inglês.
    for flag in ("--offline", "--output", "--name-threshold", "--no-reference-data"):
        assert flag in text


def test_main_writes_a_portuguese_report(tmp_path, capsys) -> None:
    model = tmp_path / "modelo.xlsx"
    client = tmp_path / "cliente.xlsx"
    output = tmp_path / "relatorio.xlsx"
    _write_sheet(model, [["CFOP", "Valor Frete"], ["5102", 10.0]])
    _write_sheet(client, [["CFOP", "Frete"], ["5102", 10.0]])

    code = main(
        [str(client), "--model", str(model), "--output", str(output),
         "--offline", "--no-reference-data"]
    )

    assert code == 0
    out = capsys.readouterr().out
    assert f"Arquivo gravado: {output}" in out
    assert "aceito=1 revisao=1 sem_correspondencia=0" in out
    workbook = pd.ExcelFile(output)
    assert workbook.sheet_names == [
        "Mapeamentos", "Metadados da Execução", "Colunas do Cliente sem Uso", "Fila de Revisão",
    ]
    metadata = dict(
        pd.read_excel(workbook, sheet_name="Metadados da Execução")[["chave", "valor"]].values
    )
    assert metadata["versao_esquema_mapeamento"] == 3
    assert metadata["modelo_embedding"] == "offline"
    assert metadata["modelo_ia"] == "offline"
    assert metadata["sha256_manifesto_referencia"] == "desativado"
    assert set(metadata) == set(rs.METADATA_KEYS.values()) - {"migrado_da_versao_esquema"}


def test_main_rejects_a_threshold_out_of_range_in_portuguese(tmp_path) -> None:
    with pytest.raises(SystemExit) as stop:
        main([str(tmp_path / "cliente.xlsx"), "--name-threshold", "1.5", "--offline"])
    assert stop.value.code == "--name-threshold deve estar entre 0 e 1"


def test_main_reports_a_missing_file_in_portuguese_without_a_traceback(tmp_path) -> None:
    model = tmp_path / "modelo.xlsx"
    _write_sheet(model, [["CFOP"], ["5102"]])
    missing = tmp_path / "nao_existe.xlsx"

    with pytest.raises(SystemExit) as stop:
        main([str(missing), "--model", str(model), "--offline", "--no-reference-data"])

    # SystemExit com texto: o Python imprime a mensagem e sai com código 1.
    assert stop.value.code == f"Arquivo não encontrado: {missing}"


def test_main_reports_a_bad_header_row_in_portuguese(tmp_path) -> None:
    model = tmp_path / "modelo.xlsx"
    client = tmp_path / "cliente.xlsx"
    _write_sheet(model, [["CFOP"], ["5102"]])
    _write_sheet(client, [["CFOP"], ["5102"]])

    with pytest.raises(SystemExit) as stop:
        main(
            [str(client), "--model", str(model), "--client-header-row", "5",
             "--offline", "--no-reference-data", "--output", str(tmp_path / "r.xlsx")]
        )

    assert stop.value.code == (
        "Não foi possível gerar o relatório de mapeamento: A linha de cabeçalho 5 "
        f"está fora das linhas da planilha: {client}"
    )
