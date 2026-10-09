"""As etapas nomeadas da análise de mapeamento de colunas.

Este módulo é o único lugar em que o raciocínio do pipeline recebe nomes
oficiais. Cada mapeamento que a ferramenta produz informa quais destas etapas
sustentaram a decisão e quais apenas a corroboraram, para que o revisor veja
*como* uma coluna foi casada, e não só *que* ela foi casada.

Há dois tipos de etapa, e a diferença importa:

* Uma **etapa de evidência** é um motivo para acreditar que uma coluna do
  cliente corresponde a uma coluna do modelo. As evidências se somam: várias
  etapas podem sustentar o mesmo mapeamento.
* Uma **trava** é um motivo para recusá-lo. Uma trava que dispara prevalece
  sobre qualquer quantidade de evidência, porque, num documento fiscal, uma
  coluna errada é pior do que uma coluna vazia.

Cada etapa de evidência declara, no campo ``transfers``, se é transferível,
isto é, se continua valendo num ERP desconhecido. Uma etapa transferível se
apoia em algo que vale para os documentos fiscais brasileiros em geral (a
legislação tributária, as tabelas oficiais de códigos, a conta dos dígitos
verificadores) e por isso funciona num ERP que ninguém viu antes. Uma etapa
não transferível se apoia nas palavras que este cliente específico escolheu
para os cabeçalhos e vale pouco no próximo cliente. A parcela dos
mapeamentos apoiados em etapas transferíveis é a métrica de generalização da
ferramenta inteira. Os quatro clientes de amostra são material de
referência, então um ganho que aparece só nas etapas não transferíveis quase
não vale nada.

Os ids das etapas (``lexical``, ``catalog`` ...) são vocabulário interno e
continuam em inglês. Nos relatórios em português eles são gravados como
``lexico``, ``catalogo`` etc. por ``report_schema``. Uma célula de etapas
lida de um relatório precisa passar por ``report_schema.steps_to_internal``
antes de chegar a ``deciding_step`` ou ``format_steps``, que só reconhecem
os ids internos.

Acrescentar uma etapa é acrescentar uma entrada aqui, preenchê-la no ponto da
decisão e dar a ela um id em português em ``report_schema.STEP_IDS`` (ou
``GUARD_IDS``, se for uma trava); ``tests/test_report_schema.py`` acusa se
as duas listas divergirem. Mudar o nome ou o resumo de uma etapa é editar
uma linha.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Step:
    """Uma forma nomeada de a ferramenta justificar, ou recusar, o mapeamento de uma coluna."""

    id: str
    name: str
    summary: str
    #: Se o raciocínio da etapa continua válido num ERP de vocabulário desconhecido.
    transfers: bool
    #: Onde a lógica está, para quem lê poder ir conferir.
    implemented_by: str


#: Etapas de evidência, da alegação de correção mais fraca para a mais forte.
#: A ordem tem significado: quando várias etapas sustentam um mapeamento, a
#: última delas nesta tupla é a informada como a que o decidiu.
EVIDENCE_STEPS: tuple[Step, ...] = (
    Step(
        id="resolver",
        name="Adjudicação pela IA",
        summary=(
            "A IA pondera a evidência de todas as outras etapas sobre os "
            "candidatos pré-selecionados e escolhe um, informando a própria "
            "confiança e uma justificativa de uma frase. Só entra em ação nas "
            "colunas que as etapas anteriores deixaram em dúvida."
        ),
        # Esta etapa fica na posição mais fraca de propósito e é marcada como
        # não transferível. O resolvedor é um árbitro, não uma evidência: o que
        # se generaliza é a evidência por baixo dele. Assim, "decidido pelo
        # resolvedor" mantém o seu significado honesto (nada objetivo sustentou
        # o mapeamento e o julgamento da IA decidiu), e um mapeamento apoiado só
        # no cabeçalho não passa por transferível apenas porque foi o resolvedor
        # quem o escolheu.
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
            "Compara o significado dos nomes de cabeçalho por meio de "
            "embeddings, de modo que 'Nota Fiscal number' pode chegar a "
            "'Nº da Nota Fiscal' sem depender de termos em comum. Exige o "
            "modo online."
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
            "Reconhece identificadores brasileiros pelos dígitos verificadores, "
            "e não pelo cabeçalho: CPF, CNPJ e a chave de acesso de 44 dígitos "
            "da NF-e. Uma coluna cujos valores passam na validação é aquele "
            "identificador, qualquer que seja o nome dela."
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
            "CSOSN e os códigos do IBS/CBS), descontando domínios curtos e "
            "sujeitos a colisão, como o CST de dois dígitos."
        ),
        transfers=True,
        implemented_by="reference_catalog.best_catalog_evidence, catalog_evidence_is_distinctive",
    ),
    Step(
        id="arithmetic",
        name="Aritmética fiscal",
        summary=(
            "Prova o papel de uma coluna por uma identidade que vem da "
            "legislação tributária, e não do vocabulário: valor = base x "
            "alíquota num trio fiscal, e valor do item = quantidade x preço "
            "unitário. É a evidência mais forte que a ferramenta tem, e a "
            "mais transferível."
        ),
        transfers=True,
        implemented_by="tax_calculation_evidence, discover_client_tax_trios, line_calculation_role",
    ),
)


#: Travas. Uma trava que dispara recusa o mapeamento, qualquer que seja a evidência.
GUARDS: tuple[Step, ...] = (
    Step(
        id="vacuity",
        name="Veto por coluna vazia",
        summary=(
            "Recusa um mapeamento para uma coluna do cliente sem nenhuma linha "
            "preenchida. Se fosse entregue, seria uma coluna que parece "
            "mapeada e está em branco sem aviso: fabricação por construção."
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
        name="Origem já consumida",
        summary=(
            "Uma coluna do cliente aceita para uma coluna do modelo sai do "
            "conjunto de candidatas, para que duas colunas do modelo não "
            "reivindiquem a mesma origem."
        ),
        transfers=True,
        implemented_by="build_mapping (o conjunto available)",
    ),
)

STEPS_BY_ID: dict[str, Step] = {
    step.id: step for step in (*EVIDENCE_STEPS, *GUARDS)
}

#: Ordem de apresentação, com a alegação mais forte por último; ver EVIDENCE_STEPS.
_EVIDENCE_RANK: dict[str, int] = {
    step.id: index for index, step in enumerate(EVIDENCE_STEPS)
}

#: Etapas cujo raciocínio continua válido num ERP nunca visto.
TRANSFERRING_STEP_IDS: frozenset[str] = frozenset(
    step.id for step in EVIDENCE_STEPS if step.transfers
)


def deciding_step(supporting: object) -> str:
    """A etapa informada como a que sustentou a decisão.

    Vence a etapa de apoio mais forte, pela ordem de EVIDENCE_STEPS. Um
    mapeamento corroborado tanto por uma correspondência de cabeçalho quanto
    pela aritmética fiscal é informado como decidido pela aritmética, porque
    essa é a alegação que continuaria valendo num cliente cujos cabeçalhos
    não conseguimos ler.

    O que não é id de etapa de evidência é ignorado: travas, o
    ``unrecorded`` e o ``catalog (agrees)`` dos relatórios arquivados e ids
    em português. Se nada sobra, devolve ``""``.
    """
    ranked = [step for step in _as_ids(supporting) if step in _EVIDENCE_RANK]
    if not ranked:
        return ""
    return max(ranked, key=lambda step: _EVIDENCE_RANK[step])


def format_steps(supporting: object) -> str:
    """Monta a célula de planilha com as etapas de apoio, a mais forte por último."""
    ranked = sorted(
        (step for step in _as_ids(supporting) if step in _EVIDENCE_RANK),
        key=lambda step: _EVIDENCE_RANK[step],
    )
    return " + ".join(ranked)


def rests_only_on_headers(supporting: object) -> bool:
    """Se nada além do texto do cabeçalho do cliente sustenta este mapeamento.

    É a pergunta que antecipa o resultado no cliente nº 5. True significa que
    o mapeamento não sobreviveria a um ERP que usa outras palavras para o
    mesmo conceito.
    """
    ids = {step for step in _as_ids(supporting) if step in _EVIDENCE_RANK}
    return bool(ids) and not (ids & TRANSFERRING_STEP_IDS)


def _as_ids(supporting: object) -> list[str]:
    """Separa uma célula (``"lexical + catalog"``) ou uma sequência de ids numa lista de ids."""
    if supporting is None:
        return []
    if isinstance(supporting, str):
        return [part.strip() for part in supporting.replace("+", ",").split(",") if part.strip()]
    return [str(part).strip() for part in supporting if str(part).strip()]
