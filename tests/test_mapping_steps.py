"""As etapas nomeadas são um contrato: os relatórios, os testes e o README as citam."""

from __future__ import annotations

import pytest

import report_schema
from mapping_steps import (
    EVIDENCE_STEPS,
    GUARDS,
    STEPS_BY_ID,
    TRANSFERRING_STEP_IDS,
    deciding_step,
    format_steps,
    rests_only_on_headers,
)


def test_every_step_id_is_unique_across_evidence_and_guards() -> None:
    ids = [step.id for step in (*EVIDENCE_STEPS, *GUARDS)]
    assert len(ids) == len(set(ids)) == len(STEPS_BY_ID)


def test_the_strongest_supporting_step_is_the_one_reported() -> None:
    # Um mapeamento corroborado por uma correspondência de cabeçalho e pela
    # aritmética fiscal é informado como decidido pela aritmética: é a
    # alegação que continuaria valendo num cliente cujos cabeçalhos não
    # conseguimos ler.
    assert deciding_step(["lexical", "arithmetic"]) == "arithmetic"
    assert deciding_step(["semantic", "catalog"]) == "catalog"
    assert deciding_step([]) == ""


def test_the_resolver_never_outranks_objective_evidence() -> None:
    # "Decidido pelo resolvedor" precisa manter o seu significado honesto:
    # nada objetivo sustentou o mapeamento e o julgamento da IA decidiu.
    for objective in ("lexical", "profile", "identifier", "catalog", "arithmetic"):
        assert deciding_step(["resolver", objective]) == objective


def test_the_resolver_alone_does_not_make_a_mapping_transferable() -> None:
    # O resolvedor é um árbitro, não uma evidência. Contá-lo como
    # transferível deixaria um mapeamento apoiado só no cabeçalho passar por
    # generalizável.
    assert "resolver" not in TRANSFERRING_STEP_IDS
    assert rests_only_on_headers(["resolver"]) is True
    assert rests_only_on_headers(["resolver", "lexical", "semantic"]) is True
    assert rests_only_on_headers(["resolver", "arithmetic"]) is False


def test_header_bound_steps_are_exactly_the_ones_reading_client_words() -> None:
    header_bound = {s.id for s in EVIDENCE_STEPS if not s.transfers}
    assert header_bound == {"lexical", "semantic", "resolver"}


def test_supporting_steps_render_strongest_last() -> None:
    assert format_steps(["arithmetic", "lexical"]) == "lexical + arithmetic"
    # e a forma montada faz a ida e volta, porque é esse texto que o relatório guarda
    assert deciding_step(format_steps(["arithmetic", "lexical"])) == "arithmetic"


@pytest.mark.parametrize("step", (*EVIDENCE_STEPS, *GUARDS), ids=lambda s: s.id)
def test_every_step_documents_itself_and_points_at_its_code(step) -> None:
    # O registro é o que o revisor lê para entender o pipeline, então uma
    # entrada sem resumo ou sem a indicação de onde está implementada não
    # serve para nada.
    assert step.name and step.name[0].isupper()
    assert len(step.summary) > 60
    assert step.implemented_by


#: Células de etapas em memória (vocabulário interno, como o mapeador e o
#: preenchimento retroativo as montam), a mesma célula no esquema em
#: português, e o que ``deciding_step`` e ``format_steps`` devem devolver.
#: As últimas quatro formas são as reais dos relatórios arquivados:
#: ``unrecorded`` e ``catalog (agrees)`` só existem no preenchimento
#: retroativo, e mapping_steps não as classifica, então não decidem nada.
STEP_CELLS: tuple[tuple[str, str, str, str], ...] = (
    ("arithmetic", "aritmetica", "arithmetic", "arithmetic"),
    ("resolver", "resolvedor_ia", "resolver", "resolver"),
    ("lexical + catalog", "lexico + catalogo", "catalog", "lexical + catalog"),
    (
        "resolver + lexical + semantic",
        "resolvedor_ia + lexico + semantico",
        "semantic",
        "resolver + lexical + semantic",
    ),
    ("profile + identifier", "perfil + identificador", "identifier", "profile + identifier"),
    # Fora de ordem: format_steps reordena, com a mais forte por último.
    ("arithmetic + lexical", "aritmetica + lexico", "arithmetic", "lexical + arithmetic"),
    (
        "lexical + semantic + profile + identifier + catalog + arithmetic",
        "lexico + semantico + perfil + identificador + catalogo + aritmetica",
        "arithmetic",
        "lexical + semantic + profile + identifier + catalog + arithmetic",
    ),
    ("semantic + catalog (agrees)", "semantico + catalogo (concorda)", "semantic", "semantic"),
    ("semantic + unrecorded", "semantico + nao_registrado", "semantic", "semantic"),
    (
        "resolver + semantic + unrecorded",
        "resolvedor_ia + semantico + nao_registrado",
        "semantic",
        "resolver + semantic",
    ),
    ("unrecorded", "nao_registrado", "", ""),
)


@pytest.mark.parametrize(
    ("internal", "portuguese", "decided", "formatted"),
    STEP_CELLS,
    ids=[case[0] for case in STEP_CELLS],
)
def test_portuguese_step_cells_read_back_to_the_same_decision(
    internal: str, portuguese: str, decided: str, formatted: str
) -> None:
    # O relatório em português grava exatamente esta célula ...
    assert report_schema.steps_to_external(internal) == portuguese
    # ... e, lida de volta por report_schema, ela volta ao vocabulário interno.
    read_back = report_schema.steps_to_internal(portuguese)
    assert read_back == internal
    for field in ("supported_by", "decided_by"):
        assert (
            report_schema.internal_value("mappings", field, portuguese, report_schema.PT_BR)
            == internal
        )

    # A célula lida em português dá a mesma decisão que a célula em inglês.
    assert deciding_step(read_back) == deciding_step(internal) == decided
    assert format_steps(read_back) == format_steps(internal) == formatted
    assert rests_only_on_headers(read_back) == rests_only_on_headers(internal)

    # Sem a conversão, mapping_steps não reconhece os ids em português: é
    # report_schema que faz a ponte, e é ela que este teste exercita.
    assert deciding_step(portuguese) == ""
    assert format_steps(portuguese) == ""
