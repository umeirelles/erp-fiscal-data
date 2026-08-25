"""The named steps of the column-mapping analysis.

This module is the single place where the pipeline's reasoning is given
official names. Every mapping the tool produces reports which of these steps
carried the decision and which merely corroborated it, so a reviewer can read
*how* a column was matched and not only *that* it was.

Two kinds of step, and the difference matters:

* An **evidence step** is a reason to believe a client column belongs in a
  model column. Evidence accumulates; several steps can support one mapping.
* A **guard** is a reason to refuse one. A guard that fires overrides any
  amount of evidence, because on tax output a wrong column is worse than an
  empty one.

Each evidence step declares whether it ``transfers``. A step that transfers
rests on something true of Brazilian fiscal documents in general - tax law,
official code tables, check-digit arithmetic - so it works on an ERP nobody
has seen. A step that does not transfer rests on the words this particular
client chose for its headers, and is worth little on the next client. The
share of mappings resting on transferring steps is the generalization metric
for the whole tool; the four sample clients are reference material, so a gain
that shows up only in non-transferring steps is close to worthless.

Adding a step means adding an entry here and populating it at the point of
decision. Renaming one means editing one line.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Step:
    """One named way the tool can justify - or refuse - a column mapping."""

    id: str
    name: str
    summary: str
    #: Whether the step's reasoning survives an ERP whose vocabulary is unknown.
    transfers: bool
    #: Where the logic lives, so a reader can go and check it.
    implemented_by: str


#: Evidence steps, ordered from weakest to strongest claim on correctness.
#: The order is meaningful: where several steps support one mapping, the last
#: one in this tuple is the one reported as having decided it.
EVIDENCE_STEPS: tuple[Step, ...] = (
    Step(
        id="resolver",
        name="Model adjudication",
        summary=(
            "Weighs every other step's evidence for the shortlisted candidates "
            "and chooses, returning its own confidence and a one-sentence "
            "reason. Reached only for columns the earlier steps left uncertain."
        ),
        # Ranked weakest on purpose, and marked as not transferring. The
        # resolver is an adjudicator, not evidence: what generalises is the
        # evidence underneath it. So "decided by resolver" carries its honest
        # meaning - nothing objective supported this and the model's judgment
        # carried it - and a header-only mapping cannot pass as transferable
        # merely because the resolver was the one to choose it.
        transfers=False,
        implemented_by="_openai_data_decision",
    ),
    Step(
        id="lexical",
        name="Lexical header match",
        summary=(
            "Compares header text after normalising accents, case, punctuation "
            "and known fiscal abbreviations, then scores token overlap and edit "
            "distance. Handles ERP truncation - 'Vlr Isen ICM' reads as the "
            "exempt ICMS slot."
        ),
        transfers=False,
        implemented_by="LexicalHeaderScorer, normalize_header, semantic_header_signature",
    ),
    Step(
        id="semantic",
        name="Semantic header match",
        summary=(
            "Compares the meaning of header names using embeddings, so "
            "'Nota Fiscal number' can reach 'Nº da Nota Fiscal' without sharing "
            "a token. Requires online mode."
        ),
        transfers=False,
        implemented_by="OpenAIEmbeddingHeaderScorer",
    ),
    Step(
        id="profile",
        name="Value profile match",
        summary=(
            "Compares the shape of the data itself - inferred type, digit "
            "widths, string lengths, numeric distribution - and any exact "
            "overlap of distinctive sample values."
        ),
        transfers=True,
        implemented_by="profile_similarity, distinctive_sample_overlap",
    ),
    Step(
        id="identifier",
        name="Identifier validation",
        summary=(
            "Recognises Brazilian identifiers by their check digits rather than "
            "their headers: CPF, CNPJ and the 44-digit NF-e access key. A "
            "column whose values validate is that identifier whatever it is called."
        ),
        transfers=True,
        implemented_by="identifier_match_score, _cpf_is_valid, _cnpj_is_valid, _nfe_key_is_valid",
    ),
    Step(
        id="catalog",
        name="Fiscal catalog lookup",
        summary=(
            "Tests values for membership in the official code tables - CFOP, "
            "NCM, IBGE municipality, the CST families, CSOSN and the IBS/CBS "
            "codes - discounting short, collision-prone domains such as "
            "two-digit CST."
        ),
        transfers=True,
        implemented_by="reference_catalog.best_catalog_evidence, catalog_evidence_is_distinctive",
    ),
    Step(
        id="arithmetic",
        name="Fiscal arithmetic",
        summary=(
            "Proves a column's role from an identity that is tax law rather "
            "than vocabulary: valor = base x aliquota across a tax trio, and "
            "line value = quantity x unit price. The strongest evidence the "
            "tool has, and the most transferable."
        ),
        transfers=True,
        implemented_by="tax_calculation_evidence, discover_client_tax_trios, line_calculation_role",
    ),
)


#: Guards. A guard that fires refuses the mapping regardless of evidence.
GUARDS: tuple[Step, ...] = (
    Step(
        id="vacuity",
        name="Empty-column veto",
        summary=(
            "Refuses a mapping onto a client column with no populated rows. "
            "Delivered, it would be a column that looks mapped and is silently "
            "blank - fabrication by construction."
        ),
        transfers=True,
        implemented_by="client_column_is_vacuous",
    ),
    Step(
        id="family_conflict",
        name="Tax-family conflict veto",
        summary=(
            "Refuses a mapping across incompatible tax families or slots - "
            "ICMS is not ICSP, a base is not a rate - however similar the "
            "headers look."
        ),
        transfers=True,
        implemented_by="semantic_header_conflict, header_tax_families, header_semantic_role",
    ),
    Step(
        id="underspecified",
        name="Underspecified-header veto",
        summary=(
            "Refuses a match where the client header is a bare generic term "
            "that names no specific model concept."
        ),
        transfers=True,
        implemented_by="underspecified_header_match",
    ),
    Step(
        id="exclusivity",
        name="Source already consumed",
        summary=(
            "A client column accepted onto one model column is withdrawn from "
            "the pool, so two model columns cannot claim the same source."
        ),
        transfers=True,
        implemented_by="build_mapping (the available set)",
    ),
)

STEPS_BY_ID: dict[str, Step] = {
    step.id: step for step in (*EVIDENCE_STEPS, *GUARDS)
}

#: Reporting order, strongest claim last - see EVIDENCE_STEPS.
_EVIDENCE_RANK: dict[str, int] = {
    step.id: index for index, step in enumerate(EVIDENCE_STEPS)
}

#: Steps whose reasoning survives an unseen ERP.
TRANSFERRING_STEP_IDS: frozenset[str] = frozenset(
    step.id for step in EVIDENCE_STEPS if step.transfers
)


def deciding_step(supporting: object) -> str:
    """The step reported as having carried the decision.

    The strongest supporting step wins, using the EVIDENCE_STEPS order. A
    mapping corroborated by both a header match and fiscal arithmetic is
    reported as decided by arithmetic, because that is the claim that would
    still hold on a client whose headers we cannot read.
    """
    ranked = [step for step in _as_ids(supporting) if step in _EVIDENCE_RANK]
    if not ranked:
        return ""
    return max(ranked, key=lambda step: _EVIDENCE_RANK[step])


def format_steps(supporting: object) -> str:
    """Render supporting steps for a spreadsheet cell, strongest last."""
    ranked = sorted(
        (step for step in _as_ids(supporting) if step in _EVIDENCE_RANK),
        key=lambda step: _EVIDENCE_RANK[step],
    )
    return " + ".join(ranked)


def rests_only_on_headers(supporting: object) -> bool:
    """Whether nothing but the client's header text supports this mapping.

    The question that predicts client #5. True means the mapping would not
    survive an ERP using different words for the same concept.
    """
    ids = {step for step in _as_ids(supporting) if step in _EVIDENCE_RANK}
    return bool(ids) and not (ids & TRANSFERRING_STEP_IDS)


def _as_ids(supporting: object) -> list[str]:
    if supporting is None:
        return []
    if isinstance(supporting, str):
        return [part.strip() for part in supporting.replace("+", ",").split(",") if part.strip()]
    return [str(part).strip() for part in supporting if str(part).strip()]
