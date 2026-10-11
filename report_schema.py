"""Converte os nomes e valores internos para o formato em português dos arquivos.

O código usa valores como ``accepted``, ``semantic`` e ``Nome (occurrence 2)``.
Este módulo os converte ao gravar relatórios e planilhas e faz a conversão
inversa na leitura. As mensagens já chegam em português; aqui ficam os
rótulos de ``TEXT_LABELS`` e a formatação dos números.

Os inputs da IA não passam por essa conversão. Prompt, payload, embedding,
schema de ``DataMappingDecision`` e nomes de coluna enviados aos modelos
precisam permanecer idênticos aos registrados no golden test.

A leitura aceita apenas a versão 3 do schema, em português. Relatórios da
versão 2 recebem uma mensagem com instruções de conversão. Nomes antigos
misturados ao formato atual também geram erro, pois uma leitura parcial
poderia ignorar hashes ou pareceres do revisor.

Este módulo não importa ``invoice_mapper`` nem ``apply_invoice_mapping``,
evitando imports circulares.
"""

from __future__ import annotations

import math
import re
import unicodedata
from typing import Iterable, Mapping, Sequence

import pandas as pd


# ---------------------------------------------------------------------------
# Versões do schema
# ---------------------------------------------------------------------------

SCHEMA_VERSION_PT = 3
#: Formato antigo, em inglês, que não é mais lido.
SCHEMA_VERSION_LEGACY = 2
#: Último commit com o ``migrate_reports.py``, que converte o formato antigo.
MIGRATION_COMMIT = "5fe5092"


# ---------------------------------------------------------------------------
# Abas
# ---------------------------------------------------------------------------

#: Nomes das abas por tipo: quatro do relatório e duas da planilha mapeada.
#: Mapeamentos deve permanecer na primeira posição, usada pelo apply na leitura.
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

#: Cabeçalhos por tabela, na ordem de gravação. Os nomes internos também
#: eram usados como cabeçalhos no formato antigo e permitem identificá-lo.
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
    # margem_minima_nome guarda --name-margin; margem_nome guarda o valor
    # medido em cada proposta. Os dois campos precisam de nomes distintos.
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

#: Marcador usado pelo backfill quando havia evidência objetiva, mas os
#: dados arquivados não permitiam identificar a etapa. mapping_steps não
#: o classifica como etapa de evidência.
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

#: Rótulos usados nas mensagens em português. Os IDs internos permanecem
#: em inglês porque também integram os inputs da IA, como os valores
#: retornados por header_semantic_role.
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
#: * ``text``: texto livre preservado como recebido, incluindo justificativas
#:   da IA e observações do revisor.
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
    """Inverte o dicionário de nomes para converter os valores lidos do arquivo."""
    return {written: internal for internal, written in mapping.items()}


# ---------------------------------------------------------------------------
# Prova aritmética e números dentro de frases
# ---------------------------------------------------------------------------

def format_decimal_br(value: float, places: int | None = None) -> str:
    """Formata um número com vírgula decimal, sem separador de milhar.

    ``places`` define a quantidade de casas decimais. Quando omitido, mantém
    os dígitos de ``str(value)``: ``0.9996`` vira ``"0,9996"``.
    """
    text = str(value) if places is None else f"{value:.{places}f}"
    return text.replace(".", ",")


def format_percent_br(value: float, places: int = 1) -> str:
    """Formata uma fração como porcentagem: ``0.882`` vira ``"88,2%"``."""
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
# Aceita variações de maiúsculas e acentos na digitação, incluindo
# caracteres Unicode decompostos.
_OCCURRENCE_PT = re.compile(
    r"(?P<name>.*) \(ocorr(?:ê|e\u0302|e)ncia (?P<n>[0-9]+)\)",
    re.DOTALL | re.IGNORECASE,
)
_UNNAMED_PT = re.compile(r"coluna sem nome (?P<n>[0-9]+)", re.IGNORECASE)


def display_name_to_external(text: object) -> object:
    """Converte os marcadores internos do nome de coluna para o formato do relatório.

    ``Valor (occurrence 2)`` vira ``Valor (ocorrência 2)``, e ``Unnamed column 7``
    vira ``Coluna sem nome 7``. O sufixo de ocorrência só é reconhecido no fim
    do nome. Outros textos são preservados, e repetir a conversão não os altera.
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
    """Converte os marcadores do nome de coluna para o formato interno.

    Preserva os demais textos. Há uma ambiguidade conhecida: um cabeçalho
    literal como ``Coluna sem nome 3`` é interpretado como o marcador de uma
    coluna sem cabeçalho.
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
    """Converte o valor de um campo para o formato usado no arquivo.

    ``field`` recebe o nome interno do campo. Números, texto livre e valores
    desconhecidos são preservados. Repetir a conversão produz o mesmo resultado.
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
    """Converte o valor lido do arquivo para a representação interna.

    Enums exigem correspondência exata com o valor em português. O parecer do
    revisor é preservado para que o leitor o valide com
    ``normalize_reviewer_status`` e possa indicar a linha em caso de erro.
    Texto livre também é preservado.
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
    """Normaliza acentos e maiúsculas para comparar o texto digitado pelo revisor."""
    decomposed = unicodedata.normalize("NFKD", text)
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch)).casefold()


def normalize_reviewer_status(value: object) -> str | None:
    """Normaliza o parecer do revisor e retorna ``None`` para valores não reconhecidos.

    Aceita ``correto``, ``incorreto`` e ``inexistente``, ignorando maiúsculas,
    acentos e espaços nas extremidades. Uma célula vazia retorna ``""``.
    Os pareceres legados em inglês são rejeitados explicitamente: como
    coincidem com os valores internos, poderiam passar pela validação por engano.
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
    """Retorna o nome da aba para um tipo, como ``mappings`` ou ``run_metadata``."""
    return SHEETS[kind]


def header_label(table: str, field: str) -> str:
    """Retorna o cabeçalho do relatório para um campo interno; preserva campos desconhecidos."""
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
    """Valida as colunas obrigatórias e rejeita cabeçalhos do formato antigo.

    Gera ``ValueError`` quando falta a coluna obrigatória ou há um nome legado.
    Se Mapeamentos contém nomes antigos e não tem ``numero_coluna_modelo``,
    a mensagem orienta a conversão do relatório inteiro. Nos demais casos,
    indica os cabeçalhos que precisam ser renomeados.
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
    """Localiza abas pelo nome, ignorando maiúsculas, acentos e espaços nas extremidades."""
    wanted = _fold(name.strip())
    return [n for n in sheet_names if isinstance(n, str) and _fold(n.strip()) == wanted]


def find_sheet(sheet_names: Sequence[str], kind: str) -> str | None:
    """Localiza a aba pelo tipo e retorna seu nome original, ou ``None`` se não existir.

    Ignora maiúsculas, acentos e espaços nas extremidades para reconhecer
    nomes editados manualmente. Isso evita que uma variação de escrita esconda
    a aba de metadados e desative a verificação de hash.

    Abas ambíguas ou com nomes legados geram ``ValueError``. Se houver apenas
    ``Mappings`` em vez de Mapeamentos, a mensagem orienta a conversão do relatório.
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
# DataFrames (pandas) e linhas de cabeçalho (openpyxl)
# ---------------------------------------------------------------------------


def _holds_text(series: pd.Series) -> bool:
    return not (
        pd.api.types.is_numeric_dtype(series)
        or pd.api.types.is_bool_dtype(series)
        or pd.api.types.is_datetime64_any_dtype(series)
    )


def _convert_columns(frame: pd.DataFrame, table: str, convert) -> None:
    """Aplica ``convert(campo, valor)`` às colunas de texto do próprio DataFrame.

    Evita ``Series.map`` porque ele infere o dtype novamente: uma coluna object
    com ``[NaN, 6, 32]`` poderia virar float64. A atribuição usada aqui preserva
    o resultado de ``convert`` em uma coluna object. O acesso por posição
    também permite trabalhar com nomes de coluna repetidos.
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
    """Converte uma cópia do DataFrame para o formato em português do relatório.

    Preserva os nomes e valores das colunas que não pertencem ao schema.
    """
    out = frame.copy()
    _convert_columns(out, table, lambda field, value: external_value(table, field, value))
    names = COLUMNS[table]
    return out.rename(
        columns=lambda name: names.get(name, name) if isinstance(name, str) else name
    )


def to_internal_frame(frame: pd.DataFrame, table: str) -> pd.DataFrame:
    """Converte uma cópia do DataFrame lido para os nomes e valores internos.

    ``check_header`` gera ``ValueError`` se encontrar cabeçalhos do formato antigo.
    """
    check_header(frame.columns, table)
    names = _reverse(COLUMNS[table])
    out = frame.copy().rename(
        columns=lambda name: names.get(name, name) if isinstance(name, str) else name
    )
    _convert_columns(out, table, lambda field, value: internal_value(table, field, value))
    return out


def internal_header_index(header_row: Sequence[object], table: str) -> dict[str, int]:
    """Associa cada nome interno ao índice da coluna no cabeçalho do openpyxl.

    Valida o cabeçalho como ``to_internal_frame``. Mantém os nomes de campos
    desconhecidos e ignora células vazias.
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
    """Monta um DataFrame de metadados com as colunas ``chave`` e ``valor``.

    Converte as chaves e os valores especiais previstos no schema. Preserva
    os demais valores e a ordem dos registros.
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
    """Lê os metadados em um dicionário de chaves internas e valores de texto.

    Remove espaços nas extremidades e converte células vazias em ``""``, como
    o apply. Chaves repetidas, inclusive duas chaves vazias, geram ``ValueError``.
    Chaves do formato antigo também são rejeitadas para evitar a leitura
    silenciosa de um relatório parcialmente convertido.
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
