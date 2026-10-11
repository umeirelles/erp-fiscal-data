"""Registra as etapas que justificam ou bloqueiam um mapeamento.

O relatório usa esse registro para mostrar ao revisor quais evidências
apoiaram a escolha e quais regras impediram sua aceitação. Várias etapas
podem apoiar a mesma proposta; um guard pode bloqueá-la mesmo assim.

``transfers`` indica se a evidência também se aplica a ERPs com cabeçalhos
desconhecidos. Relações aritméticas, catálogos oficiais e dígitos verificadores
oferecem esse apoio. A comparação das palavras do cabeçalho depende do
vocabulário reconhecido pelo mapeador. Essa distinção orienta a métrica de
generalização.

Os IDs internos, como ``lexical`` e ``catalog``, permanecem em inglês.
``report_schema`` os converte na gravação e na leitura. Antes de usar
``deciding_step`` ou ``format_steps`` com uma célula do relatório, converta-a
com ``report_schema.steps_to_internal``. Ao incluir uma etapa, cadastre também
seu ID no formato do relatório em ``STEP_IDS`` ou ``GUARD_IDS``. O teste em
``tests/test_report_schema.py`` verifica essa correspondência.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Step:
    """Descreve uma evidência ou um guard usado na decisão de mapeamento."""

    id: str
    name: str
    summary: str
    #: Indica se a evidência independe do vocabulário usado pelo ERP.
    transfers: bool
    #: Referência à implementação da etapa.
    implemented_by: str


#: Evidências em ordem de apresentação e desempate. O mapper registra como
#: decisiva a evidência usada para aceitar, não toda evidência de apoio.
EVIDENCE_STEPS: tuple[Step, ...] = (
    Step(
        id="resolver",
        name="Escolha pelo resolver de IA",
        summary=(
            "A IA compara as candidatas com base nas evidências reunidas pelas "
            "outras etapas. Retorna uma escolha, sua confiança e uma justificativa "
            "breve. É consultada quando as etapas anteriores não resolvem o mapeamento."
        ),
        # A escolha da IA, por si só, não demonstra generalização. Esse apoio
        # vem das evidências usadas na escolha, como catálogo ou aritmética.
        transfers=False,
        implemented_by="_openai_data_decision",
    ),
    Step(
        id="lexical",
        name="Correspondência léxica de cabeçalho",
        summary=(
            "Compara o texto dos cabeçalhos depois de normalizar acentos, "
            "maiúsculas e minúsculas, pontuação e abreviações fiscais "
            "conhecidas, e pontua a sobreposição de termos e a distância de "
            "edição. Lida com os cabeçalhos truncados dos ERPs: 'Vlr Isen ICM' "
            "é reconhecido como o campo de isentas do ICMS."
        ),
        transfers=False,
        implemented_by="LexicalHeaderScorer, normalize_header, semantic_header_signature",
    ),
    Step(
        id="semantic",
        name="Correspondência semântica de cabeçalho",
        summary=(
            "Usa embeddings para comparar o significado dos cabeçalhos. Assim, "
            "'Nota Fiscal number' pode corresponder a 'Nº da Nota Fiscal' mesmo "
            "sem ter todas as palavras em comum. Disponível no modo online."
        ),
        transfers=False,
        implemented_by="OpenAIEmbeddingHeaderScorer",
    ),
    Step(
        id="profile",
        name="Correspondência de perfil de valores",
        summary=(
            "Compara o formato dos próprios dados (tipo inferido, quantidade "
            "de dígitos, comprimento dos textos, distribuição numérica) e "
            "qualquer coincidência exata de valores de amostra distintivos."
        ),
        transfers=True,
        implemented_by="profile_similarity, distinctive_sample_overlap",
    ),
    Step(
        id="identifier",
        name="Validação de identificador (DV)",
        summary=(
            "Verifica dígitos de controle de CPF, CNPJ e chave de acesso de "
            "44 dígitos da NF-e. Essa validação ajuda a reconhecer o conteúdo "
            "independentemente do cabeçalho. Os tipos permitidos vêm da definição "
            "do campo em target_fields.json; cabeçalhos fora desse registro "
            "continuam usando o perfil do modelo como fallback."
        ),
        transfers=True,
        implemented_by="identifier_match_score, _cpf_is_valid, _cnpj_is_valid, _nfe_key_is_valid",
    ),
    Step(
        id="catalog",
        name="Consulta a catálogo fiscal",
        summary=(
            "Verifica se os valores pertencem às tabelas oficiais de códigos "
            "(CFOP, NCM, código de município do IBGE, as famílias de CST, "
            "CSOSN e os códigos do IBS/CBS). Reduz o peso de domínios curtos, "
            "como o CST de dois dígitos, nos quais códigos de campos diferentes "
            "podem coincidir."
        ),
        transfers=True,
        implemented_by="reference_catalog.best_catalog_evidence, catalog_evidence_is_distinctive",
    ),
    Step(
        id="arithmetic",
        name="Aritmética fiscal",
        summary=(
            "Verifica relações entre os valores: valor do tributo = base x "
            "alíquota e valor do item = quantidade x preço unitário. Essas "
            "relações ajudam a identificar o papel de cada coluna sem depender "
            "do vocabulário do ERP. Esta etapa tem a maior prioridade no registro "
            "da evidência decisiva."
        ),
        transfers=True,
        implemented_by="tax_calculation_evidence, discover_client_tax_trios, line_calculation_role",
    ),
)


#: Guards que impedem a aceitação de uma proposta. ``vacuity``,
#: ``family_conflict`` e ``exclusivity`` são registrados em ``vetado_por``.
GUARDS: tuple[Step, ...] = (
    Step(
        id="vacuity",
        name="Veto por coluna vazia",
        summary=(
            "Impede a aceitação de uma coluna do cliente sem dados. Isso evita "
            "que a saída apresente uma coluna como mapeada quando nenhuma "
            "célula foi preenchida. A avaliação classifica esse caso como fabricação."
        ),
        transfers=True,
        implemented_by="client_column_is_vacuous",
    ),
    Step(
        id="family_conflict",
        name="Veto por conflito tributário",
        summary=(
            "Recusa um mapeamento entre famílias de tributo ou campos "
            "incompatíveis (ICMS não é ICSP, base de cálculo não é alíquota), "
            "por mais parecidos que sejam os cabeçalhos."
        ),
        transfers=True,
        implemented_by="semantic_header_conflict, header_tax_families, header_semantic_role",
    ),
    Step(
        id="underspecified",
        name="Veto por cabeçalho genérico",
        summary=(
            "Recusa uma correspondência em que o cabeçalho do cliente é só um "
            "termo genérico, que não nomeia nenhum conceito específico do "
            "modelo."
        ),
        transfers=True,
        implemented_by="underspecified_header_match",
    ),
    Step(
        id="exclusivity",
        name="Coluna de origem já utilizada",
        summary=(
            "Remove do conjunto de candidatas cada coluna do cliente já aceita. "
            "Isso garante que ela seja usada como origem de apenas uma coluna "
            "do modelo."
        ),
        transfers=True,
        implemented_by="build_mapping (o conjunto available)",
    ),
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
