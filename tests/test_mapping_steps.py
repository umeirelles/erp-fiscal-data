"""The named steps are a contract: reports, tests and the README quote them."""

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


def test_the_strongest_supporting_step_is_the_one_reported() -> None:
    # A mapping corroborated by a header match and by fiscal arithmetic is
    # reported as decided by arithmetic: that is the claim that would still
    # hold on a client whose headers we cannot read.
    assert deciding_step(["lexical", "arithmetic"]) == "arithmetic"
    assert deciding_step(["semantic", "catalog"]) == "catalog"
    assert deciding_step([]) == ""


def test_the_resolver_never_outranks_objective_evidence() -> None:
    # 'decided by resolver' has to keep its honest meaning: nothing objective
    # supported this and the model's judgment carried it.
    for objective in ("lexical", "profile", "identifier", "catalog", "arithmetic"):
        assert deciding_step(["resolver", objective]) == objective


def test_the_resolver_alone_does_not_make_a_mapping_transferable() -> None:
    # The resolver is an adjudicator, not evidence. Counting it as
    # transferable would let a header-only mapping pass as generalizable.
    assert "resolver" not in TRANSFERRING_STEP_IDS
    assert rests_only_on_headers(["resolver"]) is True
    assert rests_only_on_headers(["resolver", "lexical", "semantic"]) is True
    assert rests_only_on_headers(["resolver", "arithmetic"]) is False


def test_header_bound_steps_are_exactly_the_ones_reading_client_words() -> None:
    header_bound = {s.id for s in EVIDENCE_STEPS if not s.transfers}
    assert header_bound == {"lexical", "semantic", "resolver"}


def test_supporting_steps_render_strongest_last() -> None:
    assert format_steps(["arithmetic", "lexical"]) == "lexical + arithmetic"
    # and the rendered form round-trips, since the report stores this string
    assert deciding_step(format_steps(["arithmetic", "lexical"])) == "arithmetic"


@pytest.mark.parametrize("step", (*EVIDENCE_STEPS, *GUARDS), ids=lambda s: s.id)
def test_every_step_documents_itself_and_points_at_its_code(step) -> None:
    # The registry is what a reviewer reads to understand the pipeline, so an
    # entry without a summary or a pointer to its implementation is useless.
    assert step.name and step.name[0].isupper()
    assert len(step.summary) > 60
    assert step.implemented_by
