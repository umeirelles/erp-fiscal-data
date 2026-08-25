from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from invoice_mapper import (
    ColumnRef,
    DataMappingDecision,
    MapperConfig,
    SheetTable,
    map_columns,
)
from reference_catalog import (
    ReferenceCatalogs,
    expected_catalogs,
    normalize_code_value,
)


REFERENCE_DATA = Path(__file__).parents[1] / "reference_data"


def table(headers: list[str], rows: list[list[object]]) -> SheetTable:
    columns = [
        ColumnRef(id=f"column_{index + 1:03d}", index=index, name=name, occurrence=1)
        for index, name in enumerate(headers)
    ]
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


def test_expected_catalogs_are_driven_by_canonical_business_terms() -> None:
    assert expected_catalogs("CFOP") == ("cfop",)
    assert expected_catalogs("Nº do NCM") == ("ncm",)
    assert expected_catalogs("CST PIS") == ("cst_pis_cofins",)
    assert expected_catalogs("CST ICMS") == (
        "cst_icms",
        "cst_icms_taxation",
        "csosn",
    )
    assert expected_catalogs("Código Class Tributária para CBS e IBS") == (
        "ibs_cbs_classification",
    )
    assert expected_catalogs("Material interno") == ()


def test_code_normalization_preserves_leading_zeros_and_erp_cfop_suffixes() -> None:
    assert normalize_code_value(1, width=2, normalizer="digits") == "01"
    assert normalize_code_value("01", width=2, normalizer="digits") == "01"
    assert normalize_code_value("5102/AA", width=4, normalizer="cfop") == "5102"
    assert normalize_code_value("8516.79.10", width=8, normalizer="punctuated_digits") == (
        "85167910"
    )
    assert normalize_code_value("ABC5102", width=4, normalizer="cfop") is None


def test_published_reference_snapshot_loads_and_contains_known_codes() -> None:
    catalogs = ReferenceCatalogs.from_directory(REFERENCE_DATA)

    assert "5102" in catalogs.catalogs["cfop"].codes
    assert "85167910" in catalogs.catalogs["ncm"].codes
    assert "3550308" in catalogs.catalogs["ibge_municipality"].codes
    assert "000001" in catalogs.catalogs["ibs_cbs_classification"].codes
    assert "101" in catalogs.catalogs["csosn"].codes


def test_catalog_evidence_reports_coverage_and_rejects_all_zero_cst() -> None:
    catalogs = ReferenceCatalogs.from_directory(REFERENCE_DATA)
    ncm = catalogs.analyze(
        pd.Series(["85167910", "73211100", "8516.79.10", "not-an-ncm"]),
        "ncm",
    )
    zero_cst = catalogs.analyze(pd.Series([0, 0, 0, 0, 0]), "ibs_cbs_cst")

    assert ncm["valid_count"] == 3
    assert ncm["coverage"] == 0.75
    assert ncm["invalid_examples"] == ["not-an-ncm"]
    assert zero_cst["coverage"] == 1.0
    assert zero_cst["all_zero_only"] is True
    assert zero_cst["score"] == 0.0


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


def test_distinct_coverage_ignores_how_often_a_code_repeats() -> None:
    # Two codes, one official and one not, but the unofficial one dominates
    # the rows. Row coverage collapses; distinct coverage does not.
    catalogs = ReferenceCatalogs.from_directory(REFERENCE_DATA)
    series = pd.Series(["5102"] * 2 + ["9999"] * 98)
    analysis = catalogs.analyze(series, "cfop")
    assert analysis["coverage"] == pytest.approx(0.02)
    assert analysis["distinct_coverage"] == pytest.approx(0.5)
