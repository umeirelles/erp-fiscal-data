"""Esquema em português dos relatórios de mapeamento e das planilhas mapeadas.

Fronteira entre o vocabulário interno, em inglês, e o que aparece nos
arquivos. Em memória o código usa ``accepted``, ids de etapa como
``semantic``, os ids de ``reference_data/`` e ``Nome (occurrence 2)``; esses
valores viram português só aqui, na gravação, e voltam ao interno na leitura.

As frases para pessoas já são escritas em português onde são montadas; daqui
elas tiram só o vocabulário (``TEXT_LABELS``), o marcador da prova não
decisiva e a vírgula decimal. A entrada da IA nunca passa por aqui, porque
precisa ficar idêntica byte a byte: o prompt, o prefixo do embedding, o
esquema ``DataMappingDecision``, o payload, o texto de
``semantic_header_conflict`` e ``ColumnRef.name`` / ``display_name``.

Só o esquema em português (versão 3) é lido. Um relatório do formato antigo,
em inglês (versão 2), é recusado com o caminho da conversão. Um nome antigo
num relatório em português também é recusado: lido pela metade, o relatório
faria a verificação de hash ser pulada ou os pareceres do revisor serem
ignorados sem aviso.

Não importa ``invoice_mapper`` nem ``apply_invoice_mapping``, para que os dois
possam importá-lo sem ciclo.
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

SCHEMA_VERSION_PT = 3
#: Formato antigo, em inglês, que não é mais lido.
SCHEMA_VERSION_LEGACY = 2
#: Último commit com o ``migrate_reports.py``, que converte o formato antigo.
MIGRATION_COMMIT = "5fe5092"


# ---------------------------------------------------------------------------
# Abas
# ---------------------------------------------------------------------------

#: Tipo de aba -> nome. As quatro primeiras são do relatório; as duas
#: últimas, da planilha mapeada. Mapeamentos precisa continuar sendo a
#: primeira aba do relatório, porque o ``apply`` a lê pela posição.
SHEETS: dict[str, str] = {
    "mappings": "Mapeamentos",
    "run_metadata": "Metadados da Execução",
    "unmatched": "Colunas do Cliente sem Uso",
    "queue": "Fila de Revisão",
    "mapped_data": "Dados Mapeados",
    "coverage": "Cobertura",
}

#: Nomes de aba do formato antigo: só para reconhecê-los e recusá-los.
LEGACY_SHEETS: dict[str, str] = {
    "mappings": "Mappings",
    "run_metadata": "Run Metadata",
    "unmatched": "Unmatched Client Columns",
    "queue": "Adjudication Queue",
}


# ---------------------------------------------------------------------------
# Cabeçalhos
# ---------------------------------------------------------------------------

#: Tabela -> {campo interno: cabeçalho}, na ordem das colunas gravadas. O
#: campo interno é também o cabeçalho do formato antigo; é por ele que um
#: relatório antigo é reconhecido.
COLUMNS: dict[str, dict[str, str]] = {
    # Campos de MappingResult seguidos de REVIEWER_COLUMNS.
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
    "run_metadata": {
        "key": "chave",
        "value": "valor",
    },
    # Campos de UnmatchedColumn.
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
    # Campos de AdjudicationRow.
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
    # de onde sai a linha.
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
    # Célula A1 de Dados Mapeados; as outras colunas são os cabeçalhos do
    # model.xlsx, que já estão em português.
    "mapped_data": {
        "__mapping_info__": "__info_mapeamento__",
    },
}

#: Coluna que o cabeçalho de cada tabela precisa ter.
_REQUIRED_HEADER: dict[str, str] = {
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

#: Chave interna (igual à do formato antigo) -> chave gravada.
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
    # Distinta de margem_nome de propósito: aqui é o limiar --name-margin, não
    # a margem medida em cada linha.
    "name_margin": "margem_minima_nome",
    "final_threshold": "limiar_final",
    "candidate_count": "quantidade_candidatos",
    "sample_size": "tamanho_amostra",
    "reference_manifest_sha256": "sha256_manifesto_referencia",
    # Só nos relatórios migrados do formato antigo, como os quatro arquivados.
    "migrated_from_schema_version": "migrado_da_versao_esquema",
}

#: Sentinelas: interno -> gravado. ``offline`` fica igual, como a flag --offline.
METADATA_SENTINELS: dict[str, str] = {
    "offline": "offline",
    "disabled": "desativado",
}

#: Só estas chaves têm sentinela; em outra chave o valor passa intacto.
METADATA_SENTINEL_KEYS: frozenset[str] = frozenset(
    {"embedding_model", "llm_model", "reference_manifest_sha256"}
)


# ---------------------------------------------------------------------------
# Etapas da análise e travas
# ---------------------------------------------------------------------------

#: Etapas de evidência (ids de mapping_steps.EVIDENCE_STEPS) -> id gravado.
STEP_IDS: dict[str, str] = {
    "resolver": "resolvedor_ia",
    "lexical": "lexico",
    "semantic": "semantico",
    "profile": "perfil",
    "identifier": "identificador",
    "catalog": "catalogo",
    "arithmetic": "aritmetica",
}

#: Travas (ids de mapping_steps.GUARDS) -> id gravado.
GUARD_IDS: dict[str, str] = {
    "vacuity": "coluna_vazia",
    "family_conflict": "conflito_tributario",
    "underspecified": "cabecalho_generico",
    "exclusivity": "coluna_ja_consumida",
}

#: Só nos relatórios arquivados, gravado pelo antigo preenchimento retroativo:
#: uma etapa objetiva rodou e o relatório não diz qual. mapping_steps não o
#: classifica.
LEGACY_STEP_IDS: dict[str, str] = {
    "unrecorded": "nao_registrado",
}

#: Todo id que pode aparecer numa célula de etapas.
ALL_STEP_IDS: dict[str, str] = {**STEP_IDS, **GUARD_IDS, **LEGACY_STEP_IDS}

#: Etapa que concorda mas não decide, também só dos relatórios arquivados:
#: ``catalog (agrees)`` em memória, ``catalogo (concorda)`` no arquivo.
AGREES_SUFFIX_EN = " (agrees)"
AGREES_SUFFIX_PT = " (concorda)"

STEPS_SEPARATOR = " + "
SIGNALS_SEPARATOR = ","


# ---------------------------------------------------------------------------
# Valores enumerados
# ---------------------------------------------------------------------------

#: Enum -> {valor interno: valor gravado}. Como cada um é lido está em
#: ``FIELD_KIND``.
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
    # Digitado por pessoas: a leitura é a de normalize_reviewer_status.
    "reviewer_status": {
        "": "",
        "correct": "correto",
        "incorrect": "incorreto",
        # Não é sem_correspondencia, que é a situação do mapeador: é o parecer
        # humano de que o cliente não tem este campo.
        "no_mapping_exists": "inexistente",
    },
    # Id da trava que recusou a linha.
    "vetoed_by": GUARD_IDS,
    # Ids de reference_data/manifest.json; os arquivos de lá não mudam.
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
    # Tokens da coluna sinais. O id interno também monta a chave
    # valid_{tipo}_fraction do perfil, por isso só muda na fronteira.
    "signals": {
        "arithmetic": "aritmetica",
        "catalog": "catalogo",
        "nfe_key_checkdigit": "dv_chave_nfe",
        "cnpj_checkdigit": "dv_cnpj",
        "cpf_checkdigit": "dv_cpf",
        "date": "data",
    },
    # Rótulos de mapeval.outcomes.Outcome: só impressos, nunca gravados.
    "outcome": {
        "correct": "correto",
        "missed": "perdido",
        "wrong_column": "coluna_errada",
        "fabricated": "fabricado",
        "undetermined": "indeterminado",
    },
}


# ---------------------------------------------------------------------------
# Rótulos usados dentro de frases
# ---------------------------------------------------------------------------

#: Vocabulário das frases escritas em português na origem. Os ids continuam
#: internos (os papéis de ``header_semantic_role`` são também entrada da IA);
#: os rótulos só aparecem em texto para pessoas.
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
    # Qualificador do tributo: no vocabulário fiscal, ICMS normal x ICMS ST.
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
    # Identificador validado pelo dígito verificador.
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

CATALOG_LABELS: dict[str, str] = TEXT_LABELS["catalog"]


# ---------------------------------------------------------------------------
# Tratamento de cada campo
# ---------------------------------------------------------------------------

#: Tabela -> {campo interno: tratamento da célula}:
#:
#: * ``enum:<nome>``: valor de ``ENUMS[<nome>]``, que volta ao interno na
#:   leitura. O parecer do revisor é a exceção: passa como foi digitado, e
#:   quem lê o valida com ``normalize_reviewer_status``.
#: * ``steps``: ids de etapa separados por `` + ``.
#: * ``signals``: ids de sinal separados por vírgula.
#: * ``display``: nome de exibição de coluna do cliente.
#: * ``text``: texto livre, já na língua certa (a prosa da IA e as notas do
#:   revisor nunca são traduzidas).
#:
#: Campo fora desta tabela (números, hashes, cabeçalhos do modelo, amostras
#: do cliente) passa intacto. O banner segue o tratamento de ``mappings``.
FIELD_KIND: dict[str, dict[str, str]] = {
    "mappings": {
        "client_column": "display",
        "status": "enum:status",
        "method": "enum:method",
        "reason": "text",
        "catalog_name": "enum:catalog_name",
        "arithmetic_proof": "text",
        "supported_by": "steps",
        "decided_by": "steps",
        "vetoed_by": "enum:vetoed_by",
        "reviewer_status": "enum:reviewer_status",
        # Digitada pelo revisor: um número de coluna ou um nome de exibição.
        "correct_client_column": "display",
        "reviewer_note": "text",
    },
    "unmatched": {
        "client_column": "display",
        "disposition": "enum:disposition",
        "review_priority": "enum:review_priority",
        "signals": "signals",
        "signal_detail": "text",
    },
    "queue": {
        "proposed_client_column": "display",
        "resolver_disagrees": "text",
        "arithmetic_proof": "text",
        "why_held": "text",
    },
    "coverage": {
        "state": "text",
        "client_column": "display",
        "reason": "text",
    },
}


def _field_kind(table: str, field: str) -> str | None:
    if table == "banner":
        table = "mappings"
    return FIELD_KIND.get(table, {}).get(field)


def _reverse(mapping: Mapping[str, str]) -> dict[str, str]:
    """Tabela interno -> gravado invertida, para a leitura."""
    return {written: internal for internal, written in mapping.items()}


# ---------------------------------------------------------------------------
# Prova aritmética e números dentro de frases
# ---------------------------------------------------------------------------

#: Trecho que a prova traz quando o cliente tem mais de um bloco do mesmo
#: tributo e a equação não aponta uma coluna só.
PROOF_NOT_DECISIVE_PT = "não distingue esta coluna"


def proof_is_decisive(text: object) -> bool:
    """Diz se uma prova aritmética aponta esta coluna, e não só o tributo.

    Falso sem prova ou com o marcador ``PROOF_NOT_DECISIVE_PT``: uma prova
    assim vale para qualquer um dos blocos disjuntos do cliente, e contá-la
    como evidência faria uma escolha ao acaso passar por prova.
    """
    if not isinstance(text, str) or not text:
        return False
    return PROOF_NOT_DECISIVE_PT not in text


def format_decimal_br(value: float, places: int | None = None) -> str:
    """Número com vírgula decimal, para frases (``0.9996`` -> ``"0,9996"``).

    Sem ``places``, os dígitos são os de ``str(value)``; com ``places``, essa
    quantidade fixa de casas. Não há separador de milhar.
    """
    text = str(value) if places is None else f"{value:.{places}f}"
    return text.replace(".", ",")


def format_percent_br(value: float, places: int = 1) -> str:
    """Fração como porcentagem com vírgula decimal (``0.882`` -> ``"88,2%"``)."""
    return f"{value:.{places}%}".replace(".", ",")


# ---------------------------------------------------------------------------
# Nome de exibição de coluna do cliente
# ---------------------------------------------------------------------------

#: Em memória (``ColumnRef``) o nome continua em inglês, porque
#: ``ColumnRef.name`` é entrada do embedding e da IA.
OCCURRENCE_SUFFIX_EN = " (occurrence {n})"
OCCURRENCE_SUFFIX_PT = " (ocorrência {n})"
UNNAMED_COLUMN_EN = "Unnamed column {n}"
UNNAMED_COLUMN_PT = "Coluna sem nome {n}"

_OCCURRENCE_EN = re.compile(r"(?P<name>.*) \(occurrence (?P<n>[0-9]+)\)", re.DOTALL)
_UNNAMED_EN = re.compile(r"Unnamed column (?P<n>[0-9]+)")
# Na leitura vale o que um revisor costuma digitar: qualquer caixa, com ou
# sem acento, inclusive o acento decomposto.
_OCCURRENCE_PT = re.compile(
    r"(?P<name>.*) \(ocorr(?:ê|e\u0302|e)ncia (?P<n>[0-9]+)\)",
    re.DOTALL | re.IGNORECASE,
)
_UNNAMED_PT = re.compile(r"coluna sem nome (?P<n>[0-9]+)", re.IGNORECASE)


def display_name_to_external(text: object) -> object:
    """Nome de exibição interno -> o que vai na célula.

    ``"Valor (occurrence 2)"`` vira ``"Valor (ocorrência 2)"`` e ``"Unnamed
    column 7"`` vira ``"Coluna sem nome 7"``. O sufixo só vale ancorado no
    fim. Qualquer outro texto passa intacto, então aplicar duas vezes dá o
    mesmo resultado.
    """
    if not isinstance(text, str):
        return text
    occurrence = _OCCURRENCE_EN.fullmatch(text)
    name = occurrence["name"] if occurrence else text
    unnamed = _UNNAMED_EN.fullmatch(name)
    if unnamed:
        name = UNNAMED_COLUMN_PT.format(n=unnamed["n"])
    if occurrence:
        name += OCCURRENCE_SUFFIX_PT.format(n=occurrence["n"])
    return name


def display_name_to_internal(text: object) -> object:
    """Nome lido de uma célula -> forma interna; qualquer outro texto passa intacto.

    Ambiguidade conhecida: um cabeçalho de cliente que seja literalmente
    ``"Coluna sem nome 3"`` é lido como a coluna sem nome 3.
    """
    if not isinstance(text, str):
        return text
    occurrence = _OCCURRENCE_PT.fullmatch(text)
    name = occurrence["name"] if occurrence else text
    unnamed = _UNNAMED_PT.fullmatch(name)
    if unnamed:
        name = UNNAMED_COLUMN_EN.format(n=unnamed["n"])
    if occurrence:
        name += OCCURRENCE_SUFFIX_EN.format(n=occurrence["n"])
    return name


# ---------------------------------------------------------------------------
# Células de etapas e de sinais
# ---------------------------------------------------------------------------


def _translate_steps(
    cell: object, ids: Mapping[str, str], agrees_from: str, agrees_to: str
) -> object:
    """Traduz cada etapa da célula; uma etapa desconhecida passa intacta."""
    if not isinstance(cell, str) or not cell:
        return cell
    steps = []
    for step in cell.split(STEPS_SEPARATOR):
        agrees = step.endswith(agrees_from)
        base = step[: -len(agrees_from)] if agrees else step
        if base in ids:
            step = ids[base] + (agrees_to if agrees else "")
        steps.append(step)
    return STEPS_SEPARATOR.join(steps)


def steps_to_external(cell: object) -> object:
    """``"lexical + catalog (agrees)"`` -> ``"lexico + catalogo (concorda)"``."""
    return _translate_steps(cell, ALL_STEP_IDS, AGREES_SUFFIX_EN, AGREES_SUFFIX_PT)


def steps_to_internal(cell: object) -> object:
    """``"lexico + catalogo (concorda)"`` -> ``"lexical + catalog (agrees)"``."""
    return _translate_steps(cell, _reverse(ALL_STEP_IDS), AGREES_SUFFIX_PT, AGREES_SUFFIX_EN)


def _translate_signals(cell: object, ids: Mapping[str, str]) -> object:
    if not isinstance(cell, str) or not cell:
        return cell
    tokens = cell.split(SIGNALS_SEPARATOR)
    return SIGNALS_SEPARATOR.join(ids.get(token, token) for token in tokens)


# ---------------------------------------------------------------------------
# Valor de uma célula
# ---------------------------------------------------------------------------


def external_value(table: str, field: str, value: object) -> object:
    """Valor interno de uma célula -> valor gravado.

    ``field`` é o nome interno. Valores desconhecidos, números e texto livre
    passam intactos, então aplicar duas vezes dá o mesmo resultado.
    """
    kind = _field_kind(table, field)
    if kind == "display":
        return display_name_to_external(value)
    if kind == "steps":
        return steps_to_external(value)
    if kind == "signals":
        return _translate_signals(value, ENUMS["signals"])
    if kind and kind.startswith("enum:") and isinstance(value, str):
        return ENUMS[kind.split(":", 1)[1]].get(value, value)
    return value


def internal_value(table: str, field: str, value: object) -> object:
    """Valor lido de uma célula -> vocabulário interno.

    Um enum só volta quando o valor gravado é exato. O parecer do revisor
    passa como foi digitado, para que quem lê o valide com
    ``normalize_reviewer_status`` citando a linha. Texto livre passa intacto.
    """
    kind = _field_kind(table, field)
    if kind == "display":
        return display_name_to_internal(value)
    if kind == "steps":
        return steps_to_internal(value)
    if kind == "signals":
        return _translate_signals(value, _reverse(ENUMS["signals"]))
    reversible = kind and kind.startswith("enum:") and kind != "enum:reviewer_status"
    if reversible and isinstance(value, str):
        return _reverse(ENUMS[kind.split(":", 1)[1]]).get(value, value)
    return value


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
    """Parecer digitado -> valor interno, ou ``None`` se não for um parecer.

    Aceita ``correto``, ``incorreto``, ``inexistente`` e vazio, ignorando
    espaços nas pontas, caixa e acentos; célula vazia vira ``""``. Os
    pareceres em inglês do formato antigo (``correct`` ...) coincidem com os
    valores internos, e só esta recusa explícita impede que passem calados.
    """
    if _is_missing(value):
        return ""
    if not isinstance(value, str):
        return None
    return _reverse(ENUMS["reviewer_status"]).get(_fold(value.strip()))


# ---------------------------------------------------------------------------
# Nomes de aba e de cabeçalho
# ---------------------------------------------------------------------------


def sheet_name(kind: str) -> str:
    """Nome da aba de um tipo (``"mappings"``, ``"run_metadata"`` ...)."""
    return SHEETS[kind]


def header_label(table: str, field: str) -> str:
    """Cabeçalho gravado de um campo interno; um campo fora da tabela volta como está."""
    return COLUMNS[table].get(field, field)


def _legacy_report_error(found: str) -> ValueError:
    return ValueError(
        f"O relatório está no formato antigo, em inglês (versão {SCHEMA_VERSION_LEGACY} do "
        f"esquema): {found}. Só o esquema em português (versão {SCHEMA_VERSION_PT}) é lido. "
        f"Para converter o relatório, use o migrate_reports.py do commit {MIGRATION_COMMIT}: "
        f"git worktree add /tmp/cbs-{MIGRATION_COMMIT} {MIGRATION_COMMIT} e, dentro dessa "
        "pasta, python migrate_reports.py RELATORIO EXPORTACAO, que reescreve o relatório "
        "no próprio arquivo."
    )


def _legacy_names_error(where: str, renames: Mapping[str, str]) -> ValueError:
    listing = ", ".join(f"{old!r} para {new!r}" for old, new in renames.items())
    return ValueError(
        f"{where} tem nomes do formato antigo, em inglês, num relatório em português. "
        f"Renomeie {listing}."
    )


def check_header(header: Iterable[object], table: str = "mappings") -> None:
    """Confere que o cabeçalho de uma tabela está no esquema em português.

    Levanta ``ValueError`` quando falta a coluna obrigatória da tabela ou há
    um cabeçalho do formato antigo. Na aba de mapeamentos, nomes antigos sem
    ``numero_coluna_modelo`` são um relatório inteiro no formato antigo, e o
    erro diz como convertê-lo; fora disso, são nomes a renomear.
    """
    names = [str(name) for name in header if name is not None]
    columns = COLUMNS[table]
    required = _REQUIRED_HEADER[table]
    legacy = {name: columns[name] for name in names if name in columns}
    if legacy and table == "mappings" and required not in names:
        raise _legacy_report_error(f"a aba de mapeamentos tem a coluna {next(iter(legacy))!r}")
    if legacy:
        raise _legacy_names_error(f"A aba {SHEETS[table]}", legacy)
    if required not in names:
        raise ValueError(
            f"A aba {SHEETS[table]} não tem a coluna {required!r}; confira se o arquivo "
            "é um relatório de mapeamento."
        )


def _sheets_named(sheet_names: Sequence[str], name: str) -> list[str]:
    """Abas com este nome, sem distinguir caixa, acentos e espaços nas pontas."""
    wanted = _fold(name.strip())
    return [n for n in sheet_names if isinstance(n, str) and _fold(n.strip()) == wanted]


def find_sheet(sheet_names: Sequence[str], kind: str) -> str | None:
    """Nome da aba de um tipo, como está escrito na pasta de trabalho, ou ``None``.

    A comparação ignora caixa (como o Excel), acentos (um nome pode chegar
    decomposto ou redigitado sem acento) e espaços nas pontas: sem isso a aba
    de metadados não seria encontrada e a verificação de hash seria pulada em
    silêncio. Pelo mesmo motivo, duas abas com o mesmo nome ou uma aba com o
    nome do formato antigo levantam ``ValueError``; com ``Mappings`` e sem
    a aba de mapeamentos em português, o erro diz como converter o relatório.
    """
    found = _sheets_named(sheet_names, SHEETS[kind])
    legacy = _sheets_named(sheet_names, LEGACY_SHEETS[kind]) if kind in LEGACY_SHEETS else []
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
    return not (
        pd.api.types.is_numeric_dtype(series)
        or pd.api.types.is_bool_dtype(series)
        or pd.api.types.is_datetime64_any_dtype(series)
    )


def _convert_columns(frame: pd.DataFrame, table: str, convert) -> None:
    """Aplica ``convert(campo, valor)``, no próprio quadro, às colunas de texto com tratamento.

    Não usa ``Series.map``, que reinfere o dtype: ``[NaN, 6, 32]`` numa
    coluna object voltava float64 com ``6.0``. Aqui cada célula fica como
    ``convert`` a devolveu, numa coluna object. As colunas são percorridas
    por posição, o que funciona também com nomes repetidos.
    """
    for position, column in enumerate(frame.columns):
        if not isinstance(column, str) or _field_kind(table, column) is None:
            continue
        series = frame.iloc[:, position]
        if not _holds_text(series):
            continue
        converted = [convert(column, value) for value in series.tolist()]
        frame.isetitem(position, pd.Series(converted, index=series.index, dtype=object))


def to_external_frame(frame: pd.DataFrame, table: str) -> pd.DataFrame:
    """Quadro interno -> esquema em português, para gravar. Não altera ``frame``.

    Colunas que não são da tabela ficam com o nome e os valores que têm.
    """
    out = frame.copy()
    _convert_columns(out, table, lambda field, value: external_value(table, field, value))
    names = COLUMNS[table]
    return out.rename(
        columns=lambda name: names.get(name, name) if isinstance(name, str) else name
    )


def to_internal_frame(frame: pd.DataFrame, table: str) -> pd.DataFrame:
    """Quadro lido de um arquivo -> nomes e valores internos. Não altera ``frame``.

    Recusa com ``ValueError`` um cabeçalho do formato antigo (``check_header``).
    """
    check_header(frame.columns, table)
    names = _reverse(COLUMNS[table])
    out = frame.copy().rename(
        columns=lambda name: names.get(name, name) if isinstance(name, str) else name
    )
    _convert_columns(out, table, lambda field, value: internal_value(table, field, value))
    return out


def internal_header_index(header_row: Sequence[object], table: str) -> dict[str, int]:
    """Linha de cabeçalho do openpyxl -> {nome interno: índice da coluna}.

    Confere o cabeçalho como ``to_internal_frame``. Cabeçalhos que não são da
    tabela entram com o próprio nome; células vazias ficam de fora.
    """
    check_header(header_row, table)
    names = _reverse(COLUMNS[table])
    return {
        names.get(name, name) if isinstance(name, str) else name: position
        for position, name in enumerate(header_row)
        if name is not None
    }


# ---------------------------------------------------------------------------
# Metadados da execução
# ---------------------------------------------------------------------------


def to_external_metadata(metadata: Mapping[str, object]) -> pd.DataFrame:
    """Metadados com chaves internas -> quadro ``chave``/``valor`` em português.

    Traduz as chaves e, nas chaves que podem tê-los, os sentinelas. Os demais
    valores passam intactos, na mesma ordem.
    """
    rows = []
    for key, value in metadata.items():
        if key in METADATA_SENTINEL_KEYS and isinstance(value, str):
            value = METADATA_SENTINELS.get(value, value)
        rows.append((METADATA_KEYS.get(key, key), value))
    return pd.DataFrame(rows, columns=list(COLUMNS["run_metadata"].values()))


def _clean_text(value: object) -> str:
    return "" if _is_missing(value) else str(value).strip()


def internal_metadata(frame: pd.DataFrame) -> dict[str, str]:
    """Aba de metadados lida -> {chave interna: valor como texto}.

    Os valores são limpos como no ``apply``: sem espaços nas pontas, célula
    vazia como ``""``. Levanta ``ValueError`` para chave repetida na coluna
    crua, inclusive duas chaves em branco (a regra que o ``apply`` sempre
    aplicou), e para chave do formato antigo: igual à interna, ela valeria no
    lugar da chave em português sem aviso.
    """
    check_header(frame.columns, "run_metadata")
    title = SHEETS["run_metadata"]
    key_column, value_column = COLUMNS["run_metadata"].values()
    if value_column not in frame.columns:
        raise ValueError(
            f"A aba {title!r} precisa ter as colunas {key_column!r} e {value_column!r}."
        )
    keys = frame[key_column]
    repeated = keys[keys.duplicated()]
    if not repeated.empty:
        labels = sorted({_clean_text(key) for key in repeated})
        raise ValueError(f"A aba {title!r} tem chaves repetidas: {labels}.")
    written = [_clean_text(key) for key in keys]
    legacy = {key: METADATA_KEYS[key] for key in written if key in METADATA_KEYS}
    if legacy:
        raise _legacy_names_error(f"A aba {title!r}", legacy)

    internal_keys = _reverse(METADATA_KEYS)
    internal_sentinels = _reverse(METADATA_SENTINELS)
    metadata: dict[str, str] = {}
    for key, raw_value in zip(written, frame[value_column]):
        if not key:
            continue
        key = internal_keys.get(key, key)
        value = _clean_text(raw_value)
        if key in METADATA_SENTINEL_KEYS:
            value = internal_sentinels.get(value, value)
        metadata[key] = value
    return metadata
