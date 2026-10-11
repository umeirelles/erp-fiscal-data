"""Verifica os IDs, a prioridade e a leitura das etapas internas de evidência."""

from __future__ import annotations

import pytest

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
