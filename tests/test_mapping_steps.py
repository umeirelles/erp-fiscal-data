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
    assert deciding_step(["lexical", "arithmetic"]) == "arithmetic"
    assert deciding_step(["semantic", "catalog"]) == "catalog"
    assert deciding_step([]) == ""


def test_the_resolver_never_outranks_objective_evidence() -> None:
    for objective in ("lexical", "profile", "identifier", "catalog", "arithmetic"):
        assert deciding_step(["resolver", objective]) == objective


def test_the_resolver_alone_does_not_make_a_mapping_transferable() -> None:
    # O resolvedor é um árbitro, não uma evidência.
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


@pytest.mark.parametrize("step", (*EVIDENCE_STEPS, *GUARDS), ids=lambda s: s.id)
def test_every_step_documents_itself_and_points_at_its_code(step) -> None:
    assert step.name and step.name[0].isupper()
    assert len(step.summary) > 60
    assert step.implemented_by


#: (célula interna, a mesma célula em português, deciding_step, format_steps).
#: As quatro últimas têm as formas dos relatórios arquivados: ``unrecorded`` e
#: ``catalog (agrees)`` não são etapas de evidência e não decidem nada.
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
    assert report_schema.steps_to_external(internal) == portuguese
    read_back = report_schema.steps_to_internal(portuguese)
    assert read_back == internal
    for field in ("supported_by", "decided_by"):
        assert report_schema.internal_value("mappings", field, portuguese) == internal

    assert deciding_step(read_back) == deciding_step(internal) == decided
    assert format_steps(read_back) == format_steps(internal) == formatted
    assert rests_only_on_headers(read_back) == rests_only_on_headers(internal)

    # Sem passar por report_schema, os ids em português não são reconhecidos.
    assert deciding_step(portuguese) == ""
    assert format_steps(portuguese) == ""
