"""Testes de ``mapper/catalogs.py``: escolha dos catálogos, normalização e análise dos códigos."""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from mapper.catalogs import ReferenceCatalogs, expected_catalogs, normalize_code_value

REFERENCE_DATA = Path(__file__).parents[1] / "reference_data"


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


def test_distinct_coverage_ignores_how_often_a_code_repeats() -> None:
    # Dois códigos, um oficial e outro não, mas o não oficial domina as
    # linhas. A cobertura por linha diminui; a cobertura distinta, não.
    catalogs = ReferenceCatalogs.from_directory(REFERENCE_DATA)
    series = pd.Series(["5102"] * 2 + ["9999"] * 98)
    analysis = catalogs.analyze(series, "cfop")
    assert analysis["coverage"] == pytest.approx(0.02)
    assert analysis["distinct_coverage"] == pytest.approx(0.5)


@pytest.mark.parametrize("header,catalog", [
    ("Tipo de Imposto CBS", "ibs_cbs_cst"), ("Tipo de Imposto IBSM", "ibs_cbs_cst"),
    ("Tipo de Imposto PIS", "cst_pis_cofins"), ("Tipo de Imposto IPI", "cst_ipi"),
])
def test_canonical_tax_status_fields_have_explicit_catalogs(header, catalog):
    assert catalog in expected_catalogs(header)
