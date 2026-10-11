"""IDs de evidência, prioridade de desempate e alcance das etapas."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Step:
    id: str
    transfers: bool


# A ordem desempata evidências de mesmo score; não escolhe o método vencedor.
EVIDENCE_STEPS: tuple[Step, ...] = (
    Step('resolver', False),
    Step('lexical', False),
    Step('semantic', False),
    Step('profile', True),
    Step('identifier', True),
    Step('catalog', True),
    Step('arithmetic', True),
)

GUARDS: tuple[Step, ...] = (
    Step('vacuity', True),
    Step('family_conflict', True),
    Step('underspecified', True),
    Step('exclusivity', True),
)

STEPS_BY_ID: dict[str, Step] = {
    step.id: step for step in (*EVIDENCE_STEPS, *GUARDS)
}

#: Prioridade das evidências, conforme a ordem definida em EVIDENCE_STEPS.
_EVIDENCE_RANK: dict[str, int] = {
    step.id: index for index, step in enumerate(EVIDENCE_STEPS)
}

#: Etapas que também se aplicam a ERPs com vocabulário desconhecido.
TRANSFERRING_STEP_IDS: frozenset[str] = frozenset(
    step.id for step in EVIDENCE_STEPS if step.transfers
)


def deciding_step(supporting: object) -> str:
    """Retorna a etapa de maior prioridade em EVIDENCE_STEPS, ou ``""`` se não houver.

    Se cabeçalho e aritmética apoiam a escolha, por exemplo, a aritmética
    prevalece porque sua evidência independe do vocabulário do cliente.
    """
    ids = _evidence_ids(supporting)
    return max(ids, key=lambda step: _EVIDENCE_RANK[step]) if ids else ""


def format_steps(supporting: object) -> str:
    """Monta a célula de planilha com as etapas de apoio, a mais forte por último."""
    ids = _evidence_ids(supporting)
    return " + ".join(sorted(ids, key=lambda step: _EVIDENCE_RANK[step]))


def rests_only_on_headers(supporting: object) -> bool:
    """Verifica se o mapeamento depende apenas do cabeçalho do cliente.

    ``True`` indica que não há apoio das etapas marcadas como ``transfers``;
    a escolha pode depender de palavras específicas do ERP.
    """
    ids = set(_evidence_ids(supporting))
    return bool(ids) and not (ids & TRANSFERRING_STEP_IDS)


def _evidence_ids(supporting: object) -> list[str]:
    """Extrai os IDs de evidência de uma célula ou sequência.

    Aceita textos como ``"lexical + catalog"``. Ignora guards, IDs em português
    e os marcadores ``unrecorded`` e ``catalog (agrees)`` dos relatórios arquivados.
    """
    if supporting is None:
        return []
    if isinstance(supporting, str):
        parts = supporting.replace("+", ",").split(",")
    else:
        parts = [str(part) for part in supporting]
    return [part.strip() for part in parts if part.strip() in _EVIDENCE_RANK]
