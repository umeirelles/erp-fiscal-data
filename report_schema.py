"""Esquema externo, em português, dos relatórios de mapeamento e das planilhas mapeadas.

Este módulo é a fronteira entre o vocabulário de máquina que o código usa em
memória e o que aparece nos arquivos que uma pessoa abre. O código continua
pensando em inglês nos valores de máquina: o status ``accepted``, os ids de
etapa (``semantic``), os ids de catálogo de ``reference_data/`` e o nome de
exibição ``Nome (occurrence 2)`` de ``ColumnRef``. Esses valores viram
português só aqui, ao gravar, e voltam ao vocabulário interno ao ler.

As frases para pessoas (justificativa, prova aritmética, detalhe dos sinais,
motivo de retenção) **não** são traduzidas aqui: elas são escritas em
português direto na origem, onde são montadas. Por isso este módulo não tem
modelos de frase. O que ele oferece para essas frases é o vocabulário
(rótulos de catálogo, de papel do cabeçalho etc.), o marcador da prova não
decisiva e a formatação de números com vírgula decimal.

O que nunca passa por aqui, porque é entrada do modelo de IA e precisa ficar
idêntico byte a byte: o prompt do resolvedor, o prefixo do embedding, o
esquema ``DataMappingDecision``, o payload enviado à IA, o texto de
``semantic_header_conflict`` e ``ColumnRef.name`` / ``ColumnRef.display_name``
em memória.

Só o esquema em português (versão 3) é lido. Os relatórios do formato
antigo, em inglês (versão 2, anterior à tradução), são recusados com
``ValueError`` e a indicação de como convertê-los com o ``migrate_reports.py``
do commit 5fe5092. Um nome do formato antigo num relatório em português
também é recusado: lido pela metade, sem aviso, o relatório faria a
verificação de hash ser pulada ou os pareceres do revisor serem ignorados.

As tabelas abaixo foram transcritas do glossário do projeto; o JSON do
glossário não é lido em tempo de execução. Este módulo não importa
``invoice_mapper`` nem ``apply_invoice_mapping``, para que qualquer um deles
possa importá-lo sem ciclo.
"""

from __future__ import annotations

import math
import re
import unicodedata
from typing import Iterable, Mapping, Sequence

import pandas as pd


# ---------------------------------------------------------------------------
# Versões do esquema
# ---------------------------------------------------------------------------

#: Valor de ``mapping_schema_version`` dos relatórios em português.
SCHEMA_VERSION_PT = 3
#: Versão do formato antigo, em inglês, que não é mais lido.
SCHEMA_VERSION_LEGACY = 2
#: Último commit com o ``migrate_reports.py``, que converte um relatório do
#: formato antigo para o esquema em português.
MIGRATION_COMMIT = "5fe5092"


# ---------------------------------------------------------------------------
# Abas
# ---------------------------------------------------------------------------

#: Tipo de aba -> nome em português.
#: As quatro primeiras são do relatório de mapeamento; as duas últimas, da
#: planilha mapeada (o entregável). A aba de mapeamentos precisa continuar
#: sendo a primeira do relatório, porque o ``apply`` a lê pelo índice 0.
SHEETS: dict[str, str] = {
    "mappings": "Mapeamentos",
    "run_metadata": "Metadados da Execução",
    "unmatched": "Colunas do Cliente sem Uso",
    "queue": "Fila de Revisão",
    "mapped_data": "Dados Mapeados",
    "coverage": "Cobertura",
}

#: Nomes das abas do relatório no formato antigo, em inglês. Não são lidos:
#: servem só para reconhecer um relatório antigo e recusá-lo.
LEGACY_SHEETS: dict[str, str] = {
    "mappings": "Mappings",
    "run_metadata": "Run Metadata",
    "unmatched": "Unmatched Client Columns",
    "queue": "Adjudication Queue",
}

#: Tabela -> tipo de aba onde ela fica. O banner é o bloco de linhas de
#: metadados no topo da aba Dados Mapeados.
TABLE_SHEETS: dict[str, str] = {
    "mappings": "mappings",
    "run_metadata": "run_metadata",
    "unmatched": "unmatched",
    "queue": "queue",
    "coverage": "coverage",
    "banner": "mapped_data",
    "mapped_data": "mapped_data",
}


# ---------------------------------------------------------------------------
# Cabeçalhos
# ---------------------------------------------------------------------------

#: Tabela -> {campo interno: cabeçalho em português}. O campo interno é também
#: o cabeçalho do formato antigo, em inglês, e é assim que um relatório antigo
#: é reconhecido. A ordem das chaves é a ordem das colunas gravadas.
COLUMNS: dict[str, dict[str, str]] = {
    # Aba Mapeamentos: campos de MappingResult seguidos de REVIEWER_COLUMNS.
    "mappings": {
        "model_column_number": "numero_coluna_modelo",
        "model_column": "coluna_modelo",
        "client_column_number": "numero_coluna_cliente",
        "client_column": "coluna_cliente",
        "name_confidence": "confianca_nome",
        "name_runner_up_confidence": "confianca_nome_segunda_melhor",
        "name_margin": "margem_nome",
        "data_confidence": "confianca_dados",
        "final_confidence": "confianca_final",
        "method": "metodo",
        "status": "situacao",
        "reason": "justificativa",
        "catalog_name": "nome_catalogo",
        "catalog_coverage": "cobertura_catalogo",
        "catalog_distinct_coverage": "cobertura_distinta_catalogo",
        "catalog_confidence": "confianca_catalogo",
        "arithmetic_proof": "prova_aritmetica",
        "supported_by": "apoiado_por",
        "decided_by": "decidido_por",
        "vetoed_by": "vetado_por",
        "llm_data_confidence": "confianca_dados_ia",
        "llm_final_confidence": "confianca_final_ia",
        "reviewer_status": "parecer_revisor",
        "correct_client_column": "coluna_cliente_correta",
        "reviewer_note": "observacao_revisor",
    },
    # Aba Metadados da Execução: pares chave/valor.
    "run_metadata": {
        "key": "chave",
        "value": "valor",
    },
    # Aba Colunas do Cliente sem Uso: campos de UnmatchedColumn.
    "unmatched": {
        "client_column_number": "numero_coluna_cliente",
        "client_column": "coluna_cliente",
        "disposition": "disposicao",
        "review_priority": "prioridade_revisao",
        "rows_with_value": "linhas_com_valor",
        "distinct_values": "valores_distintos",
        "signals": "sinais",
        "signal_detail": "detalhe_sinais",
        "closest_model_column": "coluna_modelo_mais_proxima",
        "closest_name_confidence": "confianca_nome_mais_proxima",
    },
    # Aba Fila de Revisão: campos de AdjudicationRow.
    "queue": {
        "rank": "posicao",
        "model_column_number": "numero_coluna_modelo",
        "model_column": "coluna_modelo",
        "proposed_client_column_number": "numero_coluna_cliente_proposta",
        "proposed_client_column": "coluna_cliente_proposta",
        "final_confidence": "confianca_final",
        "gate_distance": "distancia_limiar",
        "name_margin": "margem_nome",
        "rows_with_value": "linhas_com_valor",
        "resolver_disagrees": "divergencia_ia",
        "arithmetic_proof": "prova_aritmetica",
        "client_sample_values": "amostra_valores_cliente",
        "why_held": "motivo_retencao",
    },
    # Aba Cobertura da planilha mapeada.
    "coverage": {
        "state": "situacao_cobertura",
        "model_column_number": "numero_coluna_modelo",
        "model_column": "coluna_modelo",
        "excel_column": "coluna_excel",
        "client_column_number": "numero_coluna_cliente",
        "client_column": "coluna_cliente",
        "final_confidence": "confianca_final",
        "reason": "justificativa",
    },
    # Rótulos das 21 linhas do banner de Dados Mapeados (linhas 2 a 22 do
    # Excel), na ordem de MAPPING_METADATA. A chave é o campo de Mapeamentos
    # de onde sai o valor da linha.
    "banner": {
        "client_column_number": "numero_coluna_cliente",
        "client_column": "coluna_cliente",
        "status": "situacao",
        "method": "metodo",
        "name_confidence": "confianca_nome",
        "name_runner_up_confidence": "confianca_nome_segunda_melhor",
        "name_margin": "margem_nome",
        "data_confidence": "confianca_dados",
        "llm_data_confidence": "confianca_dados_ia",
        "llm_final_confidence": "confianca_final_ia",
        "catalog_name": "nome_catalogo",
        "catalog_coverage": "cobertura_catalogo",
        "catalog_confidence": "confianca_catalogo",
        "final_confidence": "confianca_final",
        "decided_by": "decidido_por",
        "supported_by": "apoiado_por",
        "vetoed_by": "vetado_por",
        "arithmetic_proof": "prova_aritmetica",
        "reason": "justificativa",
        "reviewer_status": "parecer_revisor",
        "reviewer_note": "observacao_revisor",
    },
    # Célula A1 da aba Dados Mapeados. As demais colunas dessa aba são os
    # cabeçalhos do model.xlsx, que já estão em português e nunca mudam.
    "mapped_data": {
        "__mapping_info__": "__info_mapeamento__",
    },
}

#: Coluna que o cabeçalho de cada tabela precisa ter.
_MARKERS: dict[str, str] = {
    "mappings": "numero_coluna_modelo",
    "run_metadata": "chave",
    "unmatched": "numero_coluna_cliente",
    "queue": "posicao",
    "coverage": "situacao_cobertura",
    "mapped_data": "__info_mapeamento__",
}


# ---------------------------------------------------------------------------
# Metadados da execução
# ---------------------------------------------------------------------------

#: Chave interna (igual à do formato antigo) -> chave em português.
METADATA_KEYS: dict[str, str] = {
    "mapping_schema_version": "versao_esquema_mapeamento",
    "generated_at_utc": "gerado_em_utc",
    "prompt_version": "versao_prompt",
    "client_file": "arquivo_cliente",
    "client_sha256": "sha256_cliente",
    "model_file": "arquivo_modelo",
    "model_sha256": "sha256_modelo",
    "client_sheet": "aba_cliente",
    "model_sheet": "aba_modelo",
    "client_header_row": "linha_cabecalho_cliente",
    "model_header_row": "linha_cabecalho_modelo",
    "embedding_model": "modelo_embedding",
    "llm_model": "modelo_ia",
    "name_threshold": "limiar_nome",
    # Em inglês a chave tem a mesma grafia do cabeçalho name_margin; em
    # português ela é distinta de propósito, porque aqui é o limiar
    # (--name-margin), não a margem medida em cada linha.
    "name_margin": "margem_minima_nome",
    "final_threshold": "limiar_final",
    "candidate_count": "quantidade_candidatos",
    "sample_size": "tamanho_amostra",
    "reference_manifest_sha256": "sha256_manifesto_referencia",
    # Só nos relatórios migrados do formato antigo, como os quatro
    # arquivados: registra de qual versão o arquivo veio, sem mexer em
    # gerado_em_utc.
    "migrated_from_schema_version": "migrado_da_versao_esquema",
}

#: Valores sentinela dos metadados: interno -> português. ``offline`` fica
#: igual (é o termo corrente em português e coincide com a flag --offline).
METADATA_SENTINELS: dict[str, str] = {
    "offline": "offline",
    "disabled": "desativado",
}

#: Chaves cujo valor pode ser um sentinela. Em qualquer outra chave o valor
#: passa intacto, mesmo que coincida com uma dessas palavras.
METADATA_SENTINEL_KEYS: frozenset[str] = frozenset(
    {"embedding_model", "llm_model", "reference_manifest_sha256"}
)


# ---------------------------------------------------------------------------
# Etapas da análise e travas
# ---------------------------------------------------------------------------

#: Etapas de evidência (ids de mapping_steps.EVIDENCE_STEPS) -> id em português.
STEP_IDS: dict[str, str] = {
    "resolver": "resolvedor_ia",
    "lexical": "lexico",
    "semantic": "semantico",
    "profile": "perfil",
    "identifier": "identificador",
    "catalog": "catalogo",
    "arithmetic": "aritmetica",
}

#: Travas (ids de mapping_steps.GUARDS) -> id em português.
GUARD_IDS: dict[str, str] = {
    "vacuity": "coluna_vazia",
    "family_conflict": "conflito_tributario",
    "underspecified": "cabecalho_generico",
    "exclusivity": "coluna_ja_consumida",
}

#: Valores que só existem nos relatórios arquivados, gravados pelo antigo
#: preenchimento retroativo (backfill_mapping_steps.py, removido depois do
#: commit 5fe5092): uma etapa objetiva rodou e o relatório não diz qual.
#: mapping_steps não classifica esse valor.
LEGACY_STEP_IDS: dict[str, str] = {
    "unrecorded": "nao_registrado",
}

#: Sufixo de evidência que concorda mas não decide, também só dos relatórios
#: arquivados: ``catalog (agrees)`` em memória, ``catalogo (concorda)`` no
#: arquivo.
AGREES_SUFFIX_EN = " (agrees)"
AGREES_SUFFIX_PT = " (concorda)"

#: Separador entre etapas numa célula.
STEPS_SEPARATOR = " + "
#: Separador entre sinais na aba de colunas sem uso. Neutro.
SIGNALS_SEPARATOR = ","

#: Todos os ids que podem aparecer numa célula de etapas: interno -> português.
ALL_STEP_IDS: dict[str, str] = {**STEP_IDS, **GUARD_IDS, **LEGACY_STEP_IDS}
_STEP_IDS_INTERNAL: dict[str, str] = {pt: en for en, pt in ALL_STEP_IDS.items()}


# ---------------------------------------------------------------------------
# Valores enumerados
# ---------------------------------------------------------------------------

#: Enum -> {valor interno: valor em português}.
#:
#: Os enums de máquina (``status``, ``method``, ``vetoed_by``, ``catalog_name``,
#: ``disposition``, ``review_priority``, ``signals``) usam snake_case ASCII e
#: têm volta: ao ler um relatório, o valor exato em português vira o interno.
#: ``reviewer_status`` é digitado por pessoas e tem regra própria (ver
#: ``normalize_reviewer_status``).
#:
#: ``why_held`` e ``coverage_state`` são frases fixas em português natural.
#: Na gravação o valor em inglês do formato antigo vira português; na leitura
#: nada volta, porque nenhum leitor depende deles e o texto em português já é
#: a forma canônica (as frases são escritas em português na origem).
#:
#: ``outcome`` são os rótulos de ``mapeval.outcomes.Outcome``: só aparecem
#: impressos pelo mapeval, nunca gravados em arquivo.
ENUMS: dict[str, dict[str, str]] = {
    "status": {
        "accepted": "aceito",
        "review": "revisao",
        "no_match": "sem_correspondencia",
    },
    "method": {
        "name": "nome",
        "data": "dados",
        "catalog": "catalogo",
        "name_only": "somente_nome",
        "data_error": "erro_dados",
    },
    "reviewer_status": {
        "": "",
        "correct": "correto",
        "incorrect": "incorreto",
        # Não é sem_correspondencia: aquilo é a situação do mapeador; isto é
        # o parecer humano de que o cliente não tem este campo.
        "no_mapping_exists": "inexistente",
    },
    # vetado_por guarda o id da trava que recusou a linha.
    "vetoed_by": dict(GUARD_IDS),
    # Ids de reference_data/manifest.json. Só mudam os que têm palavra em
    # inglês; os ids internos e os arquivos de reference_data não mudam.
    "catalog_name": {
        "cfop": "cfop",
        "ncm": "ncm",
        "ibge_municipality": "municipio_ibge",
        "ibs_cbs_classification": "cclasstrib",
        "ibs_cbs_cst": "cst_ibs_cbs",
        "cst_icms": "cst_icms",
        "cst_icms_taxation": "cst_icms_tributacao",
        "csosn": "csosn",
        "cst_ipi": "cst_ipi",
        "cst_pis_cofins": "cst_pis_cofins",
    },
    "disposition": {
        "proposed_not_accepted": "proposta_nao_aceita",
        "never_referenced": "nunca_referenciada",
    },
    "review_priority": {
        "shortlist": "prioritaria",
        "low": "baixa",
    },
    # Tokens separados por vírgula na coluna sinais. O mesmo id monta a chave
    # interna valid_{tipo}_fraction do perfil, por isso só muda na fronteira.
    "signals": {
        "arithmetic": "aritmetica",
        "catalog": "catalogo",
        "nfe_key_checkdigit": "dv_chave_nfe",
        "cnpj_checkdigit": "dv_cnpj",
        "cpf_checkdigit": "dv_cpf",
        "date": "data",
    },
    "why_held": {
        "below the accept threshold": "abaixo do limiar de aceite",
        "name evidence only - this branch cannot auto-accept however well the headers match": (
            "só há evidência de nome - este caminho não aceita automaticamente, "
            "por mais que os cabeçalhos coincidam"
        ),
        "the proposed column has no populated rows - confirming this would ship a blank column": (
            "a coluna proposta não tem linhas preenchidas - confirmá-la entregaria "
            "uma coluna em branco"
        ),
    },
    "coverage_state": {
        "delivered": "entregue",
        "awaiting decision": "aguardando decisão",
        "not in this client's export": "não consta na exportação do cliente",
    },
    "metadata_sentinel": dict(METADATA_SENTINELS),
    "outcome": {
        "correct": "correto",
        "correctly_absent": "ausente_com_acerto",
        "missed": "perdido",
        "wrong_column": "coluna_errada",
        "fabricated": "fabricado",
        "undetermined": "indeterminado",
    },
}

#: Enums que voltam ao valor interno na leitura de um relatório.
MACHINE_ENUMS: tuple[str, ...] = (
    "status",
    "method",
    "vetoed_by",
    "catalog_name",
    "disposition",
    "review_priority",
    "signals",
    "metadata_sentinel",
    "outcome",
)

_ENUMS_INTERNAL: dict[str, dict[str, str]] = {
    name: {pt: en for en, pt in values.items()} for name, values in ENUMS.items()
}


# ---------------------------------------------------------------------------
# Rótulos usados dentro de frases
# ---------------------------------------------------------------------------

#: Vocabulário para as frases escritas em português na origem. Os ids da
#: esquerda continuam internos (alguns são também entrada da IA, como os
#: papéis de ``header_semantic_role``); estes rótulos só aparecem em texto
#: para pessoas.
TEXT_LABELS: dict[str, dict[str, str]] = {
    # Papéis de header_semantic_role, citados no conflito de cabeçalho.
    "header_role": {
        "tax_classification": "classificação tributária",
        "tax_status": "situação tributária",
        "rate": "alíquota",
        "other": "outras",
        "exempt": "isentas",
        "reduced_base": "base reduzida",
        "base": "base de cálculo",
        "value": "valor",
        "tax_group": "grupo de imposto",
        "product_group": "grupo de produto",
        "tax_rule": "lei tributária",
        "text": "texto",
        "unit_of_measure": "unidade de medida",
        "unit_price": "preço unitário",
        "nfe_key": "chave de acesso da NF-e",
        "party_tax_id": "CNPJ/CPF do participante",
        "regional_tax_id": "inscrição estadual/municipal",
        "item_id": "número do item",
        "document_id": "número do documento",
    },
    # Qualificador no conflito de qualificador do tributo: no vocabulário
    # fiscal se fala em ICMS normal x ICMS ST.
    "tax_qualifier": {
        "st": "ST",
        "regular": "normal",
    },
    # Posição da coluna no trio fiscal (valor = base x alíquota).
    "trio_slot": {
        "base": "base de cálculo",
        "rate": "alíquota",
        "value": "valor",
    },
    # Tipo de identificador validado pelo dígito verificador.
    "identifier_kind": {
        "nfe_key": "chave de acesso da NF-e",
        "cnpj": "CNPJ",
        "cpf": "CPF",
    },
    # Nome legível de cada catálogo de reference_data.
    "catalog": {
        "cfop": "CFOP",
        "ncm": "NCM",
        "ibge_municipality": "código de município do IBGE",
        "ibs_cbs_classification": "cClassTrib do IBS/CBS",
        "ibs_cbs_cst": "CST do IBS/CBS",
        "cst_icms": "CST do ICMS",
        "cst_icms_taxation": "CST do ICMS (dígitos de tributação)",
        "csosn": "CSOSN",
        "cst_ipi": "CST do IPI",
        "cst_pis_cofins": "CST do PIS/COFINS",
    },
}

#: Atalho para o rótulo legível de cada catálogo (``cfop`` -> ``CFOP``).
CATALOG_LABELS: dict[str, str] = TEXT_LABELS["catalog"]


# ---------------------------------------------------------------------------
# Prova aritmética
# ---------------------------------------------------------------------------

#: Trecho que a prova traz quando o cliente tem mais de um bloco do mesmo
#: tributo e a equação não aponta uma coluna só. A frase da prova precisa
#: conter exatamente este trecho.
PROOF_NOT_DECISIVE_PT = "não distingue esta coluna"


def proof_is_decisive(text: object) -> bool:
    """Diz se uma prova aritmética aponta esta coluna, e não só o tributo.

    Falso quando não há prova (vazio, ``None`` ou valor que não é texto) ou
    quando ela traz o marcador de prova não decisiva. Uma prova assim
    confirma o escopo do tributo, mas vale para qualquer um dos blocos
    disjuntos do cliente; contá-la como evidência aritmética faria uma
    escolha ao acaso entre dois blocos passar por prova.
    """
    if not isinstance(text, str) or not text:
        return False
    return PROOF_NOT_DECISIVE_PT not in text


# ---------------------------------------------------------------------------
# Números dentro de frases
# ---------------------------------------------------------------------------


def format_decimal_br(value: float, places: int | None = None) -> str:
    """Formata um número com vírgula decimal, para uso dentro de frases.

    Com ``places=None`` os dígitos são os da representação padrão do Python
    (``0.9996`` vira ``"0,9996"``, ``5501`` vira ``"5501"``), que é o que as
    frases em inglês mostravam com ``{valor}``. Com ``places`` o número sai
    com essa quantidade fixa de casas (``0.12345, 2`` vira ``"0,12"``).

    Não há separador de milhar: os dígitos são os mesmos da versão em inglês,
    só o separador decimal muda. Células numéricas das planilhas continuam
    numéricas e não passam por aqui.
    """
    text = str(value) if places is None else f"{value:.{places}f}"
    return text.replace(".", ",")


def format_percent_br(value: float, places: int = 1) -> str:
    """Formata uma fração como porcentagem com vírgula decimal (``0.882`` -> ``"88,2%"``).

    Dá exatamente os dígitos de ``f"{value:.1%}"``, com vírgula no lugar do
    ponto, para que a frase em português diga o mesmo número da inglesa.
    """
    return f"{value:.{places}%}".replace(".", ",")


# ---------------------------------------------------------------------------
# Nome de exibição de coluna do cliente
# ---------------------------------------------------------------------------

#: Formas do nome de exibição. Em memória (``ColumnRef``) continua o inglês,
#: porque ``ColumnRef.name`` é entrada do embedding e da IA.
OCCURRENCE_SUFFIX_EN = " (occurrence {n})"
OCCURRENCE_SUFFIX_PT = " (ocorrência {n})"
UNNAMED_COLUMN_EN = "Unnamed column {n}"
UNNAMED_COLUMN_PT = "Coluna sem nome {n}"

_OCCURRENCE_EN = re.compile(r"(?P<name>.*) \(occurrence (?P<n>[0-9]+)\)", re.DOTALL)
# Na leitura aceita o que um revisor costuma digitar: com ou sem acento
# (inclusive o acento decomposto) e com qualquer caixa.
_OCCURRENCE_PT = re.compile(
    r"(?P<name>.*) \(ocorr(?:ê|ê|e)ncia (?P<n>[0-9]+)\)",
    re.DOTALL | re.IGNORECASE,
)
_UNNAMED_EN = re.compile(r"Unnamed column (?P<n>[0-9]+)")
_UNNAMED_PT = re.compile(r"coluna sem nome (?P<n>[0-9]+)", re.IGNORECASE)


def display_name_to_external(text: object) -> object:
    """Nome de exibição interno -> português, para gravar numa célula.

    ``"Valor ICMS (occurrence 2)"`` vira ``"Valor ICMS (ocorrência 2)"`` e
    ``"Unnamed column 7"`` vira ``"Coluna sem nome 7"``. O sufixo só é
    reconhecido ancorado no fim do texto. Qualquer outro texto passa intacto,
    então aplicar duas vezes dá o mesmo resultado.
    """
    if not isinstance(text, str):
        return text
    match = _OCCURRENCE_EN.fullmatch(text)
    base, occurrence = (match["name"], match["n"]) if match else (text, None)
    unnamed = _UNNAMED_EN.fullmatch(base)
    if unnamed:
        base = UNNAMED_COLUMN_PT.format(n=unnamed["n"])
    if occurrence is None:
        return base
    return base + OCCURRENCE_SUFFIX_PT.format(n=occurrence)


def display_name_to_internal(text: object) -> object:
    """Nome de exibição lido de uma célula -> forma interna em inglês.

    Reconhece ``" (ocorrência N)"`` e ``" (ocorrencia N)"`` (sem acento), em
    qualquer caixa, e também ``"Coluna sem nome N"``. Qualquer outro texto
    passa intacto.

    Ambiguidade conhecida: um cabeçalho de cliente que seja literalmente
    ``"Coluna sem nome 3"`` é lido como a coluna sem nome 3.
    """
    if not isinstance(text, str):
        return text
    match = _OCCURRENCE_PT.fullmatch(text)
    base, occurrence = (match["name"], match["n"]) if match else (text, None)
    unnamed = _UNNAMED_PT.fullmatch(base)
    if unnamed:
        base = UNNAMED_COLUMN_EN.format(n=unnamed["n"])
    if occurrence is None:
        return base
    return base + OCCURRENCE_SUFFIX_EN.format(n=occurrence)


# ---------------------------------------------------------------------------
# Células de etapas
# ---------------------------------------------------------------------------


def _step_token_to_external(token: str) -> str:
    base, agrees = token, False
    if token.endswith(AGREES_SUFFIX_EN):
        base, agrees = token[: -len(AGREES_SUFFIX_EN)], True
    translated = ALL_STEP_IDS.get(base)
    if translated is None:
        # Desconhecido ou já em português: fica como está.
        return token
    return translated + (AGREES_SUFFIX_PT if agrees else "")


def _step_token_to_internal(token: str) -> str:
    base, agrees = token, False
    if token.endswith(AGREES_SUFFIX_PT):
        base, agrees = token[: -len(AGREES_SUFFIX_PT)], True
    internal = _STEP_IDS_INTERNAL.get(base)
    if internal is None:
        # Desconhecido: fica como está.
        return token
    return internal + (AGREES_SUFFIX_EN if agrees else "")


def steps_to_external(cell: object) -> object:
    """Célula de etapas interna -> português.

    ``"lexical + catalog (agrees) + unrecorded"`` vira
    ``"lexico + catalogo (concorda) + nao_registrado"``. Cada etapa é
    traduzida separadamente; uma desconhecida passa intacta. Valores que não
    são texto, e o texto vazio, passam como estão.
    """
    if not isinstance(cell, str) or not cell:
        return cell
    return STEPS_SEPARATOR.join(
        _step_token_to_external(token) for token in cell.split(STEPS_SEPARATOR)
    )


def steps_to_internal(cell: object) -> object:
    """Célula de etapas lida -> ids internos.

    ``"lexico + catalogo (concorda)"`` vira ``"lexical + catalog (agrees)"``.
    Uma etapa desconhecida passa intacta.
    """
    if not isinstance(cell, str) or not cell:
        return cell
    return STEPS_SEPARATOR.join(
        _step_token_to_internal(token) for token in cell.split(STEPS_SEPARATOR)
    )


# ---------------------------------------------------------------------------
# Parecer do revisor
# ---------------------------------------------------------------------------

def _is_missing(value: object) -> bool:
    if value is None or value is pd.NA or value is pd.NaT:
        return True
    return isinstance(value, float) and math.isnan(value)


def _fold(text: str) -> str:
    """Tira acentos e caixa, para comparar o que uma pessoa digitou."""
    decomposed = unicodedata.normalize("NFKD", text)
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch)).casefold()


def normalize_reviewer_status(value: object) -> str | None:
    """Parecer digitado pelo revisor -> valor interno, ou ``None`` se não for um parecer.

    Aceita ``correto``, ``incorreto``, ``inexistente`` e vazio, ignorando
    espaços nas pontas, caixa e acentos. Célula vazia (``None`` ou NaN) vira
    ``""``. Qualquer outro valor devolve ``None``, inclusive os pareceres em
    inglês do formato antigo (``correct`` ...): como eles coincidem com os
    valores internos, só esta recusa explícita impede que passem calados.
    Quem lê recusa o parecer mostrando o que foi digitado.
    """
    if _is_missing(value):
        return ""
    if not isinstance(value, str):
        return None
    return _ENUMS_INTERNAL["reviewer_status"].get(_fold(value.strip()))


# ---------------------------------------------------------------------------
# Tratamento de cada campo
# ---------------------------------------------------------------------------

#: (tabela, campo interno) -> tratamento da célula.
#:
#: * ``enum:<nome>``: valor de ``ENUMS[<nome>]``; volta ao interno na leitura.
#:   ``reviewer_status`` é a exceção: na leitura passa como foi digitado, e
#:   quem lê o valida com ``normalize_reviewer_status``.
#: * ``phrase:<nome>``: frase fixa de ``ENUMS[<nome>]``; o inglês do formato
#:   antigo vira português na gravação e nada volta na leitura.
#: * ``steps``: ids de etapa separados por `` + ``.
#: * ``signals``: ids de sinal separados por vírgula.
#: * ``display``: nome de exibição de coluna do cliente.
#: * ``text``: frase livre, que passa intacta por esta camada (ela já é
#:   escrita em português na origem; a prosa da IA e as notas do revisor
#:   nunca são traduzidas).
#:
#: Campo fora desta tabela (números, hashes, cabeçalhos do modelo, valores
#: de amostra do cliente) passa intacto. O banner usa o tratamento do campo
#: de mesmo nome em ``mappings``.
FIELD_KIND: dict[tuple[str, str], str] = {
    ("mappings", "client_column"): "display",
    ("mappings", "status"): "enum:status",
    ("mappings", "method"): "enum:method",
    ("mappings", "reason"): "text",
    ("mappings", "catalog_name"): "enum:catalog_name",
    ("mappings", "arithmetic_proof"): "text",
    ("mappings", "supported_by"): "steps",
    ("mappings", "decided_by"): "steps",
    ("mappings", "vetoed_by"): "enum:vetoed_by",
    ("mappings", "reviewer_status"): "enum:reviewer_status",
    # Digitada pelo revisor: um número de coluna ou um nome de exibição.
    ("mappings", "correct_client_column"): "display",
    ("mappings", "reviewer_note"): "text",
    ("unmatched", "client_column"): "display",
    ("unmatched", "disposition"): "enum:disposition",
    ("unmatched", "review_priority"): "enum:review_priority",
    ("unmatched", "signals"): "signals",
    ("unmatched", "signal_detail"): "text",
    ("queue", "proposed_client_column"): "display",
    ("queue", "resolver_disagrees"): "text",
    ("queue", "arithmetic_proof"): "text",
    ("queue", "why_held"): "phrase:why_held",
    ("coverage", "state"): "phrase:coverage_state",
    ("coverage", "client_column"): "display",
    ("coverage", "reason"): "text",
}


def _field_kind(table: str, field: str) -> str | None:
    if table == "banner":
        table = "mappings"
    return FIELD_KIND.get((table, field))


def _signals_to_external(cell: object) -> object:
    if not isinstance(cell, str) or not cell:
        return cell
    values = ENUMS["signals"]
    return SIGNALS_SEPARATOR.join(
        values.get(token, token) for token in cell.split(SIGNALS_SEPARATOR)
    )


def _signals_to_internal(cell: object) -> object:
    if not isinstance(cell, str) or not cell:
        return cell
    values = _ENUMS_INTERNAL["signals"]
    return SIGNALS_SEPARATOR.join(
        values.get(token, token) for token in cell.split(SIGNALS_SEPARATOR)
    )


def external_value(table: str, field: str, value: object) -> object:
    """Valor interno de uma célula -> o que é gravado no esquema em português.

    ``field`` é o nome interno do campo. Valores desconhecidos, números e
    texto livre passam intactos, de modo que aplicar duas vezes dá o mesmo
    resultado.
    """
    kind = _field_kind(table, field)
    if kind is None or kind == "text":
        return value
    if kind == "display":
        return display_name_to_external(value)
    if kind == "steps":
        return steps_to_external(value)
    if kind == "signals":
        return _signals_to_external(value)
    enum_name = kind.split(":", 1)[1]
    if isinstance(value, str):
        return ENUMS[enum_name].get(value, value)
    return value


def internal_value(table: str, field: str, value: object) -> object:
    """Valor lido de uma célula -> vocabulário interno.

    * enums de máquina convertem só os valores **exatos** em português;
      valores desconhecidos passam como estão;
    * o parecer do revisor passa como foi digitado: quem lê o valida com
      ``normalize_reviewer_status``, que conhece a linha e pode citá-la;
    * nomes de exibição e etapas convertem a forma em português;
    * frases e texto livre não voltam para o inglês.
    """
    kind = _field_kind(table, field)
    if kind in (None, "text", "enum:reviewer_status") or kind.startswith("phrase:"):
        return value
    if kind == "display":
        return display_name_to_internal(value)
    if kind == "steps":
        return steps_to_internal(value)
    if kind == "signals":
        return _signals_to_internal(value)
    enum_name = kind.split(":", 1)[1]
    if isinstance(value, str):
        return _ENUMS_INTERNAL[enum_name].get(value, value)
    return value


# ---------------------------------------------------------------------------
# Nomes de aba e de cabeçalho
# ---------------------------------------------------------------------------


def sheet_name(kind: str) -> str:
    """Nome em português da aba de um tipo (``"mappings"``, ``"run_metadata"`` ...)."""
    return SHEETS[kind]


def header_label(table: str, field: str) -> str:
    """Cabeçalho em português de um campo interno, para gravar ou citar em mensagens.

    Um campo que não pertence à tabela volta como está.
    """
    return COLUMNS[table].get(field, field)


def _table_title(table: str) -> str:
    return SHEETS[TABLE_SHEETS[table]]


def _legacy_report_error(found: str) -> ValueError:
    """Erro para um relatório no formato antigo, em inglês, com o caminho da conversão."""
    return ValueError(
        f"O relatório está no formato antigo, em inglês (versão {SCHEMA_VERSION_LEGACY} do "
        f"esquema): {found}. Só o esquema em português (versão {SCHEMA_VERSION_PT}) é lido. "
        f"Para converter o relatório, use o migrate_reports.py do commit {MIGRATION_COMMIT}: "
        f"git worktree add /tmp/cbs-{MIGRATION_COMMIT} {MIGRATION_COMMIT} e, dentro dessa "
        "pasta, python migrate_reports.py RELATORIO EXPORTACAO, que reescreve o relatório "
        "no próprio arquivo."
    )


def _legacy_names_error(where: str, renames: Mapping[str, str]) -> ValueError:
    """Erro para nomes do formato antigo, em inglês, dentro de um relatório em português."""
    listing = ", ".join(f"{old!r} para {new!r}" for old, new in renames.items())
    return ValueError(
        f"{where} tem nomes do formato antigo, em inglês, num relatório em português. "
        f"Renomeie {listing}."
    )


def check_header(header: Iterable[object], table: str = "mappings") -> None:
    """Confere que o cabeçalho de uma tabela está no esquema em português.

    Levanta ``ValueError`` quando falta a coluna que toda tabela desse tipo
    tem (``numero_coluna_modelo`` na aba de mapeamentos) ou quando há um
    cabeçalho do formato antigo, em inglês. A aba de mapeamentos é a
    referência: com cabeçalhos em inglês e sem ``numero_coluna_modelo``, o
    relatório inteiro está no formato antigo, e o erro diz como convertê-lo.
    Fora disso, um cabeçalho em inglês é um nome a renomear: um relatório
    meio traduzido não é lido pela metade.
    """
    names = [str(name) for name in header if name is not None]
    columns = COLUMNS[table]
    marker = _MARKERS[table]
    legacy = {name: columns[name] for name in names if name in columns}
    if legacy and table == "mappings" and marker not in names:
        raise _legacy_report_error(f"a aba de mapeamentos tem a coluna {next(iter(legacy))!r}")
    if legacy:
        raise _legacy_names_error(f"A aba {_table_title(table)}", legacy)
    if marker not in names:
        raise ValueError(
            f"A aba {_table_title(table)} não tem a coluna {marker!r}; confira se o arquivo "
            "é um relatório de mapeamento."
        )


def _sheet_key(name: str) -> str:
    """Forma de comparação de um nome de aba.

    O Excel trata nomes de aba sem distinguir maiúsculas de minúsculas, e um
    nome com acento pode chegar decomposto (``ç`` como ``c`` + cedilha) ou
    redigitado sem acento. Sem esta comparação, ``"metadados da execucao"``
    não seria reconhecida, ``find_sheet`` devolveria ``None`` e a verificação
    de hash seria pulada em silêncio.
    """
    return _fold(name.strip())


def find_sheet(sheet_names: Sequence[str], kind: str) -> str | None:
    """Nome da aba de um tipo numa pasta de trabalho, como está escrito nela.

    Devolve o nome para ser usado na leitura, ou ``None`` se a aba não
    existe (por exemplo, relatórios antigos sem metadados). A comparação
    ignora caixa, acentos e espaços nas pontas (ver ``_sheet_key``).

    Levanta ``ValueError`` se há duas abas com o mesmo nome a menos de caixa
    e acentos, ou uma aba com o nome do formato antigo, em inglês: sem isso,
    uma delas seria ignorada em silêncio e, no caso dos metadados, a
    verificação de hash seria pulada. Com a aba ``Mappings`` e sem a aba de
    mapeamentos em português, o relatório inteiro está no formato antigo, e
    o erro diz como convertê-lo.
    """

    def named(name: str) -> list[str]:
        wanted = _sheet_key(name)
        return [n for n in sheet_names if isinstance(n, str) and _sheet_key(n) == wanted]

    found = named(SHEETS[kind])
    legacy = named(LEGACY_SHEETS[kind]) if kind in LEGACY_SHEETS else []
    if legacy and kind == "mappings" and not found:
        raise _legacy_report_error(f"a pasta de trabalho tem a aba {legacy[0]!r}")
    if legacy:
        raise _legacy_names_error("A pasta de trabalho", {legacy[0]: SHEETS[kind]})
    if len(found) > 1:
        raise ValueError(
            f"A pasta de trabalho tem mais de uma aba {SHEETS[kind]!r}: {found}. Mantenha só uma."
        )
    return found[0] if found else None


# ---------------------------------------------------------------------------
# Quadros (pandas) e linhas de cabeçalho (openpyxl)
# ---------------------------------------------------------------------------


def _holds_text(series: pd.Series) -> bool:
    """Se a coluna pode conter texto; colunas numéricas, booleanas e de data não podem."""
    return not (
        pd.api.types.is_numeric_dtype(series)
        or pd.api.types.is_bool_dtype(series)
        or pd.api.types.is_datetime64_any_dtype(series)
    )


def _convert_columns(frame: pd.DataFrame, table: str, convert) -> None:
    """Converte, no próprio ``frame``, as células das colunas que têm tratamento.

    ``convert(campo, valor)`` recebe o nome interno do campo. Só passam por
    aqui as colunas de ``FIELD_KIND`` que podem conter texto; as demais ficam
    intactas.

    Não usa ``Series.map``, que reinfere o dtype do resultado: a coluna object
    ``correct_client_column`` de sap_procosa, com ``[NaN, 6, 32]``, voltava
    float64 com ``6.0`` e ``32.0``, e ``None`` virava ``NaN``. Aqui cada célula
    fica exatamente como ``convert`` a devolveu, numa coluna object. As colunas
    são percorridas por posição, o que também funciona com nomes repetidos.
    """
    for position, column in enumerate(frame.columns):
        if not isinstance(column, str) or _field_kind(table, column) is None:
            continue
        series = frame.iloc[:, position]
        if not _holds_text(series):
            continue
        frame.isetitem(
            position,
            pd.Series(
                [convert(column, value) for value in series.tolist()],
                index=series.index,
                dtype=object,
            ),
        )


def to_external_frame(frame: pd.DataFrame, table: str) -> pd.DataFrame:
    """Quadro com nomes e valores internos -> esquema em português, para gravar.

    Renomeia os cabeçalhos com ``COLUMNS[table]``, converte cada célula
    conforme ``FIELD_KIND`` e mantém a ordem das colunas. Colunas que não são
    da tabela ficam com o nome e os valores que têm. O quadro recebido não é
    alterado.
    """
    out = frame.copy()
    _convert_columns(out, table, lambda field, value: external_value(table, field, value))
    names = COLUMNS[table]
    return out.rename(
        columns=lambda name: names.get(name, name) if isinstance(name, str) else name
    )


def to_internal_frame(frame: pd.DataFrame, table: str) -> pd.DataFrame:
    """Quadro lido de um arquivo -> nomes e valores internos.

    Confere o cabeçalho com ``check_header``, que recusa com ``ValueError``
    um relatório no formato antigo ou com nomes dele. O quadro recebido não
    é alterado.
    """
    check_header(frame.columns, table)
    internal = {pt: en for en, pt in COLUMNS[table].items()}
    out = frame.copy().rename(
        columns=lambda name: internal.get(name, name) if isinstance(name, str) else name
    )
    _convert_columns(out, table, lambda field, value: internal_value(table, field, value))
    return out


def internal_header_index(header_row: Sequence[object], table: str) -> dict[str, int]:
    """Linha de cabeçalho do openpyxl -> {nome interno: índice da coluna}.

    Para leitores que percorrem as linhas com ``iter_rows(values_only=True)``.
    Confere o cabeçalho como ``to_internal_frame``. Cabeçalhos que não são da
    tabela entram com o próprio nome; células de cabeçalho vazias ficam de
    fora.
    """
    check_header(header_row, table)
    internal = {pt: en for en, pt in COLUMNS[table].items()}
    index: dict[str, int] = {}
    for position, name in enumerate(header_row):
        if name is None:
            continue
        key = internal.get(name, name) if isinstance(name, str) else name
        index[key] = position
    return index


# ---------------------------------------------------------------------------
# Metadados da execução
# ---------------------------------------------------------------------------

_METADATA_KEYS_INTERNAL: dict[str, str] = {pt: en for en, pt in METADATA_KEYS.items()}
_METADATA_SENTINELS_INTERNAL: dict[str, str] = {
    pt: en for en, pt in METADATA_SENTINELS.items()
}


def to_external_metadata(metadata: Mapping[str, object]) -> pd.DataFrame:
    """Dicionário de metadados com chaves internas -> quadro ``chave``/``valor`` em português.

    Traduz as chaves e os sentinelas (``disabled`` -> ``desativado``) das
    chaves que podem tê-los. Os demais valores (hashes, nomes de arquivo,
    limiares, data de geração) passam intactos, na mesma ordem.
    """
    rows = []
    for key, value in metadata.items():
        if key in METADATA_SENTINEL_KEYS and isinstance(value, str):
            value = METADATA_SENTINELS.get(value, value)
        rows.append((METADATA_KEYS.get(key, key), value))
    columns = COLUMNS["run_metadata"]
    return pd.DataFrame(rows, columns=[columns["key"], columns["value"]])


def _clean_text(value: object) -> str:
    return "" if _is_missing(value) else str(value).strip()


def internal_metadata(frame: pd.DataFrame) -> dict[str, str]:
    """Aba de metadados lida -> dicionário {chave interna: valor como texto}.

    O cabeçalho precisa ser ``chave``/``valor`` (ver ``check_header``). As
    chaves e os sentinelas em português viram internos, para que nenhuma
    verificação de hash seja pulada por não reconhecer a chave. Valores são
    limpos como no ``apply``: texto sem espaços nas pontas, célula vazia como
    ``""``. Levanta ``ValueError`` para chave repetida na coluna crua (a mesma
    regra do ``apply``) e para chave do formato antigo, em inglês: igual à
    interna, ela valeria no lugar da chave em português sem aviso.
    """
    check_header(frame.columns, "run_metadata")
    title = sheet_name("run_metadata")
    key_column = header_label("run_metadata", "key")
    value_column = header_label("run_metadata", "value")
    if value_column not in frame.columns:
        raise ValueError(
            f"A aba {title!r} precisa ter as colunas {key_column!r} e {value_column!r}."
        )
    # Mesma regra que o apply sempre aplicou à coluna crua: qualquer repetição,
    # inclusive duas chaves em branco, recusa a aba.
    raw_repeated = frame[key_column][frame[key_column].duplicated()]
    if not raw_repeated.empty:
        labels = sorted({_clean_text(key) for key in raw_repeated})
        raise ValueError(f"A aba {title!r} tem chaves repetidas: {labels}.")
    written = [_clean_text(key) for key in frame[key_column]]
    legacy = {key: METADATA_KEYS[key] for key in written if key in METADATA_KEYS}
    if legacy:
        raise _legacy_names_error(f"A aba {title!r}", legacy)
    metadata: dict[str, str] = {}
    for key, raw_value in zip(written, frame[value_column]):
        if not key:
            continue
        key = _METADATA_KEYS_INTERNAL.get(key, key)
        value = _clean_text(raw_value)
        if key in METADATA_SENTINEL_KEYS:
            value = _METADATA_SENTINELS_INTERNAL.get(value, value)
        metadata[key] = value
    return metadata
