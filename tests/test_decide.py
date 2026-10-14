"""Testes de ``mapper/decide.py``: passagens, substituições locais, confiança e etapas.

As justificativas dos fake resolvers ficam em inglês porque fazem o papel da
resposta da IA, que continua em inglês.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np
import pandas as pd
import pytest

from mapper import decide
from mapper.catalogs import ReferenceCatalogs
from mapper.decide import (
    EVIDENCE_STEPS,
    GUARDS,
    REASON_CATALOG_DECISION,
    REASON_CONFLICT_REVIEW,
    REASON_OFFLINE,
    REASON_OVERLAP,
    REASON_PASS_ONE_ACCEPTED,
    REASON_PRESERVE_BEST,
    REASON_SOURCE_COMPETITION,
    REASON_UNSUPPORTED_NO_MATCH,
    REASON_VACUITY,
    STEPS_BY_ID,
    TRANSFERRING_STEP_IDS,
    MapperConfig,
    _evidence_attribution,
    _supporting_step_ids,
    _unique_distinctive_catalog_decision,
    deciding_step,
    format_steps,
    map_columns,
    rests_only_on_headers,
)
from mapper.headers import LexicalHeaderScorer
from mapper.resolver import DataMappingDecision, OpenAIDataResolver
from mapper.spreadsheet import ColumnRef

from support import (
    FailingResolver,
    FixedResolver,
    FixedScorer,
    Scores,
    table,
    table_from_headers,
)

REFERENCE_DATA = Path(__file__).parents[1] / "reference_data"


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


def test_the_report_names_the_step_that_carried_each_decision() -> None:
    # As etapas saem dos mesmos scores em que a decisão foi tomada.
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

    from mapper.catalogs import CatalogSpec, LoadedCatalog, ReferenceCatalogs

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
    # está vazia: o guard de coluna vazia recusa o aceite e a justificativa
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


def test_distinctive_catalog_makes_bogus_header_a_viable_review_without_llm() -> None:
    catalogs = ReferenceCatalogs.from_directory(REFERENCE_DATA)
    ncm_codes = sorted(catalogs.catalogs["ncm"].codes)[:5]
    model = table(["Nº do NCM"], [[None] for _ in ncm_codes])
    client = table(
        ["Likely by name", "X9"],
        [[f"INV-{index}", code] for index, code in enumerate(ncm_codes, start=1)],
    )
    scorer = FixedScorer(np.array([[0.80, 0.10]]))
    resolver = FixedResolver(
        {
            "Nº do NCM": DataMappingDecision(
                client_column_id=None,
                data_confidence=0.0,
                final_confidence=0.0,
                reason="No LLM decision should be necessary.",
            )
        }
    )

    result = map_columns(
        model,
        client,
        scorer,
        resolver,
        MapperConfig(candidate_count=1),
        reference_catalogs=catalogs,
    )[0]

    assert resolver.calls == []
    assert result.client_column == "X9"
    assert result.method == "catalog"
    assert result.status == "review"
    assert result.catalog_name == "ncm"
    assert result.catalog_coverage == 1.0
    assert result.catalog_confidence == 1.0


def test_short_overlapping_cst_domain_does_not_bypass_resolver() -> None:
    catalogs = ReferenceCatalogs.from_directory(REFERENCE_DATA)
    model = table(["CST PIS"], [["01"], ["02"], ["03"], ["04"], ["05"]])
    client = table(["X1"], [["01"], ["02"], ["03"], ["04"], ["05"]])
    scorer = FixedScorer(np.array([[0.20]]))
    resolver = FixedResolver(
        {
            "CST PIS": DataMappingDecision(
                client_column_id="column_001",
                data_confidence=0.80,
                final_confidence=0.70,
                reason="Codes are CST-like, but the tax family is not identified.",
            )
        }
    )

    result = map_columns(
        model,
        client,
        scorer,
        resolver,
        reference_catalogs=catalogs,
    )[0]

    assert resolver.calls == ["CST PIS"]
    assert result.status == "no_match"


@pytest.mark.parametrize("reverse", [False, True])
def test_stronger_header_claim_wins_independently_of_target_order(reverse):
    headers = ["Target A", "Target B"]
    if reverse:
        headers.reverse()
    results = map_columns(
        table_from_headers(headers, [[1, 2]]), table_from_headers(["Source X", "Source Y"], [[11, 22]]),
        Scores({"Target A": [.91, .70], "Target B": [.99, .70]}), None,
    )
    accepted = {r.model_column: r.client_column for r in results if r.status == "accepted"}
    assert accepted == {"Target B": "Source X"}


@pytest.mark.parametrize("reverse", [False, True])
def test_ambiguous_header_claims_stay_for_review_online(reverse):
    headers = ["Target A", "Target B"][::(-1 if reverse else 1)]
    class Resolver:
        def resolve(self, **kwargs):
            return DataMappingDecision(client_column_id="column_001", data_confidence=.99,
                                              final_confidence=.99, reason="Shared proposal.")
    results = map_columns(
        table_from_headers(headers, [[1, 2]]), table_from_headers(["Source X", "Source Y"], [[11, 22]]),
        Scores({"Target A": [.95, .70], "Target B": [.94, .70]}), Resolver(),
    )
    assert all(r.status == "review" for r in results)
    assert all(r.reason == REASON_SOURCE_COMPETITION for r in results)


def test_unique_arithmetic_succeeds_without_a_resolver_call():
    base = [100., 200., 300., 400., 500.] * 4
    weaker = [100., 200., 300., 400., 625.] * 4
    rate = [10.] * 20
    value = [10., 20., 30., 40., 50.] * 4
    result = map_columns(
        table_from_headers(["Base ICMS", "Aliquota ICMS", "Valor ICMS"], list(zip(base, rate, value))),
        table_from_headers(["X1", "X2", "ICMS Taxa", "ICMS Valor"], list(zip(base, weaker, rate, value))),
        Scores({"Base ICMS": [.84, .86, .20, .20], "Aliquota ICMS": [.20, .20, .96, .20],
                "Valor ICMS": [.20, .20, .20, .96]}), FailingResolver(),
    )[0]
    assert (result.client_column, result.status) == ("X1", "accepted")
    assert result.llm_final_confidence is None
    assert "resolver" not in result.supported_by


@pytest.mark.parametrize("reverse", [False, True])
@pytest.mark.parametrize("weaker,expected", [(.75, {"Target B": "Source X"}), (.87, {})])
def test_second_pass_compares_all_source_claims_before_accepting(reverse, weaker, expected):
    headers = ["Target A", "Target B"][::(-1 if reverse else 1)]
    class Resolver:
        def resolve(self, **kwargs):
            return DataMappingDecision(client_column_id="column_001", data_confidence=.99,
                                              final_confidence=.99, reason="Shared proposal.")
    values = ["distinctive first example", "distinctive second example", "distinctive third example"]
    results = map_columns(
        table_from_headers(headers, [[value, value] for value in values]),
        table_from_headers(["Source X", "Source Y"], [[value, "other data"] for value in values]),
        Scores({"Target A": [weaker, .4], "Target B": [.88, .4]}), Resolver(),
    )
    accepted = {r.model_column: r.client_column for r in results if r.status == "accepted"}
    assert accepted == expected
    assert all(r.client_column == "Source X" for r in results)


@pytest.mark.parametrize("reverse", [False, True])
def test_withdrawn_competing_proposal_does_not_veto_the_remaining_claim(reverse):
    headers = ["Target A", "Target B"][::(-1 if reverse else 1)]
    class Resolver:
        def resolve(self, **kwargs):
            chosen = "column_001" if kwargs["model_column"].name == "Target A" else None
            return DataMappingDecision(client_column_id=chosen, data_confidence=.99 if chosen else 0,
                                              final_confidence=.99 if chosen else 0, reason="Fixed proposal.")
    results = map_columns(
        table_from_headers(headers, [[f"first {n}", f"second {n}"] for n in range(30)]),
        table_from_headers(["Source X", "Source Y"], [[f"third {n}", f"fourth {n}"] for n in range(30)]),
        Scores({"Target A": [.9414, .7], "Target B": [.9116, .7]}), Resolver(),
    )
    assert {r.model_column: r.status for r in results} == {"Target A": "accepted", "Target B": "no_match"}


@pytest.mark.parametrize("reverse", [False, True])
def test_newly_confirmed_values_enable_local_arithmetic_without_more_api_calls(reverse):
    base = [1e9 * n for n in range(1, 31)]
    values = {"Valor ICMS": [b * .1 for b in base], "Aliquota ICMS": [10.] * 30,
              "Base ICMS": [None] * 30}
    headers = list(values)[::(-1 if reverse else 1)]
    calls = []
    class Resolver:
        def resolve(self, **kwargs):
            name = kwargs["model_column"].name
            calls.append(name)
            return DataMappingDecision(
                client_column_id={"Valor ICMS": "column_001", "Base ICMS": "column_003"}[name],
                data_confidence=.99, final_confidence=.99, reason="Fixed proposal.")
    results = map_columns(
        table_from_headers(headers, list(zip(*(values[h] for h in headers)))),
        table_from_headers(["ICMS Valor", "ICMS Taxa", "ICMS Base"],
              list(zip(values["Valor ICMS"], values["Aliquota ICMS"], base))),
        Scores({"Valor ICMS": [.85, .1, .1], "Aliquota ICMS": [.1, .98, .1],
                "Base ICMS": [.1, .1, .85]}), Resolver(),
    )
    by_name = {r.model_column: r for r in results}
    assert all(r.status == "accepted" for r in results)
    assert len({r.client_column_number for r in results}) == 3
    assert sorted(calls) == ["Base ICMS", "Valor ICMS"]
    assert by_name["Base ICMS"].final_confidence == .94
    assert by_name["Base ICMS"].decided_by == "arithmetic"
    assert by_name["Valor ICMS"].decided_by == "profile"
    assert by_name["Aliquota ICMS"].decided_by == "semantic"
    assert "arithmetic" in by_name["Valor ICMS"].supported_by


@pytest.mark.parametrize("confidence", [0.0, 0.01, 0.99])
def test_local_identifier_evidence_does_not_depend_on_resolver_confidence(confidence):
    model = table_from_headers(["Nº ID fiscal 1"], [["model A"], ["model B"], ["model C"]])
    client = table_from_headers(["Source X"], [["52998224725"], ["11144477735"], ["52998224725"]])
    raw = DataMappingDecision(client_column_id="column_001", data_confidence=confidence,
                                     final_confidence=confidence, reason="Controlled API response")
    api = SimpleNamespace(responses=SimpleNamespace(parse=lambda **kwargs: SimpleNamespace(output_parsed=raw)))
    result = map_columns(model, client, Scores({"Nº ID fiscal 1": [.8]}),
                                OpenAIDataResolver(api))[0]
    assert (result.status, result.client_column_number, result.decided_by) == ("accepted", 1, "identifier")
    assert result.final_confidence == .92
    assert result.llm_final_confidence == confidence
    assert result.llm_data_confidence == confidence


def test_weak_rows_preserve_raw_resolver_confidence_in_the_audit():
    raw = DataMappingDecision(client_column_id="column_001", data_confidence=.1,
                                     final_confidence=.1, reason="Weak data")
    api = SimpleNamespace(responses=SimpleNamespace(parse=lambda **kwargs: SimpleNamespace(output_parsed=raw)))
    result = map_columns(table_from_headers(["Target A"], [[0], [0], [0]]),
                                table_from_headers(["Source X"], [[0], [0], [0]]),
                                Scores({"Target A": [.88]}), OpenAIDataResolver(api))[0]
    assert result.llm_final_confidence == .1
    assert result.llm_data_confidence == .1
    assert result.final_confidence == .88
    assert result.status == "review"


@pytest.mark.parametrize("alternatives, expected", [(1, "lexical + arithmetic"), (2, "lexical")])
def test_arithmetic_support_is_independent_of_explanation_wording(alternatives, expected):
    run = SimpleNamespace(client_tax_trios={}, header_step="lexical")
    column = ColumnRef("target_001", 0, "Valor PIS", 1)
    with patch.object(decide, "trio_corroboration", return_value={"alternatives": alternatives}), \
         patch.object(decide, "_trio_proof_text", return_value="Uma explicação reescrita livremente"):
        evidence = _evidence_attribution(run, column, 0, decided_by="lexical", name_confidence=.95)
    assert evidence["supported_by"] == expected
    assert evidence["arithmetic_proof"] == "Uma explicação reescrita livremente"


@pytest.mark.parametrize("confidence", [0.0, .99])
def test_joint_arithmetic_acceptance_uses_local_evidence(confidence):
    rows = [[100+10*i, 1.65, (100+10*i)*.0165, (100+10*i)*.0165] for i in range(40)]
    model = table_from_headers(["Base PIS", "Aliquota PIS", "Valor PIS"], [row[:3] for row in rows])
    client = table_from_headers(["PIS base", "PIS rate", "Candidate A", "Candidate B"], rows)
    class Resolver:
        def resolve(self, **kwargs):
            return DataMappingDecision(client_column_id="column_003", data_confidence=confidence,
                                               final_confidence=confidence, reason="Selected candidate")
    result = map_columns(model, client, Scores({"Base PIS": [.99,.1,.1,.1],
        "Aliquota PIS": [.1,.99,.1,.1], "Valor PIS": [.1,.1,.89,.7]}), Resolver())[2]
    assert (result.status, result.client_column_number, result.final_confidence) == ("accepted", 3, .956)
    assert result.decided_by == "arithmetic"


@pytest.mark.parametrize("column_id", [None, "unknown_column"])
def test_zero_confidence_fix_does_not_invent_a_selected_column(column_id):
    class Resolver:
        def resolve(self, **kwargs):
            return DataMappingDecision(client_column_id=column_id, data_confidence=0,
                                               final_confidence=0, reason="No selection")
    result = map_columns(table_from_headers(["Nº ID fiscal 1"], [["model"]]),
        table_from_headers(["Source X"], [["52998224725"]]), Scores({"Nº ID fiscal 1": [.8]}), Resolver())[0]
    assert result.status == "no_match"
    assert result.client_column_number is None


@pytest.mark.parametrize("reverse", [False, True])
def test_zero_confidence_candidates_still_participate_in_source_competition(reverse):
    headers = ["Target A", "Target B"][::(-1 if reverse else 1)] + ["Anchor"]
    model = table_from_headers(headers, [["model A", "model B", "anchor"]])
    client = table_from_headers(["Source X", "Source Y"], [["source X", "source Y"]])
    class Resolver:
        def resolve(self, **kwargs):
            confidence = 0 if kwargs["model_column"].name == "Target A" else .5
            return DataMappingDecision(client_column_id="column_001", data_confidence=confidence,
                                               final_confidence=confidence, reason="Proposal")
    results = map_columns(model, client, Scores({
        "Target A": [.9009, .89], "Target B": [.92, .89], "Anchor": [.2, .99],
    }), Resolver())
    by_name = {r.model_column: r for r in results}
    assert by_name["Anchor"].status == "accepted"
    for name in ["Target A", "Target B"]:
        assert by_name[name].status == "review"
        assert by_name[name].vetoed_by == "exclusivity"
        assert by_name[name].decided_by == ""


def test_every_step_id_is_unique_across_evidence_and_guards() -> None:
    ids = [step.id for step in (*EVIDENCE_STEPS, *GUARDS)]
    assert len(ids) == len(set(ids)) == len(STEPS_BY_ID)


def test_deciding_step_returns_the_highest_priority_evidence() -> None:
    # A prioridade deste helper não identifica qual score venceu no mapper.
    assert deciding_step(["lexical", "arithmetic"]) == "arithmetic"
    assert deciding_step(["semantic", "catalog"]) == "catalog"
    assert deciding_step([]) == ""


def test_the_resolver_never_outranks_objective_evidence() -> None:
    for objective in ("lexical", "profile", "identifier", "catalog", "arithmetic"):
        assert deciding_step(["resolver", objective]) == objective


def test_the_resolver_alone_does_not_make_a_mapping_transferable() -> None:
    # O resolver é um árbitro, não uma evidência.
    assert "resolver" not in TRANSFERRING_STEP_IDS
    assert rests_only_on_headers(["resolver"]) is True
    assert rests_only_on_headers(["resolver", "lexical", "semantic"]) is True
    assert rests_only_on_headers(["resolver", "arithmetic"]) is False


def test_header_bound_steps_are_exactly_the_ones_reading_client_words() -> None:
    header_bound = {s.id for s in EVIDENCE_STEPS if not s.transfers}
    assert header_bound == {"lexical", "semantic", "resolver"}


def test_supporting_steps_render_strongest_last() -> None:
    assert format_steps(["arithmetic", "lexical"]) == "lexical + arithmetic"
    # A célula montada é o que o relatório guarda, e ela faz a ida e volta.
    assert deciding_step(format_steps(["arithmetic", "lexical"])) == "arithmetic"


#: (célula interna, deciding_step, format_steps).
#: ``unrecorded``, ``catalog (agrees)`` e IDs traduzidos não são evidências.
STEP_CELLS: tuple[tuple[str, str, str], ...] = (
    ("arithmetic", "arithmetic", "arithmetic"),
    ("resolver", "resolver", "resolver"),
    ("lexical + catalog", "catalog", "lexical + catalog"),
    (
        "resolver + lexical + semantic",
        "semantic",
        "resolver + lexical + semantic",
    ),
    ("profile + identifier", "identifier", "profile + identifier"),
    # Fora de ordem: format_steps reordena, com a mais forte por último.
    ("arithmetic + lexical", "arithmetic", "lexical + arithmetic"),
    (
        "lexical + semantic + profile + identifier + catalog + arithmetic",
        "arithmetic",
        "lexical + semantic + profile + identifier + catalog + arithmetic",
    ),
    ("semantic + catalog (agrees)", "semantic", "semantic"),
    ("semantic + unrecorded", "semantic", "semantic"),
    (
        "resolver + semantic + unrecorded",
        "semantic",
        "resolver + semantic",
    ),
    ("unrecorded", "", ""),
    ("lexico + semantico + aritmetica", "", ""),
)


@pytest.mark.parametrize(
    ("internal", "decided", "formatted"),
    STEP_CELLS,
    ids=[case[0] for case in STEP_CELLS],
)
def test_step_cells_preserve_priority_and_ignore_non_evidence(
    internal: str, decided: str, formatted: str
) -> None:
    assert deciding_step(internal) == decided
    assert format_steps(internal) == formatted
