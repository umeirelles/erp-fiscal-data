"""Etapa 5: decidir o mapeamento (``map_columns``).

Recebe as tabelas do modelo e do cliente, o avaliador de cabeçalhos, o resolver e os
catálogos; devolve um ``MappingResult`` por coluna do modelo, com situação, confiança,
justificativa e as etapas que apoiaram ou decidiram a escolha.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

import numpy as np
import pandas as pd

from mapper import schema
from mapper.catalogs import ReferenceCatalogs, best_catalog_evidence, expected_catalogs
from mapper.evidence import (
    DISTINCTIVE_IDENTIFIER_SCORE,
    GENERIC_PROFILE_CAP,
    MIN_SHARED_SAMPLES,
    _WEAK_EVIDENCE,
    _catalog_contradicts_accept,
    _catalog_label,
    _catalog_score,
    _overlap_evidence_score,
    _relationship_is_strong_contradiction,
    _relationship_score,
    _trio_proof_text,
    catalog_evidence_is_distinctive,
    client_column_is_vacuous,
    discover_client_tax_trios,
    distinctive_sample_overlap,
    distinctive_sample_overlap_count,
    identifier_match_score,
    line_calculation_evidence,
    numeric_series,
    profile_similarity,
    summarize_values,
    tax_calculation_evidence,
    trio_corroboration,
)
from mapper.headers import (
    _LINE_ROLES,
    _TRIO_ROLES,
    HeaderScorer,
    LexicalHeaderScorer,
    _same_explicit_role,
    _target_role,
    _target_tax_scope,
    describe_header_conflict_pt,
    line_calculation_role,
    semantic_header_conflict,
    underspecified_header_match,
)
from mapper.resolver import DataMappingDecision, DataResolver, OpenAIDataResolver
from mapper.schema import target_field
from mapper.spreadsheet import ColumnRef, SheetTable

# Os valores internos ficam em inglês (``accepted``, ``semantic``); ``schema`` os
# converte para o português dos arquivos. As regras estão em docs/ARCHITECTURE.md.


# ---------------------------------------------------------------------------
# Thresholds e pesos
#
# MapperConfig reúne os parâmetros expostos pela CLI; as demais constantes ficam
# logo abaixo (as de cabeçalho e de evidência, em headers.py e evidence.py).
# Alterações nesses valores podem mudar os mapeamentos: compare os resultados
# com ``python run.py evaluate`` (docs/EVALUATION.md).
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class MapperConfig:
    """Configura os thresholds e os tamanhos de amostra disponíveis na CLI."""

    name_threshold: float = 0.90  # --name-threshold: aceite pelo cabeçalho
    name_margin_threshold: float = 0.05  # --name-margin: vantagem sobre a segunda opção
    final_threshold: float = 0.90  # --final-threshold: o threshold de aceite
    candidate_count: int = 10  # --candidates: tamanho de cada lista de candidatos
    sample_size: int = 8  # --sample-size: valores de amostra por perfil

    @property
    def near_name_threshold(self) -> float:
        """Retorna o threshold de cabeçalho menos uma margem: 0,85 na configuração padrão."""
        return self.name_threshold - self.name_margin_threshold

    @property
    def weak_name_threshold(self) -> float:
        """Retorna o threshold de cabeçalho menos duas margens: 0,80 na configuração padrão."""
        return self.name_threshold - 2 * self.name_margin_threshold

    @property
    def weak_final_threshold(self) -> float:
        """Retorna o threshold final menos duas margens: 0,80 na configuração padrão."""
        return self.final_threshold - 2 * self.name_margin_threshold

    @property
    def clamp_ceiling(self) -> float:
        """Limita a confiança a um valor abaixo do threshold quando falta evidência decisiva."""
        return self.final_threshold - CEILING_GAP


# Primeira passagem: auditoria dos trios aceitos pelo cabeçalho
DEMOTION_MIN_RELATIONSHIP = 0.90
DEMOTION_MIN_IMPROVEMENT = 0.50

# Escolha pelo catálogo e apoio mínimo para manter uma proposta em revisão
CATALOG_DECISION_MIN_SCORE = 0.90
CATALOG_DECISION_MIN_LEAD = 0.10
REVIEW_MIN_CATALOG_COVERAGE = 0.90

# Segunda passagem: decisões locais e substituições
LOCAL_DECISION_NAME_WEIGHT = 0.35  # decisão por catálogo ou sobreposição:
LOCAL_DECISION_DATA_WEIGHT = 0.65  # max(0,80; 0,35 x nome + 0,65 x dados)
LOCAL_DECISION_FLOOR = 0.80
UNIQUE_RELATIONSHIP_MIN_LEAD = 0.10
OVERLAP_MAX_NAME_DEFICIT = 0.10
STRONG_OVERLAP = 0.50
STRONG_OVERLAP_FINAL_FLOOR = 0.90

# Confiança final local
NAME_WEIGHT_WITH_OBJECTIVE = 0.40  # com evidência objetiva: 0,4 x nome + 0,6 x evidência
OBJECTIVE_WEIGHT = 0.60
OBJECTIVE_WEAK_NAME_FLOOR = 0.80
CEILING_NAME_WEIGHT = 0.70  # sem evidência decisiva: 0,7 x nome + 0,3 x dados, com teto
CEILING_DATA_WEIGHT = 0.30
CEILING_GAP = 0.0001  # o teto é o threshold de aceite menos isto

FLOAT_TOLERANCE = 1e-9


# ---------------------------------------------------------------------------
# Mensagens da coluna justificativa
#
# A justificativa escrita pela IA (DataMappingDecision.reason) passa intacta,
# em inglês, porque o prompt continua em inglês. Números entram com vírgula
# decimal (schema.format_decimal_br / format_percent_br) e rótulos fiscais
# por schema.TEXT_LABELS.
# ---------------------------------------------------------------------------

REASON_NO_UNUSED = "Não restam colunas do cliente disponíveis."
REASON_SOURCE_COMPETITION = (
    "Mais de um campo do modelo propõe esta origem. A atribuição precisa de revisão "
    "para preservar o uso de uma origem distinta por destino."
)
REASON_PASS_ONE_ACCEPTED = (
    "O cabeçalho corresponde ao campo do modelo e tem vantagem suficiente sobre a "
    "segunda opção. Mapeamento aceito."
)
REASON_OFFLINE = (
    "O score do cabeçalho ou a margem sobre a segunda opção não atingiu o mínimo "
    "exigido. Como a comparação de dados está desativada, a proposta segue para revisão."
)
#: ``exc_type`` é o nome da classe da exceção; ``exc``, a mensagem dela.
REASON_DATA_ERROR = "A comparação de dados falhou: {exc_type}: {exc}"
#: ``catalog`` é o nome legível (``_catalog_label``); as coberturas chegam
#: formatadas por ``schema.format_percent_br`` (``88,2%``).
REASON_CATALOG_DECISION = (
    "Na versão consultada da tabela oficial de {catalog}, {distinct_coverage} dos "
    "{unique_formatted_count} códigos distintos desta coluna são válidos "
    "({coverage} das linhas). Entre as colunas disponíveis, apenas esta reúne "
    "evidência suficiente para a escolha pelo catálogo."
)
REASON_UNIQUE_RELATIONSHIP = (
    "Esta coluna se destaca das alternativas por confirmar uma relação fiscal em "
    "grande parte das linhas, como base de cálculo x alíquota = valor do tributo."
)
REASON_OVERLAP = (
    "As amostras do modelo coincidem com valores desta coluna que são diferentes "
    "de zero e pouco sujeitos a coincidências. Esse apoio não aparece nas "
    "colunas concorrentes."
)
REASON_JOINT = (
    "A comparação do cabeçalho e a relação fiscal verificada nas linhas apontam "
    "para esta coluna, com vantagem suficiente sobre as alternativas."
)
REASON_NULL_FALLBACK = (
    "A IA encontrou evidências favoráveis, mas não escolheu uma coluna. A candidata "
    "com o maior score de cabeçalho foi mantida para revisão."
)
REASON_PRESERVE_BEST = (
    "Os dados não permitem confirmar a candidata com o melhor cabeçalho. Ela foi "
    "mantida para revisão, pois a alternativa tem apenas um formato de valores "
    "semelhante."
)
REASON_UNSUPPORTED_NO_MATCH = (
    "A comparação dos cabeçalhos e dos dados não encontrou uma candidata com "
    "evidência suficiente. Este campo pode não constar na exportação do cliente."
)
#: ``header_conflict`` é o conflito em português (``describe_header_conflict_pt``).
REASON_CONFLICT_REVIEW = (
    "Os cabeçalhos apresentam {header_conflict}. Os valores, porém, oferecem "
    "evidência de correspondência e sugerem que o cabeçalho do cliente pode estar "
    "incorreto. A proposta precisa de revisão."
)
REASON_CONTRADICTION = (
    "A proposta não corresponde à relação fiscal verificada em grande parte das "
    "linhas. Por isso, precisa de revisão antes de ser aceita."
)
#: ``client_column`` é o nome de exibição já em português
#: (``schema.display_name_to_external``); sai entre aspas, por ``!r``.
REASON_VACUITY = (
    "{client_column!r} não tem linhas preenchidas. O mapeamento não foi aceito, "
    "pois essa origem deixaria a coluna do modelo sem dados."
)


# ---------------------------------------------------------------------------
# Etapas de evidência e travas: os IDs de apoiado_por, decidido_por e vetado_por
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# O relatório: uma linha por coluna do modelo
# ---------------------------------------------------------------------------


def _supporting_step_ids(
    *,
    header_step: str,
    name_confidence: float,
    relationship_score: float = 0.0,
    catalog_score: float = 0.0,
    overlap_score: float = 0.0,
    identifier_score: float = 0.0,
    arithmetic_decisive: bool = False,
    resolver_ran: bool = False,
) -> list[str]:
    """Lista as etapas que apoiam a escolha, da evidência mais fraca à mais forte.

    Usa os mesmos scores da decisão para que o relatório registre apenas as
    evidências consideradas durante o mapeamento.
    """
    steps: list[str] = []
    if resolver_ran:
        steps.append("resolver")
    if name_confidence > 0.0:
        steps.append(header_step)
    if overlap_score > 0.0:
        steps.append("profile")
    if identifier_score > 0.0:
        steps.append("identifier")
    if catalog_score > 0.0:
        steps.append("catalog")
    # Uma prova que não distingue a coluna confirma o escopo do tributo, não
    # este mapeamento.
    if relationship_score > 0.0 or arithmetic_decisive:
        steps.append("arithmetic")
    return steps


@dataclass
class MappingResult:
    """Representa a decisão de uma coluna do modelo na aba Mapeamentos.

    ``status`` usa os valores internos ``accepted``, ``review`` e ``no_match``;
    ``schema`` os converte ao gravar o arquivo. A ordem dos campos define
    a ordem das colunas do relatório.
    """

    model_column_number: int
    model_column: str
    client_column_number: int | None
    client_column: str | None
    name_confidence: float
    name_runner_up_confidence: float
    name_margin: float
    data_confidence: float | None
    final_confidence: float
    method: str
    status: str
    reason: str
    catalog_name: str | None = None
    #: Ponderada pelas linhas; as regras usam a cobertura sobre os códigos
    #: distintos (catalog_distinct_coverage), da qual catalog_confidence sai.
    catalog_coverage: float | None = None
    catalog_distinct_coverage: float | None = None
    catalog_confidence: float | None = None
    #: Registra o trio aritmético do cliente que sustenta a associação desta coluna.
    arithmetic_proof: str | None = None
    #: Evidências e guard registrados para a decisão (ver EVIDENCE_STEPS e GUARDS).
    supported_by: str = ""
    decided_by: str = ""
    vetoed_by: str = ""
    llm_data_confidence: float | None = None
    llm_final_confidence: float | None = None


_NO_NAME_SCORES = (0.0, 0.0, 0.0)


def _mapping_row(
    model_column: ColumnRef,
    client_column: ColumnRef | None,
    name_scores: tuple[float, float, float],
    **fields,
) -> MappingResult:
    """Monta o resultado de uma coluna e arredonda os scores de cabeçalho e a margem para quatro casas."""
    name_confidence, runner_up_confidence, name_margin = name_scores
    return MappingResult(
        model_column_number=model_column.index + 1,
        model_column=model_column.name,
        client_column_number=None if client_column is None else client_column.index + 1,
        client_column=None if client_column is None else client_column.display_name,
        name_confidence=round(name_confidence, 4),
        name_runner_up_confidence=round(runner_up_confidence, 4),
        name_margin=round(name_margin, 4),
        **fields,
    )


# ---------------------------------------------------------------------------
# map_columns: o algoritmo
# ---------------------------------------------------------------------------


@dataclass
class _MappingRun:
    """Reúne os dados e o estado compartilhados pelas etapas de ``map_columns``."""

    model_table: SheetTable
    client_table: SheetTable
    config: MapperConfig
    reference_catalogs: ReferenceCatalogs | None
    #: Pontuação de nome, modelo x cliente.
    similarities: np.ndarray
    #: Etapa de cabeçalho: "lexical" com o avaliador léxico, "semantic" com qualquer outro.
    header_step: str
    client_tax_trios: dict[tuple[str, tuple[str, ...]], list[dict[str, object]]]
    numeric_client_series: dict[int, pd.Series]
    #: Perfis das colunas do cliente; vazio offline.
    client_profiles: dict[int, dict[str, object]]
    #: Colunas do cliente ainda não consumidas por um aceite.
    available: set[int]
    #: Decisões já tomadas, por índice da coluna do modelo.
    results: dict[int, MappingResult]
    catalog_cache: dict[tuple[str, int], dict[str, object]]
    target_profiles: dict[int, dict[str, object]]


def map_columns(
    model_table: SheetTable,
    client_table: SheetTable,
    header_scorer: HeaderScorer,
    data_resolver: DataResolver | None,
    config: MapperConfig = MapperConfig(),
    reference_catalogs: ReferenceCatalogs | None = None,
) -> list[MappingResult]:
    """Associa cada coluna do modelo a uma coluna do cliente em duas passagens.

    Retorna um ``MappingResult`` por coluna, na ordem do modelo. Sem
    ``data_resolver``, a segunda passagem encaminha a melhor candidata pelo
    cabeçalho para revisão, quando houver uma coluna disponível.
    """
    run = _start_run(
        model_table, client_table, header_scorer, config, reference_catalogs,
        online=data_resolver is not None,
    )

    return _map_run(run, data_resolver)


def _map_run(run: _MappingRun, data_resolver: DataResolver | None) -> list[MappingResult]:
    """Executa as decisões usando os perfis e as evidências preparados para esta execução."""
    # 1. Aceita as correspondências claras de cabeçalho.
    second_pass_targets = _first_pass(run)
    # 2. Confere as escolhas com os catálogos e as relações aritméticas.
    _audit_accepted_catalog_matches(run, second_pass_targets)
    _demote_inconsistent_tax_header_matches(run, second_pass_targets)
    # Todas as propostas veem as mesmas origens e âncoras da primeira passagem.
    proposals = {index: _second_pass(run, index, data_resolver)
                 for index in sorted(second_pass_targets)}
    _resolve_competing_claims(proposals, run.config)
    run.results.update(proposals)
    if data_resolver is not None:
        _propagate_confirmed_relationships(run, proposals)
    return [run.results[index] for index in range(len(run.model_table.columns))]


def _propagate_confirmed_relationships(
    run: _MappingRun, proposals: dict[int, MappingResult],
) -> None:
    """Reavalia a aritmética após novos aceites, em rodadas sem chamadas à API.

    Cada rodada usa as mesmas referências para todos os alvos. Só continua
    quando confirma ao menos um destino, portanto termina em no máximo N
    rodadas. Aceites anteriores não são substituídos por decisões posteriores.
    """
    while any(result.status == "accepted" for result in proposals.values()):
        run.available.difference_update(
            result.client_column_number - 1 for result in proposals.values()
            if result.status == "accepted" and result.client_column_number is not None
        )
        pending = [index for index, result in run.results.items() if result.status != "accepted"]
        proposals = {}
        for index in pending:
            name = run.model_table.columns[index].name
            if _target_role(name) not in _TRIO_ROLES and line_calculation_role(name) is None:
                continue
            pool = _candidate_pool(run, index, online=True)
            choice = _relationship_choice(pool, run.config)
            if choice is None:
                continue
            proposal = _judge_proposal(
                run, pool, choice, {"llm_data_confidence": None, "llm_final_confidence": None}
            )
            if proposal.status == "accepted":
                proposals[index] = proposal
        _resolve_competing_claims(proposals, run.config)
        run.results.update(proposals)


def _start_run(
    model_table: SheetTable,
    client_table: SheetTable,
    header_scorer: HeaderScorer,
    config: MapperConfig,
    reference_catalogs: ReferenceCatalogs | None,
    *,
    online: bool,
) -> _MappingRun:
    """Calcula os scores dos cabeçalhos e prepara os dados usados ao longo da execução."""
    if not client_table.columns:
        raise ValueError("A planilha do cliente não tem colunas")
    similarities = header_scorer.scores(
        [column.name for column in model_table.columns],
        [column.name for column in client_table.columns],
    )
    expected_shape = (len(model_table.columns), len(client_table.columns))
    if similarities.shape != expected_shape:
        raise ValueError(
            f"O avaliador de cabeçalhos retornou {similarities.shape}; esperado {expected_shape}"
        )
    return _MappingRun(
        model_table=model_table,
        client_table=client_table,
        config=config,
        reference_catalogs=reference_catalogs,
        similarities=similarities,
        header_step="lexical" if isinstance(header_scorer, LexicalHeaderScorer) else "semantic",
        client_tax_trios=discover_client_tax_trios(client_table),
        numeric_client_series={
            index: numeric_series(client_table.series(column))
            for index, column in enumerate(client_table.columns)
        },
        client_profiles=(
            {
                index: summarize_values(client_table.series(column), config.sample_size)
                for index, column in enumerate(client_table.columns)
            }
            if online
            else {}
        ),
        available=set(range(len(client_table.columns))),
        results={},
        catalog_cache={},
        target_profiles={},
    )


def _evidence_attribution(
    run: _MappingRun, model_column: ColumnRef, client_index: int, *,
    decided_by: str, **scores
) -> dict[str, str | None]:
    """Reúne a prova aritmética e as etapas que apoiaram e decidiram a proposta."""
    trio = trio_corroboration(run.client_tax_trios, model_column.name, client_index)
    proof = _trio_proof_text(trio)
    steps = _supporting_step_ids(
        header_step=run.header_step,
        arithmetic_decisive=trio is not None and int(trio.get("alternatives", 1)) <= 1,
        **scores,
    )
    return {
        "arithmetic_proof": proof,
        "supported_by": format_steps(steps),
        "decided_by": decided_by,
    }


# --- Primeira passagem e auditorias -----------------------------------------


def _first_pass(run: _MappingRun) -> list[int]:
    """Aceita as correspondências claras de cabeçalho e separa as demais para a segunda passagem.

    Calcula as propostas antes de reservar origens. A ordem do modelo não
    decide qual campo recebe uma coluna disputada.
    """
    config = run.config
    client_table = run.client_table
    second_pass_targets: list[int] = []
    for target_index, model_column in enumerate(run.model_table.columns):
        ranked = _rank_header_compatible(
            target_index, run.similarities, run.available, model_column, client_table.columns, 2
        )
        if not ranked:
            run.results[target_index] = _mapping_row(
                model_column, None, _NO_NAME_SCORES,
                data_confidence=None, final_confidence=0.0,
                method="name", status="no_match", reason=REASON_NO_UNUSED,
            )
            continue

        client_index, name_confidence = ranked[0]
        _, runner_up_confidence, name_margin = _name_score_context(ranked)
        client_column = client_table.columns[client_index]
        accepted_by_name = (
            name_confidence >= config.name_threshold
            and name_margin >= config.name_margin_threshold
            and semantic_header_conflict(model_column.name, client_column.name) is None
            and not underspecified_header_match(model_column.name, client_column.name)
            and not client_column_is_vacuous(client_table, client_column)
        )
        if not accepted_by_name:
            second_pass_targets.append(target_index)
            continue
        run.results[target_index] = _mapping_row(
            model_column, client_column, (name_confidence, runner_up_confidence, name_margin),
            data_confidence=None, final_confidence=round(name_confidence, 4),
            method="name", status="accepted", reason=REASON_PASS_ONE_ACCEPTED,
            **_evidence_attribution(
                run, model_column, client_column.index, decided_by=run.header_step,
                name_confidence=name_confidence
            ),
        )
    demoted = _resolve_competing_claims(run.results, config)
    for index in demoted:
        del run.results[index]
    second_pass_targets.extend(demoted)
    for result in run.results.values():
        if result.status == "accepted":
            run.available.remove(result.client_column_number - 1)
    return second_pass_targets


def _resolve_competing_claims(
    results: dict[int, MappingResult], config: MapperConfig,
) -> list[int]:
    """Compara propostas aceitas para a mesma origem; empates ficam para revisão."""
    claims: dict[int, list[tuple[int, MappingResult]]] = {}
    for index, result in results.items():
        if result.status == "accepted" and result.client_column_number is not None:
            claims.setdefault(result.client_column_number - 1, []).append((index, result))
    demoted = []
    for source, candidates in claims.items():
        if len(candidates) < 2:
            continue
        candidates.sort(key=lambda item: item[1].final_confidence, reverse=True)
        lead = candidates[0][1].final_confidence - candidates[1][1].final_confidence
        if lead + FLOAT_TOLERANCE < config.name_margin_threshold or lead == 0:
            withheld = candidates
        else:
            withheld = candidates[1:]
        for index, result in withheld:
            result.status = "review"
            result.decided_by = ""
            result.vetoed_by = "exclusivity"
            result.reason = REASON_SOURCE_COMPETITION
            demoted.append(index)
    return demoted


def _accepted_source_index(result: MappingResult) -> int | None:
    if result.status != "accepted" or result.client_column_number is None:
        return None
    return result.client_column_number - 1


def _reopen(run: _MappingRun, target_index: int, second_pass_targets: list[int]) -> None:
    """Reabre a decisão para a segunda passagem e libera a coluna do cliente para outras propostas."""
    source_index = _accepted_source_index(run.results[target_index])
    if source_index is None:
        return
    run.available.add(source_index)
    del run.results[target_index]
    if target_index not in second_pass_targets:
        second_pass_targets.append(target_index)


def _audit_accepted_catalog_matches(run: _MappingRun, second_pass_targets: list[int]) -> None:
    """Registra a evidência de catálogo e reabre as decisões que ela contradiz."""
    if run.reference_catalogs is None:
        return
    for target_index, result in list(run.results.items()):
        source_index = _accepted_source_index(result)
        if source_index is None:
            continue
        selected = best_catalog_evidence(
            _catalog_analyses(run, _expected_catalog_names(run, target_index), source_index)
        )
        if selected is None:
            continue
        result.catalog_name = str(selected["catalog"])
        result.catalog_coverage = round(float(selected["coverage"]), 4)
        result.catalog_distinct_coverage = round(
            float(selected.get("distinct_coverage", selected["coverage"])), 4
        )
        result.catalog_confidence = round(float(selected["score"]), 4)
        if _catalog_contradicts_accept(selected):
            _reopen(run, target_index, second_pass_targets)


def _demote_inconsistent_tax_header_matches(
    run: _MappingRun, second_pass_targets: list[int]
) -> None:
    """Reabre uma posição de cada trio aceito pelo cabeçalho quando os valores não confirmam a equação."""
    scopes = {
        scope
        for column in run.model_table.columns
        if (scope := _target_tax_scope(column.name)) is not None
    }
    for scope in sorted(scopes):
        target_index = _inconsistent_trio_position(run, scope)
        if target_index is not None:
            _reopen(run, target_index, second_pass_targets)


def _inconsistent_trio_position(
    run: _MappingRun, scope: tuple[str, tuple[str, ...]]
) -> int | None:
    """Escolhe qual posição reabrir em um trio cuja equação não foi confirmada.

    Procura uma substituição entre as colunas disponíveis e reabre a posição
    com a maior melhora, se ela atingir o mínimo exigido. Sem alternativa,
    uma contradição forte reabre a escolha com menor score de cabeçalho.
    Retorna ``None`` quando nenhum desses critérios é atendido.
    """
    model_table, results = run.model_table, run.results
    anchors = _accepted_tax_anchors(model_table, results, scope)
    if _TRIO_ROLES - anchors.keys():
        return None
    current = tax_calculation_evidence(
        run.numeric_client_series[anchors["base"]],
        run.numeric_client_series[anchors["rate"]],
        run.numeric_client_series[anchors["value"]],
    )
    if bool(current["available"]) and float(current["score"]) >= DEMOTION_MIN_RELATIONSHIP:
        return None

    role_targets = {
        _target_role(model_table.columns[index].name): index
        for index, result in results.items()
        if result.status == "accepted"
        and _target_tax_scope(model_table.columns[index].name) == scope
        and _target_role(model_table.columns[index].name) in _TRIO_ROLES
    }
    alternatives = _trio_swap_alternatives(run, anchors, role_targets)
    if alternatives:
        improvement, _, _, target_index, _ = max(alternatives)
        if improvement < DEMOTION_MIN_IMPROVEMENT:
            return None
        return target_index
    if _relationship_is_strong_contradiction([current]):
        # Reabre a decisão com menor apoio do cabeçalho. A segunda passagem
        # considera a contradição aritmética antes de aceitar a proposta.
        return min(
            role_targets.values(),
            key=lambda index: (
                results[index].name_confidence,
                results[index].name_margin,
                -index,
            ),
        )
    return None


def _trio_swap_alternatives(
    run: _MappingRun, anchors: dict[str, int], role_targets: dict[str, int]
) -> list[tuple[float, float, str, int, int]]:
    """Lista as trocas possíveis como (melhora, score de nome, papel, coluna do modelo, coluna do cliente)."""
    alternatives: list[tuple[float, float, str, int, int]] = []
    for role, target_index in role_targets.items():
        current_source = anchors[role]
        current_score = _relationship_score(
            _candidate_relationship_evidence(run, target_index, {current_source}).get(
                current_source, []
            )
        )
        candidate_rules = _candidate_relationship_evidence(
            run, target_index, run.available | {current_source}
        )
        for candidate_index, rules in candidate_rules.items():
            if candidate_index == current_source:
                continue
            candidate_values = run.client_table.series(run.client_table.columns[candidate_index])
            if summarize_values(candidate_values)["evidence_quality"] != "informative":
                continue
            relationship_score = _relationship_score(rules)
            if relationship_score < DEMOTION_MIN_RELATIONSHIP:
                continue
            alternatives.append(
                (
                    relationship_score - current_score,
                    float(run.similarities[target_index, candidate_index]),
                    role,
                    target_index,
                    candidate_index,
                )
            )
    return alternatives


# --- Evidência por candidato --------------------------------------------------


def _accepted_tax_anchors(
    model_table: SheetTable,
    results: dict[int, MappingResult],
    scope: tuple[str, tuple[str, ...]],
    *,
    exclude_target: int | None = None,
) -> dict[str, int]:
    """Associa cada papel do trio à coluna do cliente já aceita para o tributo e escopo informados."""
    anchors: dict[str, int] = {}
    for target_index, result in results.items():
        if target_index == exclude_target:
            continue
        model_column = model_table.columns[target_index]
        role = _target_role(model_column.name)
        if role not in _TRIO_ROLES or _target_tax_scope(model_column.name) != scope:
            continue
        source_index = _accepted_source_index(result)
        if source_index is not None:
            anchors.setdefault(role, source_index)
    return anchors


def _accepted_line_anchors(
    model_table: SheetTable,
    results: dict[int, MappingResult],
    *,
    exclude_target: int | None = None,
) -> dict[str, int]:
    """Associa cada papel de quantidade x preço à coluna do cliente já aceita."""
    anchors: dict[str, int] = {}
    for target_index, result in results.items():
        if target_index == exclude_target:
            continue
        role = line_calculation_role(model_table.columns[target_index].name)
        if role is None:
            continue
        source_index = _accepted_source_index(result)
        if source_index is not None:
            anchors.setdefault(role, source_index)
    return anchors


def _candidate_relationship_evidence(
    run: _MappingRun, target_index: int, candidate_indices: set[int]
) -> dict[int, list[dict[str, object]]]:
    """Testa cada candidata na posição restante de uma equação cujas outras colunas já foram aceitas."""
    evidence: dict[int, list[dict[str, object]]] = {
        index: [] for index in candidate_indices
    }
    model_name = run.model_table.columns[target_index].name
    values = run.numeric_client_series

    tax_role = _target_role(model_name)
    tax_scope = _target_tax_scope(model_name)
    if tax_role in _TRIO_ROLES and tax_scope is not None:
        anchors = _accepted_tax_anchors(
            run.model_table, run.results, tax_scope, exclude_target=target_index
        )
        if _TRIO_ROLES - {tax_role} <= anchors.keys():
            for candidate_index in candidate_indices:
                sources = {**anchors, tax_role: candidate_index}
                relationship = tax_calculation_evidence(
                    values[sources["base"]], values[sources["rate"]], values[sources["value"]]
                )
                relationship.update(
                    {
                        "rule": "tax_value_equals_base_times_rate",
                        "target_role": tax_role,
                        "tax_scope": tax_scope[0],
                        "tax_qualifiers": list(tax_scope[1]),
                    }
                )
                evidence[candidate_index].append(relationship)

    line_role = line_calculation_role(model_name)
    if line_role is not None:
        anchors = _accepted_line_anchors(
            run.model_table, run.results, exclude_target=target_index
        )
        if _LINE_ROLES - {line_role} <= anchors.keys():
            for candidate_index in candidate_indices:
                sources = {**anchors, line_role: candidate_index}
                relationship = line_calculation_evidence(
                    values[sources["quantity"]],
                    values[sources["unit_price"]],
                    values[sources["line_value"]],
                )
                relationship.update(
                    {
                        "rule": "line_value_equals_quantity_times_unit_price",
                        "target_role": line_role,
                    }
                )
                evidence[candidate_index].append(relationship)

    return {index: rules for index, rules in evidence.items() if rules}


def _expected_catalog_names(run: _MappingRun, target_index: int) -> list[str]:
    """Lista os catálogos carregados que se aplicam ao cabeçalho do modelo."""
    return [
        name
        for name in expected_catalogs(run.model_table.columns[target_index].name)
        if name in run.reference_catalogs.catalogs
    ]


def _catalog_analyses(
    run: _MappingRun, catalog_names: Sequence[str], client_index: int
) -> list[dict[str, object]]:
    """Compara uma coluna com cada catálogo e mantém o resultado em cache por (catálogo, coluna)."""
    analyses = []
    for name in catalog_names:
        key = name, client_index
        if key not in run.catalog_cache:
            column = run.client_table.columns[client_index]
            run.catalog_cache[key] = run.reference_catalogs.analyze(
                run.client_table.series(column), name
            )
        analyses.append(run.catalog_cache[key])
    return analyses


def _candidate_catalog_evidence(
    run: _MappingRun, target_index: int, candidate_indices: set[int]
) -> dict[int, list[dict[str, object]]]:
    if run.reference_catalogs is None:
        return {}
    catalog_names = _expected_catalog_names(run, target_index)
    evidence = {
        index: _catalog_analyses(run, catalog_names, index) for index in candidate_indices
    }
    return {index: matches for index, matches in evidence.items() if matches}


# --- Conjunto de candidatos -----------------------------------------------------


def _by_score_then_index(item: tuple[int, float]) -> tuple[float, int]:
    return -item[1], item[0]


def _rank_available(
    target_index: int,
    similarities: np.ndarray,
    available: set[int],
    limit: int,
) -> list[tuple[int, float]]:
    """Retorna até ``limit`` colunas disponíveis, ordenadas pelo score de cabeçalho: (índice, score)."""
    ranked = sorted(
        ((index, float(similarities[target_index, index])) for index in available),
        key=_by_score_then_index,
    )
    return ranked[:limit]


def _without_header_conflicts(
    ranked: Sequence[tuple[int, float]],
    model_column: ColumnRef,
    client_columns: Sequence[ColumnRef],
) -> list[tuple[int, float]]:
    return [
        item
        for item in ranked
        if semantic_header_conflict(model_column.name, client_columns[item[0]].name) is None
    ]


def _rank_header_compatible(
    target_index: int,
    similarities: np.ndarray,
    available: set[int],
    model_column: ColumnRef,
    client_columns: Sequence[ColumnRef],
    limit: int,
) -> list[tuple[int, float]]:
    """Ordena as candidatas pelo cabeçalho e exclui conflitos, salvo se todas conflitarem.

    Na primeira passagem, uma candidata incompatível não reduz a margem da
    escolha. Ela ainda pode participar da segunda passagem se os dados
    oferecerem evidência para revisão.
    """
    ranked = _rank_available(target_index, similarities, available, len(available))
    compatible = _without_header_conflicts(ranked, model_column, client_columns)
    return (compatible or ranked)[:limit]


def _top_positive(scored, limit: int) -> list[int]:
    """Retorna os índices dos maiores scores positivos, até o limite informado."""
    ranked = sorted(scored, key=_by_score_then_index)
    return [index for index, score in ranked[:limit] if score > 0.0]


def _rank_name_and_profile_candidates(
    target_index: int,
    similarities: np.ndarray,
    available: set[int],
    target_profile: dict[str, object],
    client_profiles: dict[int, dict[str, object]],
    limit: int,
    relationship_evidence: dict[int, list[dict[str, object]]] | None = None,
    catalog_evidence: dict[int, list[dict[str, object]]] | None = None,
) -> list[tuple[int, float]]:
    """Combina as melhores candidatas por cabeçalho, perfil, relação fiscal e catálogo.

    Retorna pares de índice e score de cabeçalho, em ordem decrescente de score.
    """
    relationship_evidence = relationship_evidence or {}
    catalog_evidence = catalog_evidence or {}
    pool = {index for index, _ in _rank_available(target_index, similarities, available, limit)}
    pool.update(
        _top_positive(
            ((index, profile_similarity(target_profile, client_profiles[index])) for index in available),
            limit,
        )
    )
    pool.update(
        _top_positive(
            (
                (index, _relationship_score(rules))
                for index, rules in relationship_evidence.items()
                if index in available
            ),
            limit,
        )
    )
    pool.update(
        _top_positive(
            (
                (index, _catalog_score(matches))
                for index, matches in catalog_evidence.items()
                if index in available
            ),
            limit,
        )
    )
    return sorted(
        ((index, float(similarities[target_index, index])) for index in pool),
        key=_by_score_then_index,
    )


def _name_score_context(
    ranked: Sequence[tuple[int, float]],
    chosen_index: int | None = None,
) -> tuple[float, float, float]:
    """Retorna o score da candidata escolhida, o da melhor alternativa e a margem entre eles."""
    if not ranked:
        return 0.0, 0.0, 0.0
    if chosen_index is None:
        chosen_index = ranked[0][0]
    chosen_score = next(score for index, score in ranked if index == chosen_index)
    runner_up = max(
        (score for index, score in ranked if index != chosen_index),
        default=0.0,
    )
    return chosen_score, runner_up, chosen_score - runner_up


@dataclass
class _CandidatePool:
    """Reúne as candidatas e suas evidências para a segunda passagem de uma coluna do modelo."""

    model_column: ColumnRef
    target_profile: dict[str, object]
    #: Pares (índice da coluna do cliente, score de cabeçalho), em ordem decrescente.
    ranked: list[tuple[int, float]]
    #: Os mesmos, sem os cabeçalhos em conflito com o do modelo.
    compatible: list[tuple[int, float]]
    columns: list[ColumnRef]
    name_scores: dict[int, float]
    relationships: dict[int, list[dict[str, object]]]
    catalogs: dict[int, list[dict[str, object]]]
    client_profiles: dict[int, dict[str, object]]
    #: Melhor candidata compatível, quando disponível, e seus scores de cabeçalho.
    best_column: ColumnRef | None
    best_name: tuple[float, float, float]

    def relationship_score(self, index: int) -> float:
        return _relationship_score(self.relationships.get(index, []))

    def is_compatible(self, column: ColumnRef) -> bool:
        return semantic_header_conflict(self.model_column.name, column.name) is None


def _candidate_pool(run: _MappingRun, target_index: int, *, online: bool) -> _CandidatePool:
    """Monta as candidatas pelo cabeçalho no modo offline e por quatro critérios no modo online."""
    model_column = run.model_table.columns[target_index]
    if target_index not in run.target_profiles:
        run.target_profiles[target_index] = summarize_values(
            run.model_table.series(model_column), run.config.sample_size
        )
    target_profile = run.target_profiles[target_index]
    relationships = (
        _candidate_relationship_evidence(run, target_index, run.available) if online else {}
    )
    catalogs = _candidate_catalog_evidence(run, target_index, run.available) if online else {}
    if online:
        ranked = _rank_name_and_profile_candidates(
            target_index,
            run.similarities,
            run.available,
            target_profile,
            run.client_profiles,
            run.config.candidate_count,
            relationships,
            catalogs,
        )
    else:
        ranked = _rank_available(
            target_index, run.similarities, run.available, run.config.candidate_count
        )
    compatible = _without_header_conflicts(ranked, model_column, run.client_table.columns)
    columns = [run.client_table.columns[index] for index, _ in ranked]
    best_ranked = compatible or ranked
    return _CandidatePool(
        model_column=model_column,
        target_profile=target_profile,
        ranked=ranked,
        compatible=compatible,
        columns=columns,
        name_scores=dict(ranked),
        relationships=relationships,
        catalogs=catalogs,
        client_profiles=run.client_profiles,
        best_column=run.client_table.columns[best_ranked[0][0]] if best_ranked else None,
        best_name=_name_score_context(best_ranked),
    )


# --- Segunda passagem -----------------------------------------------------------


@dataclass
class _Choice:
    """Registra a coluna proposta, a decisão que a sustenta e o método que a selecionou."""

    column: ColumnRef | None
    decision: DataMappingDecision
    method: str


def _second_pass(
    run: _MappingRun, target_index: int, data_resolver: DataResolver | None
) -> MappingResult:
    """Avalia as candidatas pelos dados, aplica os ajustes locais e decide se a proposta pode ser aceita."""
    pool = _candidate_pool(run, target_index, online=data_resolver is not None)
    model_column = pool.model_column
    if not pool.ranked:
        return _mapping_row(
            model_column, None, _NO_NAME_SCORES,
            data_confidence=None, final_confidence=0.0,
            method="data", status="no_match", reason=REASON_NO_UNUSED,
        )
    if data_resolver is None:
        return _mapping_row(
            model_column, pool.best_column, pool.best_name,
            data_confidence=None, final_confidence=round(pool.best_name[0], 4),
            method="name_only", status="review", reason=REASON_OFFLINE,
        )

    candidates = [
        (column, run.client_table.series(column), pool.name_scores[column.index])
        for column in pool.columns
    ]
    catalog_decision = _unique_distinctive_catalog_decision(
        candidates=candidates, catalog_evidence=pool.catalogs
    )
    local_choice = _relationship_choice(pool, run.config)
    local_decision = catalog_decision or (local_choice.decision if local_choice else None)
    try:
        decision = local_decision or _ask_resolver(run, data_resolver, pool, candidates)
    except Exception as exc:  # Uma falha da API não interrompe o relatório.
        return _mapping_row(
            model_column, pool.best_column, pool.best_name,
            data_confidence=None, final_confidence=round(pool.best_name[0], 4),
            method="data_error", status="review",
            reason=REASON_DATA_ERROR.format(exc_type=type(exc).__name__, exc=exc),
        )

    # Registra a confiança da decisão para auditoria; a confiança de aceitação será recalculada.
    resolver_ran = local_decision is None
    llm_confidences = {
        "llm_data_confidence": round(decision.data_confidence, 4) if resolver_ran else None,
        "llm_final_confidence": round(decision.final_confidence, 4) if resolver_ran else None,
    }
    column_by_id = {column.id: column for column in pool.columns}
    choice = _Choice(
        column=column_by_id.get(decision.client_column_id or ""),
        decision=decision,
        method="catalog" if catalog_decision is not None else "data",
    )
    choice = _apply_local_overrides(choice, pool, run.config)
    if choice.column is None:
        return _mapping_row(
            model_column, None, pool.best_name,
            data_confidence=round(choice.decision.data_confidence, 4), final_confidence=0.0,
            method="data", status="no_match", reason=choice.decision.reason,
            **llm_confidences,
        )
    return _judge_proposal(run, pool, choice, llm_confidences)


def _ask_resolver(
    run: _MappingRun,
    data_resolver: DataResolver,
    pool: _CandidatePool,
    candidates: list[tuple[ColumnRef, pd.Series, float]],
) -> DataMappingDecision:
    """Envia as candidatas e suas evidências à IA, que pode escolher uma delas ou nenhuma."""
    client_columns = run.client_table.columns
    candidate_indices = {column.index for column, _, _ in candidates}

    def by_column_id(evidence: dict[int, list[dict[str, object]]]) -> dict[str, list]:
        return {
            client_columns[index].id: items
            for index, items in evidence.items()
            if index in candidate_indices
        }

    prepared = {}
    if isinstance(data_resolver, OpenAIDataResolver):
        prepared = {
            "target_profile": pool.target_profile,
            "candidate_profiles": {column.id: pool.client_profiles[column.index]
                                   for column in pool.columns},
        }
    return data_resolver.resolve(
        model_column=pool.model_column,
        model_values=run.model_table.series(pool.model_column),
        candidates=candidates,
        sample_size=run.config.sample_size,
        relationship_evidence=by_column_id(pool.relationships),
        catalog_evidence=by_column_id(pool.catalogs),
        **prepared,
    )


def _unique_distinctive_catalog_decision(
    *,
    candidates: Sequence[tuple[ColumnRef, pd.Series, float]],
    catalog_evidence: dict[int, list[dict[str, object]]],
    minimum_score: float = CATALOG_DECISION_MIN_SCORE,
    minimum_lead: float = CATALOG_DECISION_MIN_LEAD,
) -> DataMappingDecision | None:
    """Escolhe a única candidata com evidência suficiente de catálogo, sem consultar a IA."""
    ranked: list[tuple[float, float, ColumnRef, dict[str, object]]] = []
    for column, _, name_score in candidates:
        best = best_catalog_evidence(catalog_evidence.get(column.index, []))
        if catalog_evidence_is_distinctive(best, final_threshold=minimum_score):
            ranked.append((float(best["score"]), name_score, column, best))
    if not ranked:
        return None
    ranked.sort(key=lambda item: (-item[0], -item[1], item[2].index))
    catalog_score, name_score, column, evidence = ranked[0]
    runner_up = ranked[1][0] if len(ranked) > 1 else 0.0
    if catalog_score - runner_up < minimum_lead:
        return None
    final_confidence = max(
        LOCAL_DECISION_FLOOR,
        LOCAL_DECISION_NAME_WEIGHT * name_score + LOCAL_DECISION_DATA_WEIGHT * catalog_score,
    )
    return DataMappingDecision(
        client_column_id=column.id,
        data_confidence=round(catalog_score, 4),
        final_confidence=round(final_confidence, 4),
        reason=REASON_CATALOG_DECISION.format(
            catalog=_catalog_label(evidence["catalog"]),
            distinct_coverage=schema.format_percent_br(
                float(evidence.get("distinct_coverage", evidence["coverage"]))
            ),
            unique_formatted_count=int(evidence["unique_formatted_count"]),
            coverage=schema.format_percent_br(float(evidence["coverage"])),
        ),
    )


def _apply_local_overrides(choice: _Choice, pool: _CandidatePool, config: MapperConfig) -> _Choice:
    """Aplica, em ordem, as regras locais de ajuste à escolha do catálogo ou da IA.

    Cada regra retorna uma nova escolha quando se aplica, ou ``None`` para
    manter a escolha atual.
    """
    unique_relationship = _unique_relationship_override(choice, pool, config)
    if unique_relationship is not None:
        choice = unique_relationship
    else:
        choice = _distinctive_overlap_override(choice, pool) or choice
    choice = _joint_name_and_relationship_override(choice, pool, config) or choice
    choice = _null_fallback_override(choice, pool, config) or choice
    return _preserve_best_name_override(choice, pool, config) or choice


def _relationship_choice(pool: _CandidatePool, config: MapperConfig) -> _Choice | None:
    """Procura uma escolha aritmética decisiva antes de qualquer consulta externa."""
    return _unique_relationship_override(
        _Choice(None, DataMappingDecision(client_column_id=None, data_confidence=0.0,
                                         final_confidence=0.0, reason=""), "data"),
        pool, config,
    )


def _unique_relationship_override(
    choice: _Choice, pool: _CandidatePool, config: MapperConfig
) -> _Choice | None:
    """Prioriza a candidata sem conflito cuja relação fiscal tem vantagem clara sobre as demais."""
    ranked = sorted(
        (
            (pool.relationship_score(column.index), pool.name_scores[column.index], column)
            for column in pool.columns
            if pool.is_compatible(column)
        ),
        key=lambda item: (-item[0], -item[1], item[2].index),
    )
    if not ranked:
        return None
    best_score, name_score, column = ranked[0]
    runner_up = ranked[1][0] if len(ranked) > 1 else 0.0
    if not (
        best_score >= config.final_threshold
        and best_score - runner_up >= UNIQUE_RELATIONSHIP_MIN_LEAD
    ):
        return None
    decision = choice.decision
    same_candidate = choice.column is not None and choice.column.index == column.index
    final_confidence = round(
        NAME_WEIGHT_WITH_OBJECTIVE * name_score + OBJECTIVE_WEIGHT * best_score, 4
    )
    return _Choice(
        column=column,
        decision=decision.model_copy(
            update={
                "client_column_id": column.id,
                "data_confidence": (
                    max(decision.data_confidence, best_score) if same_candidate else best_score
                ),
                "final_confidence": (
                    max(decision.final_confidence, final_confidence)
                    if same_candidate
                    else final_confidence
                ),
                "reason": REASON_UNIQUE_RELATIONSHIP,
            }
        ),
        method="data",
    )


def _distinctive_overlap_override(choice: _Choice, pool: _CandidatePool) -> _Choice | None:
    """Escolhe a candidata com vantagem clara na quantidade de amostras distintivas em comum."""
    target_profile = pool.target_profile
    ranked = sorted(
        (
            (
                distinctive_sample_overlap(target_profile, pool.client_profiles[column.index]),
                distinctive_sample_overlap_count(target_profile, pool.client_profiles[column.index]),
                pool.name_scores[column.index],
                column,
            )
            for column in pool.columns
        ),
        key=lambda item: (-item[1], -item[0], -item[2], item[3].index),
    )
    best_overlap, best_count, name_score, column = ranked[0]
    second_count = ranked[1][1] if len(ranked) > 1 else 0
    second_overlap = ranked[1][0] if len(ranked) > 1 else 0.0
    chosen = choice.column
    chosen_overlap = (
        distinctive_sample_overlap(target_profile, pool.client_profiles[chosen.index])
        if chosen is not None
        else 0.0
    )
    chosen_name_score = pool.name_scores[chosen.index] if chosen is not None else 0.0
    if not (
        best_count >= MIN_SHARED_SAMPLES
        and (best_count > second_count or best_overlap > second_overlap)
        and best_overlap > chosen_overlap
        and (
            name_score >= chosen_name_score - OVERLAP_MAX_NAME_DEFICIT
            or best_overlap >= STRONG_OVERLAP
        )
    ):
        return None
    data_confidence = _overlap_evidence_score(best_overlap)
    final_confidence = max(
        LOCAL_DECISION_FLOOR,
        LOCAL_DECISION_NAME_WEIGHT * name_score + LOCAL_DECISION_DATA_WEIGHT * data_confidence,
    )
    if best_overlap >= STRONG_OVERLAP:
        final_confidence = max(STRONG_OVERLAP_FINAL_FLOOR, final_confidence)
    return _Choice(
        column=column,
        decision=choice.decision.model_copy(
            update={
                "client_column_id": column.id,
                "data_confidence": round(data_confidence, 4),
                "final_confidence": round(final_confidence, 4),
                "reason": REASON_OVERLAP,
            }
        ),
        method="data",
    )


def _joint_name_and_relationship_override(
    choice: _Choice, pool: _CandidatePool, config: MapperConfig
) -> _Choice | None:
    """Explica a concordância quando o cabeçalho e a relação fiscal apoiam a mesma escolha.

    Mais de uma coluna pode satisfazer a equação. O cabeçalho ajuda a desempatar
    quando a combinação dos scores tem vantagem clara. A coluna escolhida não muda.
    """
    chosen = choice.column
    if chosen is None:
        return None
    relationship = pool.relationship_score(chosen.index)
    if not (relationship >= config.final_threshold):
        return None
    joint_score = (
        NAME_WEIGHT_WITH_OBJECTIVE * pool.name_scores[chosen.index] + OBJECTIVE_WEIGHT * relationship
    )
    runner_up = max(
        (
            NAME_WEIGHT_WITH_OBJECTIVE * pool.name_scores[column.index]
            + OBJECTIVE_WEIGHT * pool.relationship_score(column.index)
            for column in pool.columns
            if pool.is_compatible(column) and column.index != chosen.index
        ),
        default=0.0,
    )
    if not (
        joint_score >= config.final_threshold
        and joint_score - runner_up >= config.name_margin_threshold
    ):
        return None
    decision = choice.decision
    return _Choice(
        column=chosen,
        decision=decision.model_copy(
            update={
                "reason": REASON_JOINT,
            }
        ),
        method=choice.method,
    )


def _null_fallback_override(
    choice: _Choice, pool: _CandidatePool, config: MapperConfig
) -> _Choice | None:
    """Mantém a melhor candidata pelo cabeçalho se a IA não escolheu e há dados favoráveis ou papel compatível."""
    if choice.column is not None:
        return None
    best = pool.best_column
    best_name = pool.best_name[0]
    favorable_data = (
        choice.decision.data_confidence >= max(0.0, config.weak_final_threshold)
        and best_name >= config.near_name_threshold
    )
    same_role = (
        _same_explicit_role(pool.model_column.name, best.name)
        and best_name >= config.weak_name_threshold
    )
    if not (favorable_data or same_role):
        return None
    return _Choice(
        column=best,
        decision=choice.decision.model_copy(
            update={
                "client_column_id": best.id,
                "final_confidence": best_name,
                "reason": REASON_NULL_FALLBACK,
            }
        ),
        method=choice.method,
    )


def _preserve_best_name_override(
    choice: _Choice, pool: _CandidatePool, config: MapperConfig
) -> _Choice | None:
    """Preserva a escolha pelo cabeçalho quando dados vazios ou zerados tornam a comparação de formato fraca."""
    best = pool.best_column
    best_name, _, best_margin = pool.best_name
    rows_are_weak = (
        pool.target_profile["evidence_quality"] in _WEAK_EVIDENCE
        or pool.client_profiles[best.index]["evidence_quality"] in _WEAK_EVIDENCE
    )
    clear_name = (
        best_name >= config.near_name_threshold
        and best_margin >= config.name_margin_threshold
        and rows_are_weak
    )
    chosen = choice.column
    # Uma escolha com relação fiscal ou catálogo forte não é trocada.
    replaceable = chosen is None or (
        chosen.index != best.index
        and pool.relationship_score(chosen.index) < config.final_threshold
        and _catalog_score(pool.catalogs.get(chosen.index, [])) < config.final_threshold
    )
    if not (clear_name and replaceable):
        return None
    return _Choice(
        column=best,
        decision=choice.decision.model_copy(
            update={
                "client_column_id": best.id,
                "data_confidence": 0.0,
                "final_confidence": best_name,
                "reason": REASON_PRESERVE_BEST,
            }
        ),
        method=choice.method,
    )


@dataclass
class _LocalEvidence:
    """Reúne as evidências calculadas pelo código, sem usar a confiança informada pela IA."""

    name_confidence: float
    runner_up_confidence: float
    name_margin: float
    name_is_decisive: bool
    same_explicit_role: bool
    header_conflict: str | None
    relationship_score: float
    relationship_contradiction: bool
    catalog: dict[str, object] | None
    catalog_score: float
    catalog_distinct_coverage: float
    catalog_objective_score: float
    overlap_score: float
    identifier_score: float
    #: A melhor evidência independente do cabeçalho: relação, catálogo distintivo, amostras ou DV.
    objective_data_score: float
    data_confidence: float
    final_confidence: float


def _local_evidence(pool: _CandidatePool, chosen: ColumnRef, config: MapperConfig) -> _LocalEvidence:
    model_name = pool.model_column.name
    # A margem é medida contra os demais sem conflito, quando a escolhida não
    # conflita; fica negativa se a escolha não é o melhor cabeçalho.
    chosen_is_compatible = any(index == chosen.index for index, _ in pool.compatible)
    name_context = pool.compatible if chosen_is_compatible else pool.ranked
    name_confidence, runner_up_confidence, name_margin = _name_score_context(
        name_context, chosen.index
    )
    target_profile = pool.target_profile
    selected_profile = pool.client_profiles[chosen.index]
    relationship_rules = pool.relationships.get(chosen.index, [])
    relationship_score = _relationship_score(relationship_rules)
    catalog = best_catalog_evidence(pool.catalogs.get(chosen.index, []))
    catalog_score = float(catalog["score"]) if catalog is not None else 0.0
    catalog_objective_score = (
        catalog_score
        if catalog_evidence_is_distinctive(catalog, final_threshold=config.final_threshold)
        else 0.0
    )
    overlap_score = (
        _overlap_evidence_score(distinctive_sample_overlap(target_profile, selected_profile))
        if distinctive_sample_overlap_count(target_profile, selected_profile) >= MIN_SHARED_SAMPLES
        else 0.0
    )
    definition = target_field(model_name)
    identifier_score = identifier_match_score(
        target_profile, selected_profile,
        kinds=definition.identifier_kinds if definition is not None else None,
    )
    objective_data_score = max(
        relationship_score, catalog_objective_score, overlap_score, identifier_score
    )
    generic_profile_score = min(
        GENERIC_PROFILE_CAP, profile_similarity(target_profile, selected_profile)
    )
    data_confidence = round(max(objective_data_score, generic_profile_score), 4)
    name_is_decisive = (
        name_confidence >= config.name_threshold
        and name_margin >= config.name_margin_threshold
        and not underspecified_header_match(model_name, chosen.name)
    )
    same_explicit_role = _same_explicit_role(model_name, chosen.name)
    return _LocalEvidence(
        name_confidence=name_confidence,
        runner_up_confidence=runner_up_confidence,
        name_margin=name_margin,
        name_is_decisive=name_is_decisive,
        same_explicit_role=same_explicit_role,
        header_conflict=semantic_header_conflict(model_name, chosen.name),
        relationship_score=relationship_score,
        relationship_contradiction=_relationship_is_strong_contradiction(relationship_rules),
        catalog=catalog,
        catalog_score=catalog_score,
        # Usa a cobertura dos códigos distintos, que também serve de base
        # para o score do catálogo.
        catalog_distinct_coverage=(
            float(catalog.get("distinct_coverage", catalog["coverage"]))
            if catalog is not None
            else 0.0
        ),
        catalog_objective_score=catalog_objective_score,
        overlap_score=overlap_score,
        identifier_score=identifier_score,
        objective_data_score=objective_data_score,
        data_confidence=data_confidence,
        final_confidence=_local_final_confidence(
            name_confidence,
            objective_data_score,
            data_confidence,
            name_is_decisive=name_is_decisive,
            same_explicit_role=same_explicit_role,
            config=config,
        ),
    )


def _local_final_confidence(
    name_confidence: float,
    objective_data_score: float,
    data_confidence: float,
    *,
    name_is_decisive: bool,
    same_explicit_role: bool,
    config: MapperConfig,
) -> float:
    """Calcula a confiança final a partir do cabeçalho e das evidências, aplicando o teto quando necessário."""
    if name_is_decisive:
        return round(name_confidence, 4)
    if objective_data_score >= config.final_threshold:
        floor = (
            config.final_threshold
            if same_explicit_role or name_confidence >= config.weak_name_threshold
            else OBJECTIVE_WEAK_NAME_FLOOR
        )
        return round(
            max(
                floor,
                NAME_WEIGHT_WITH_OBJECTIVE * name_confidence
                + OBJECTIVE_WEIGHT * objective_data_score,
            ),
            4,
        )
    # Sem evidência suficiente, a proposta não pode ser aceita. O teto
    # de 0,8999 mantém a confiança abaixo do threshold padrão.
    return round(
        min(
            config.clamp_ceiling,
            max(
                name_confidence,
                CEILING_NAME_WEIGHT * name_confidence + CEILING_DATA_WEIGHT * data_confidence,
            ),
        ),
        4,
    )


def _acceptance_step(local: _LocalEvidence, header_step: str) -> str:
    """Nomeia a evidência usada no ramo que calculou a confiança de aceite."""
    if local.name_is_decisive:
        return header_step
    scores = {"arithmetic": local.relationship_score, "catalog": local.catalog_objective_score,
              "profile": local.overlap_score, "identifier": local.identifier_score}
    return deciding_step([step for step, score in scores.items()
                          if score > 0 and score == local.objective_data_score])


def _accept_gate(
    local: _LocalEvidence, config: MapperConfig, chosen_is_vacuous: bool
) -> tuple[bool, bool]:
    """Verifica a aceitação e retorna (aceito, bloqueado apenas por coluna vazia)."""
    passes_otherwise = (
        local.final_confidence >= config.final_threshold
        and (local.name_is_decisive or local.objective_data_score >= config.final_threshold)
        and local.header_conflict is None
        and not local.relationship_contradiction
    )
    return passes_otherwise and not chosen_is_vacuous, passes_otherwise and chosen_is_vacuous


def _judge_proposal(
    run: _MappingRun,
    pool: _CandidatePool,
    choice: _Choice,
    llm_confidences: dict[str, float | None],
) -> MappingResult:
    """Usa as evidências locais para aceitar a proposta, encaminhá-la à revisão ou recusá-la."""
    config = run.config
    model_column = pool.model_column
    chosen = choice.column
    local = _local_evidence(pool, chosen, config)
    accepted, vetoed_by_vacuity = _accept_gate(
        local, config, client_column_is_vacuous(run.client_table, chosen)
    )
    name_scores = (local.name_confidence, local.runner_up_confidence, local.name_margin)
    if not accepted and not _review_proposal_is_supported(
        name_confidence=local.name_confidence,
        data_confidence=local.data_confidence,
        final_confidence=local.final_confidence,
        header_conflict=local.header_conflict,
        target_profile=pool.target_profile,
        selected_profile=pool.client_profiles[chosen.index],
        relationship_score=local.relationship_score,
        catalog_score=local.catalog_score,
        catalog_coverage=local.catalog_distinct_coverage,
        same_explicit_role=local.same_explicit_role,
        identifier_score=local.identifier_score,
        config=config,
    ):
        return _mapping_row(
            model_column, None, name_scores,
            data_confidence=local.data_confidence, final_confidence=0.0,
            method="data", status="no_match", reason=REASON_UNSUPPORTED_NO_MATCH,
            **llm_confidences,
        )

    catalog = local.catalog
    return _mapping_row(
        model_column, chosen, name_scores,
        data_confidence=local.data_confidence,
        final_confidence=local.final_confidence,
        method=choice.method,
        status="accepted" if accepted else "review",
        reason=_proposal_reason(choice.decision.reason, local, model_column, chosen, vetoed_by_vacuity),
        **_evidence_attribution(
            run,
            model_column,
            chosen.index,
            decided_by=_acceptance_step(local, run.header_step) if accepted else "",
            name_confidence=local.name_confidence,
            relationship_score=local.relationship_score,
            catalog_score=local.catalog_objective_score,
            overlap_score=local.overlap_score,
            identifier_score=local.identifier_score,
            resolver_ran=llm_confidences["llm_final_confidence"] is not None,
        ),
        vetoed_by=(
            "vacuity" if vetoed_by_vacuity
            else "family_conflict" if local.header_conflict is not None
            else ""
        ),
        # A cobertura distinta ainda não é exportada neste caminho; veja docs/KNOWN_ISSUES.md.
        catalog_name=str(catalog["catalog"]) if catalog is not None else None,
        catalog_coverage=round(float(catalog["coverage"]), 4) if catalog is not None else None,
        catalog_confidence=round(local.catalog_score, 4) if catalog is not None else None,
        **llm_confidences,
    )


def _proposal_reason(
    decision_reason: str,
    local: _LocalEvidence,
    model_column: ColumnRef,
    chosen: ColumnRef,
    vetoed_by_vacuity: bool,
) -> str:
    """Retorna a justificativa da decisão ou o motivo do bloqueio, quando uma regra impediu a aceitação."""
    if local.header_conflict is not None:
        # A verificação usa o valor interno em inglês; o relatório exibe sua descrição em português.
        return REASON_CONFLICT_REVIEW.format(
            header_conflict=describe_header_conflict_pt(model_column.name, chosen.name)
        )
    if local.relationship_contradiction:
        return REASON_CONTRADICTION
    if vetoed_by_vacuity:
        # Explicita o bloqueio para que o revisor entenda por que um score alto não bastou.
        return REASON_VACUITY.format(
            client_column=schema.display_name_to_external(chosen.display_name)
        )
    return decision_reason


def _review_proposal_is_supported(
    *,
    name_confidence: float,
    data_confidence: float,
    final_confidence: float,
    header_conflict: str | None,
    target_profile: dict[str, object],
    selected_profile: dict[str, object],
    relationship_score: float,
    catalog_score: float,
    catalog_coverage: float,
    same_explicit_role: bool,
    identifier_score: float,
    config: MapperConfig,
) -> bool:
    """Verifica se há evidência suficiente para manter uma proposta em revisão.

    Sem esse apoio, o resultado será ``sem_correspondencia``. Quando há
    conflito de cabeçalho, a proposta precisa de evidência distintiva nos dados.
    """
    near_name_threshold = max(0.0, config.near_name_threshold)
    weak_name_threshold = max(0.0, config.weak_name_threshold)
    rows_are_informative = (
        target_profile["evidence_quality"] == "informative"
        and selected_profile["evidence_quality"] == "informative"
    )
    distinctive_rows = (
        distinctive_sample_overlap_count(target_profile, selected_profile) >= MIN_SHARED_SAMPLES
        or identifier_score >= DISTINCTIVE_IDENTIFIER_SCORE
    )
    relationship_is_strong = relationship_score >= config.final_threshold
    distinctive_catalog = (
        catalog_score >= config.final_threshold
        and catalog_coverage >= REVIEW_MIN_CATALOG_COVERAGE
    )

    if header_conflict is not None:
        return bool(
            (distinctive_rows or relationship_is_strong)
            and data_confidence >= config.final_threshold
            and final_confidence >= config.final_threshold
        ) or bool(
            distinctive_catalog
            and data_confidence >= config.final_threshold
            and final_confidence >= max(0.0, config.weak_final_threshold)
        )
    if name_confidence >= near_name_threshold:
        return True
    if (
        same_explicit_role
        and name_confidence >= weak_name_threshold
        and final_confidence >= weak_name_threshold
    ):
        return True
    return bool(
        rows_are_informative
        and name_confidence >= weak_name_threshold
        and data_confidence >= near_name_threshold
        and final_confidence >= weak_name_threshold
    ) or bool(
        (distinctive_rows or relationship_is_strong or distinctive_catalog)
        and data_confidence >= near_name_threshold
        and final_confidence >= weak_name_threshold
    )
