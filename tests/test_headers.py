"""Testes de ``mapper/headers.py``: normalização, papéis, tributos, conflitos e scorers."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from mapper.headers import (
    _MIN_ROLE_PREFIX,
    _ROLE_CONCEPT_WORDS,
    OpenAIEmbeddingHeaderScorer,
    describe_header_conflict_pt,
    expand_truncated_role_words,
    header_semantic_role,
    header_tax_families,
    semantic_header_conflict,
    semantic_header_signature,
    underspecified_header_match,
)


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
