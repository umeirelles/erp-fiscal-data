"""Testes de ``mapper/evidence.py``: perfis, dígitos verificadores e aritmética fiscal."""

from __future__ import annotations

import pandas as pd
import pytest

from mapper.evidence import (
    _trio_proof_text,
    client_column_is_vacuous,
    discover_client_tax_trios,
    identifier_match_score,
    line_calculation_evidence,
    profile_similarity,
    sample_values,
    summarize_values,
    tax_calculation_evidence,
    trio_corroboration,
)
from mapper.schema import target_field
from mapper.spreadsheet import ColumnRef, SheetTable

from support import table


def test_sample_values_preserves_strings_and_removes_duplicates() -> None:
    values = pd.Series([None, "", "000051789", "000051789", "001", 12.5])

    assert sample_values(values, limit=3) == ["000051789", "001", "12.5"]


def test_sample_values_are_independent_of_row_order() -> None:
    values = pd.Series([0, 100, 25, 50, 75, 100, 125, 150, 175, 200])

    assert sample_values(values, limit=6) == sample_values(
        values.iloc[::-1].reset_index(drop=True), limit=6
    )


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


@pytest.mark.parametrize("cpf,cnpj", [(0, 1), (1, 0), (.5, .5)])
def test_allowed_identifiers_do_not_depend_on_target_sample_mix(cpf, cnpj):
    kinds = target_field("Nº ID fiscal 1").identifier_kinds
    source = dict(valid_cpf_fraction=cpf, valid_cnpj_fraction=cnpj)
    for sample in ({}, dict(valid_cpf_fraction=.914, valid_cnpj_fraction=.086)):
        assert identifier_match_score(sample, source, kinds=kinds) == 1
    assert identifier_match_score({}, source, kinds=()) == 0
