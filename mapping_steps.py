"""As etapas nomeadas da análise de mapeamento de colunas.

Cada mapeamento informa quais etapas o sustentaram, para que o revisor veja
*como* uma coluna foi casada, e não só *que* foi. Há dois tipos:

* uma **etapa de evidência** é um motivo para acreditar no mapeamento; várias
  podem sustentar o mesmo;
* uma **trava** é um motivo para recusá-lo e prevalece sobre qualquer
  evidência, porque num documento fiscal uma coluna errada é pior do que uma
  vazia.

``transfers`` diz se a etapa continua valendo num ERP nunca visto: as que se
apoiam na legislação, nas tabelas oficiais ou nos dígitos verificadores se
transferem; as que leem as palavras do cabeçalho do cliente, não. A fração
dos mapeamentos apoiados em etapas transferíveis é a métrica de
generalização da ferramenta.

Os ids (``lexical``, ``catalog``...) são internos, em inglês;
``report_schema`` os grava em português. Uma célula lida de um relatório passa
por ``report_schema.steps_to_internal`` antes de chegar a ``deciding_step`` ou
``format_steps``. Uma etapa nova precisa também de um id em português em
``report_schema.STEP_IDS`` (ou ``GUARD_IDS``); ``tests/test_report_schema.py``
acusa se faltar.
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
        # A mais fraca e não transferível de propósito: o resolvedor é um
        # árbitro, não uma evidência. O que se transfere é a evidência por
        # baixo dele, e um mapeamento apoiado só no cabeçalho não passa por
        # transferível porque foi a IA quem o escolheu.
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
#: Só ``vacuity`` e ``family_conflict`` chegam a ``vetado_por`` (KNOWN_ISSUES 2.1).
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
    """A etapa de apoio mais forte, pela ordem de EVIDENCE_STEPS; ``""`` se não houver.

    Cabeçalho e aritmética juntos dão aritmética: é a alegação que valeria num
    cliente cujos cabeçalhos não conseguimos ler.
    """
    ids = _evidence_ids(supporting)
    return max(ids, key=lambda step: _EVIDENCE_RANK[step]) if ids else ""


def format_steps(supporting: object) -> str:
    """Monta a célula de planilha com as etapas de apoio, a mais forte por último."""
    ids = _evidence_ids(supporting)
    return " + ".join(sorted(ids, key=lambda step: _EVIDENCE_RANK[step]))


def rests_only_on_headers(supporting: object) -> bool:
    """Se nada além do texto do cabeçalho do cliente sustenta o mapeamento.

    True quer dizer que ele não sobreviveria a um ERP que usa outras palavras
    para o mesmo conceito.
    """
    ids = set(_evidence_ids(supporting))
    return bool(ids) and not (ids & TRANSFERRING_STEP_IDS)


def _evidence_ids(supporting: object) -> list[str]:
    """Os ids de etapa de evidência de uma célula (``"lexical + catalog"``) ou sequência.

    O resto é ignorado: travas, ids em português e o ``unrecorded`` e o
    ``catalog (agrees)`` dos relatórios arquivados.
    """
    if supporting is None:
        return []
    if isinstance(supporting, str):
        parts = supporting.replace("+", ",").split(",")
    else:
        parts = [str(part) for part in supporting]
    return [part.strip() for part in parts if part.strip() in _EVIDENCE_RANK]
