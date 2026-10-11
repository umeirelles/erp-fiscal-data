"""Associa as colunas da planilha do cliente às colunas de ``model.xlsx``.

``map_columns`` organiza o trabalho em duas passagens. A primeira compara os
cabeçalhos. A segunda analisa os valores usando relações aritméticas,
catálogos oficiais, amostras e dígitos verificadores. No modo online, também
consulta o resolver de IA. As regras estão em docs/ARCHITECTURE.md.

O código usa valores internos em inglês, como ``accepted`` e ``semantic``.
``report_schema`` faz a conversão para o formato em português dos arquivos.
As mensagens destinadas ao usuário são escritas em português neste módulo.

Os inputs da OpenAI permanecem em inglês: prompt, prefixo do embedding,
``DataMappingDecision``, payload, ``semantic_header_conflict``, ``ColumnRef.name``
e ``display_name``. O golden test em ``tests/test_model_input_golden.py``
verifica que esses inputs não mudaram.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sys
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from difflib import SequenceMatcher
from pathlib import Path
from typing import Mapping, Protocol, Sequence

import numpy as np
import pandas as pd
from openai import OpenAI
from pydantic import BaseModel, Field

import report_schema
from cli_ptbr import PortugueseArgumentParser
from spreadsheet import ColumnRef, SheetTable, normalize_header, _header_text
from target_schema import target_field
from mapping_steps import deciding_step, format_steps
from reference_catalog import ReferenceCatalogs, best_catalog_evidence, expected_catalogs


DEFAULT_EMBEDDING_MODEL = "text-embedding-3-large"
DEFAULT_LLM_MODEL = "gpt-4o-mini"
#: Versão do formato do relatório; a 3 é o schema em português.
MAPPING_SCHEMA_VERSION = 3
PROMPT_VERSION = "2026-08-04.2"
#: Colunas que o revisor preenche na aba de mapeamentos (nomes internos).
REVIEWER_COLUMNS = ["reviewer_status", "correct_client_column", "reviewer_note"]


# ---------------------------------------------------------------------------
# Thresholds e pesos
#
# MapperConfig reúne os parâmetros expostos pela CLI. As demais constantes
# ficam logo abaixo. Alterações nesses valores podem mudar os mapeamentos;
# compare os resultados com o mapeval (docs/EVALUATION.md). A seção
# "Constantes" de docs/ARCHITECTURE.md explica o uso de cada parâmetro.
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


# Cabeçalhos
IDENTICAL_HEADER_SCORE = 1.0  # texto normalizado idêntico
SEMANTIC_EQUIVALENCE_SCORE = 0.98  # assinaturas iguais: passa do threshold de nome, abaixo do idêntico
LEXICAL_SEQUENCE_WEIGHT = 0.7  # avaliador léxico: 0,7 x SequenceMatcher + 0,3 x Jaccard
LEXICAL_JACCARD_WEIGHT = 0.3
_MIN_ROLE_PREFIX = 3  # abaixo deste tamanho, uma palavra é ruído, não truncamento

# Perfil dos valores: pesos de profile_similarity (somam 1)
PROFILE_NUMERIC_FRACTION_WEIGHT = 0.05
PROFILE_DIGIT_FRACTION_WEIGHT = 0.05
PROFILE_ALPHA_FRACTION_WEIGHT = 0.05
PROFILE_LENGTH_WEIGHT = 0.05
PROFILE_DIGIT_COUNT_WEIGHT = 0.10
PROFILE_DIGIT_COUNT_OVERLAP_WEIGHT = 0.55
PROFILE_TYPE_WEIGHT = 0.15
GENERIC_PROFILE_CAP = 0.84  # semelhança de formato sozinha nunca alcança o threshold de aceite

# Amostras distintivas e identificadores
DISTINCTIVE_MIN_DIGITS = 6  # um valor só de dígitos precisa de 6 para não coincidir por acaso
DISTINCTIVE_MIN_CHARS = 4  # os demais, de 4 caracteres...
DISTINCTIVE_MIN_MIXED_CHARS = 3  # ...ou 3, se misturam letras e dígitos
MIN_SHARED_SAMPLES = 2  # amostras distintivas em comum para valer como evidência
OVERLAP_SCORE_BASE = 0.90  # pontuação da sobreposição: min(0,99; 0,90 + 0,09 x sobreposição)
OVERLAP_SCORE_SLOPE = 0.09
OVERLAP_SCORE_CAP = 0.99
IDENTIFIER_MIN_VALID_FRACTION = 0.80  # fração com DV válido para tratar a coluna como CPF/CNPJ/chave
DISTINCTIVE_IDENTIFIER_SCORE = 0.90

# Aritmética fiscal: valor = base x alíquota e valor do item = quantidade x preço
NONZERO = 1e-12  # abaixo disto o número conta como zero
CENTS_TOLERANCE = 0.02  # dois centavos: erro absoluto aceito e menor valor que conta
TAX_RELATIVE_TOLERANCE = 0.02
LINE_RELATIVE_TOLERANCE = 0.03
MIN_RELATIONSHIP_ROWS = 5
FULL_CONFIDENCE_ROWS = 20.0  # com menos linhas, a pontuação cai por √(linhas / 20)
MIN_DISTINCT_VALUES = 3  # base ou valor com menos valores distintos: identidade degenerada...
DEGENERATE_DISCOUNT = 0.75  # ...e pontuação descontada
CONTRADICTION_MIN_ROWS = 20  # contradição forte: >= 20 linhas, cobertura >= 0,80, aderência <= 0,20
CONTRADICTION_MIN_COVERAGE = 0.80
CONTRADICTION_MAX_FIT = 0.20
TRIO_MIN_FIT = 0.97  # aderência mínima à equação para registrar um trio
TRIO_MIN_ROWS = 30  # quantidade mínima de linhas para sustentar a relação
DEMOTION_MIN_RELATIONSHIP = 0.90  # auditoria dos trios aceitos na primeira passagem
DEMOTION_MIN_IMPROVEMENT = 0.50

# Catálogos oficiais de códigos
CATALOG_MIN_VALUES = 5
CATALOG_MIN_FORMAT_FRACTION = 0.90
UNIQUE_DOMAIN_SPECIFICITY = 1.0  # CFOP, NCM e município do IBGE
#: Score mínimo para catálogos com especificidade 1,0. Tabelas pequenas,
#: como CST e CSOSN, podem coincidir com códigos internos do ERP; elas
#: não recebem esse tratamento.
DISTINCTIVE_CATALOG_FLOOR = 0.50
DISTINCTIVE_CATALOG_MIN_CODES = 5  # códigos válidos distintos exigidos com esse piso
AUDIT_MIN_SPECIFICITY = 0.90  # auditoria: um aceite com menos de 10% de códigos válidos é reaberto
AUDIT_MAX_COVERAGE = 0.10
CATALOG_DECISION_MIN_SCORE = 0.90
CATALOG_DECISION_MIN_LEAD = 0.10
REVIEW_MIN_CATALOG_COVERAGE = 0.90
UNMATCHED_CATALOG_THRESHOLD = 1.0  # aba de colunas sem uso: só os domínios de especificidade 1,0

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

# Fila de Revisão
_CLAMPED_BAND = 0.0002  # confianças tão próximas são o mesmo teto; a margem desempata
RESOLVER_DISAGREEMENT_GAP = 0.10
FLOAT_TOLERANCE = 1e-9


# ---------------------------------------------------------------------------
# Mensagens exibidas nos relatórios
#
# A justificativa escrita pela IA (DataMappingDecision.reason) passa intacta,
# em inglês, porque o prompt continua em inglês. Números entram com vírgula
# decimal (report_schema.format_decimal_br / format_percent_br) e rótulos
# fiscais por report_schema.TEXT_LABELS.
# ---------------------------------------------------------------------------

#: Conflito de cabeçalho. Os lados chegam prontos: famílias em maiúsculas
#: (``ICMS``), qualificadores e papéis pelos rótulos de ``report_schema``.
HEADER_CONFLICT_FAMILY_PT = "tributos diferentes ({left} vs {right})"
HEADER_CONFLICT_QUALIFIER_PT = "tratamentos tributários diferentes ({left} vs {right})"
HEADER_CONFLICT_ROLE_PT = "tipos de campo incompatíveis ({left} vs {right})"

#: Coluna justificativa.
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
#: formatadas por ``report_schema.format_percent_br`` (``88,2%``).
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
#: (``report_schema.display_name_to_external``); sai entre aspas, por ``!r``.
REASON_VACUITY = (
    "{client_column!r} não tem linhas preenchidas. O mapeamento não foi aceito, "
    "pois essa origem deixaria a coluna do modelo sem dados."
)

#: Coluna prova_aritmetica. ``tax_scope`` é a sigla do tributo em maiúsculas;
#: ``fit_score`` chega com vírgula decimal (``0,998``).
PROOF_TEXT = (
    "valor = base x alíquota se confirma para {tax_scope} em {rows_tested} linhas, "
    "com aderência {fit_score}"
)
#: Complemento da prova quando o cliente tem mais de um bloco do mesmo tributo.
#: Termina com o marcador que ``report_schema.proof_is_decisive`` procura.
PROOF_NOT_DECISIVE_SUFFIX = (
    " - mas este cliente tem {alternatives} blocos disjuntos de {tax_scope} que "
    "satisfazem a equação, portanto ela " + report_schema.PROOF_NOT_DECISIVE_PT
)

#: Coluna detalhe_sinais, unidas por "; ". ``slot``, ``catalog`` e ``kind``
#: chegam como rótulos legíveis; ``distinct_coverage`` e ``fraction``, como
#: porcentagens com vírgula decimal.
SIGNAL_TRIO = "participa como {slot} de um trio comprovado de valor = base x alíquota"
SIGNAL_CATALOG = (
    "{distinct_coverage} dos seus {unique_formatted_count} códigos distintos são "
    "válidos na tabela oficial de {catalog}"
)
SIGNAL_CHECKDIGIT = "{fraction} dos valores passam na validação do dígito verificador de {kind}"
SIGNAL_DATES = "contém datas"
SIGNAL_EMPTY = "Esta coluna não tem valores."
SIGNAL_ALL_ZERO = "Todos os valores desta coluna são zero."
SIGNAL_NO_SIGNAL = (
    "A coluna contém dados, mas a análise não identificou sinais para priorizar "
    "sua revisão. Ela permanece na lista de colunas sem uso."
)

#: Coluna motivo_retencao: as três razões possíveis.
WHY_HELD_BELOW_THRESHOLD = "confiança abaixo do threshold de aceitação"
WHY_HELD_NAME_ONLY = (
    "o cabeçalho sugere uma correspondência, mas falta evidência para aceitação automática"
)
WHY_HELD_VACUOUS = (
    "a coluna de origem está vazia; confirmar a proposta não acrescentaria dados ao modelo"
)
#: Coluna divergencia_ia. ``llm_final`` chega com vírgula decimal (``0,75``).
RESOLVER_DISAGREES = "confiança informada pelo resolver de IA: {llm_final}"


# ---------------------------------------------------------------------------
# Leitura dos cabeçalhos: normalização, sinônimos, família de tributo e papel
# ---------------------------------------------------------------------------

_HEADER_STOPWORDS = {
    "44", "a", "as", "da", "das", "de", "do", "dos", "digit", "for", "of", "o", "os", "para", "the",
}

_HEADER_TOKEN_ALIASES = {
    "access": "acesso",
    "aliq": "aliquota",
    "aliquota": "aliquota",
    "alq": "aliquota",
    "basis": "base",
    "business": "negocio",
    "cof": "cofins",
    "cofi": "cofins",
    "cod": "codigo",
    "ctg": "categoria",
    "date": "data",
    "desc": "descricao",
    "description": "descricao",
    "doc": "documento",
    "document": "documento",
    "electronic": "eletronico",
    "eletronica": "eletronico",
    "empresa": "empresa",
    "goods": "mercadoria",
    "invoice": "nota",
    "ibss": "ibs",
    "key": "chave",
    "lanc": "lancamento",
    "liq": "liquido",
    "liquido": "liquido",
    "measure": "medida",
    "mercadorias": "mercadoria",
    "name": "nome",
    "negocios": "negocio",
    "net": "liquido",
    "no": "numero",
    "nr": "numero",
    "num": "numero",
    "number": "numero",
    "partner": "parceiro",
    "place": "local",
    "posting": "lancamento",
    "price": "preco",
    "product": "produto",
    "qtd": "quantidade",
    "quantity": "quantidade",
    "rate": "aliquota",
    "region": "regiao",
    "series": "serie",
    "supplier": "fornecedor",
    "taxa": "aliquota",
    "type": "categoria",
    "unit": "unidade",
    "val": "valor",
    "value": "valor",
    "vendor": "fornecedor",
    "vlr": "valor",
}

_TAX_FAMILY_ALIASES = {"cof": "cofins", "cofi": "cofins", "fcp": "fecp", "ibss": "ibs"}

_TAX_FAMILIES = {
    "cbs", "cofins", "csll", "fecp", "icap", "icep", "icms", "icsp",
    "ibs", "ibsm", "inss", "ipi", "irpj", "iss", "iva", "pis",
}

#: As três posições do trio fiscal e as três do trio quantidade x preço.
_TRIO_ROLES = frozenset({"base", "rate", "value"})
_LINE_ROLES = frozenset({"quantity", "unit_price", "line_value"})

#: Palavras que nomeiam um papel no cabeçalho (usadas por header_semantic_role).
#: Não incluem "outros" nem "isentos", que _ROLE_CONCEPT_WORDS inclui.
_OTHER_WORDS = frozenset({"outra", "outras", "outro", "other"})
_EXEMPT_WORDS = frozenset({"isenta", "isentas", "isento", "exempt"})
_REDUCED_WORDS = frozenset({"reduzida", "reduzido", "reduced"})
_BASE_WORDS = frozenset({"base", "basis"})
_VALUE_WORDS = frozenset({"valor", "value", "amount"})
_RATE_WORDS = frozenset({"aliquota", "rate", "taxa", "tximp"})
_MONTANTE_WORDS = frozenset({"mont", "monta", "montante"})

#: Palavras de papel agrupadas pelo conceito. Os ERPs truncam os cabeçalhos
#: ("Vlr Isen ICM"), então um prefixo que alcança um único conceito é
#: expandido. Nenhum prefixo daqui alcança dois conceitos;
#: ``test_the_role_vocabulary_is_prefix_unambiguous`` garante isso.
_ROLE_CONCEPT_WORDS: dict[str, frozenset[str]] = {
    "other": frozenset({"outra", "outras", "outro", "outros", "other"}),
    "exempt": frozenset({"isenta", "isentas", "isento", "isentos", "exempt"}),
    "reduced": frozenset({"reduzida", "reduzido", "reduced"}),
}
_ROLE_CANONICAL = {"other": "outras", "exempt": "isentas", "reduced": "reduzida"}
_ROLE_WORDS = frozenset(word for words in _ROLE_CONCEPT_WORDS.values() for word in words)


def _role_prefix_index() -> dict[str, str]:
    """Associa cada prefixo abreviado ao conceito que ele identifica sem ambiguidade."""
    index: dict[str, list[str]] = {}
    for concept, words in _ROLE_CONCEPT_WORDS.items():
        for word in words:
            for size in range(_MIN_ROLE_PREFIX, len(word)):
                index.setdefault(word[:size], []).append(concept)
    return {
        prefix: concepts[0]
        for prefix, concepts in index.items()
        if len(set(concepts)) == 1
    }


_ROLE_PREFIXES = _role_prefix_index()


def semantic_header_signature(value: object) -> frozenset[str]:
    """Reúne os conceitos do cabeçalho após remover stopwords e normalizar sinônimos.

    Usa vocabulário fiscal em português e inglês, sem regras específicas de
    cliente. Assinaturas iguais e não vazias indicam cabeçalhos equivalentes;
    a ordem das palavras não interfere na comparação.
    """
    normalized = normalize_header(value)
    # CST é a sigla padrão de "código de situação tributária".
    normalized = re.sub(r"\btax\s+situation\b", "cst", normalized)
    normalized = re.sub(r"\bsituacao\s+tributaria\b", "cst", normalized)
    # Nos livros fiscais, "valor contábil" é o total do documento. Exigir a
    # palavra de valor deixa de fora "Conta Contábil" e "Data Contábil".
    normalized = re.sub(r"\b(?:valor|vlr|val|vl)\s+contab\w*", "valor total nf", normalized)
    tokens = [
        _HEADER_TOKEN_ALIASES.get(token, token)
        for token in normalized.split()
        if token not in _HEADER_STOPWORDS
    ]
    # Alguns ERPs truncam PIS para PI; só no contexto de CST o sentido é inequívoco.
    if "cst" in tokens:
        tokens = ["pis" if token == "pi" else token for token in tokens]
    return frozenset(tokens)


def semantically_equivalent_headers(left: object, right: object) -> bool:
    left_signature = semantic_header_signature(left)
    return bool(left_signature) and left_signature == semantic_header_signature(right)


def underspecified_header_match(model_header: object, client_header: object) -> bool:
    """Verifica se o cabeçalho do cliente é genérico demais para uma escolha pelo nome.

    Isso ocorre quando ele contém apenas uma palavra da assinatura mais longa
    do modelo, como ``Centro`` para ``Centro custo``. A proposta ainda pode ser
    aceita se houver evidência suficiente nos dados.
    """
    model_signature = semantic_header_signature(model_header)
    client_signature = semantic_header_signature(client_header)
    return (
        len(model_signature) > 1
        and len(client_signature) == 1
        and client_signature < model_signature
    )


def header_tax_families(value: object) -> frozenset[str]:
    """Identifica os tributos mencionados explicitamente no cabeçalho."""
    families = {
        _TAX_FAMILY_ALIASES.get(token, token)
        for token in semantic_header_signature(value)
    }
    return frozenset(families & _TAX_FAMILIES)


def expand_truncated_role_words(tokens: set[str]) -> set[str]:
    """Expande os tokens abreviados que identificam um único papel fiscal.

    Mantém o token original e acrescenta a palavra completa ao conjunto.
    """
    expanded = set(tokens)
    for token in tokens:
        if token in _ROLE_WORDS or len(token) < _MIN_ROLE_PREFIX:
            continue
        concept = _ROLE_PREFIXES.get(token)
        if concept is not None:
            expanded.add(_ROLE_CANONICAL[concept])
    return expanded


def header_semantic_role(value: object) -> str | None:
    """Identifica o primeiro papel fiscal reconhecido no cabeçalho, sem consultar os valores."""
    normalized = normalize_header(value)
    padded = f" {normalized} "
    raw_tokens = set(normalized.split())
    tokens = expand_truncated_role_words(
        set(semantic_header_signature(value)) | raw_tokens
    )

    if (
        {"classification", "classificacao"} & tokens
        or ({"class", "classif"} & tokens and {"tax", "tributaria"} & tokens)
    ):
        return "tax_classification"
    if (
        " tax situation " in padded
        or "taxsituation" in tokens
        or " situacao " in padded
        or " sit tributaria " in padded
        or "cst" in tokens
        or " item estat imposto " in padded
        or re.search(r"\btipo\s+(?:de\s+|do\s+)?imposto\b", normalized)
    ):
        return "tax_status"
    # Um nome de família pode trazer um sinal de alíquota (DIF_ALIQ gera
    # "aliq"). Se o cabeçalho também nomeia uma posição e a alíquota vem só de
    # uma abreviação, a posição vence; "Tax rate (Tax/Base)" continua alíquota.
    names_a_slot = bool(
        tokens & (_OTHER_WORDS | _EXEMPT_WORDS | _REDUCED_WORDS | _BASE_WORDS | _VALUE_WORDS)
    )
    rate_word_spelled_out = bool(_RATE_WORDS & raw_tokens) or " tx imposto " in padded
    names_a_rate = bool(_RATE_WORDS & tokens) or " tx imposto " in padded
    if names_a_rate and not (names_a_slot and not rate_word_spelled_out):
        return "rate"
    if _OTHER_WORDS & tokens:
        return "other"
    if (
        _EXEMPT_WORDS & tokens
        or ({"exc", "excl", "excluded"} & tokens and _BASE_WORDS & tokens)
    ):
        return "exempt"
    if _REDUCED_WORDS & tokens and _BASE_WORDS & tokens:
        return "reduced_base"
    # "Mont." é a base de cálculo numa coluna de tributo (PIS Mont.) e o
    # montante (valor) fora dela.
    if header_tax_families(value) and _MONTANTE_WORDS & tokens:
        return "base"
    if _BASE_WORDS & tokens:
        return "base"
    if _VALUE_WORDS & tokens or ("total" in tokens and {"item", "linha"} & tokens):
        return "value"
    if _MONTANTE_WORDS & tokens:
        return "value"
    if {"grupo", "group"} & tokens and {"imposto", "tax"} & tokens:
        return "tax_group"
    if (
        {"grupo", "group"} & tokens
        and {"material", "mercadoria", "produto"} & tokens
    ):
        return "product_group"
    if {"lei", "law", "direito"} & tokens:
        return "tax_rule"
    if {"texto", "text", "descricao", "description"} & tokens:
        return "text"
    if "medida" in tokens and {"unidade", "codigo"} & tokens:
        return "unit_of_measure"
    if "preco" in tokens:
        return "unit_price"
    if ({"chave", "key"} & tokens and {"acesso", "nfe", "nf"} & tokens):
        return "nfe_key"
    if {"cnpj", "cpf"} & tokens or (
        {"fiscal", "tax"} & tokens
        and {"id", "identificador"} & tokens
        and not {"regional", "state", "estadual", "municipal"} & tokens
    ):
        return "party_tax_id"
    if (
        {"regional", "state", "estadual", "municipal"} & tokens
        and {"fiscal", "tax", "id", "inscricao", "registration"} & tokens
    ):
        return "regional_tax_id"
    if (
        "item" in tokens
        and not {"imposto", "tax", "total", "valor", "value", "amount"} & tokens
        and (
            len(tokens) == 1
            or bool({"numero", "codigo", "id", "identificador"} & tokens)
        )
    ):
        return "item_id"
    if (
        {"numero", "number", "documento", "document", "nf", "nfe", "invoice"}
        & tokens
        and not {"fiscal", "tax"} & tokens
    ):
        return "document_id"
    return None


def _same_explicit_role(model_header: object, client_header: object) -> bool:
    model_role = _target_role(model_header)
    return model_role is not None and model_role == header_semantic_role(client_header)


def _header_conflict_parts(
    left: object, right: object
) -> tuple[str, tuple[str, ...], tuple[str, ...]] | None:
    """Identifica o conflito entre dois cabeçalhos, ou retorna ``None``.

    Retorna ``(tipo, lado esquerdo, lado direito)``, com tipo ``family``,
    ``qualifier`` ou ``role``. As descrições para a IA e para o usuário partem
    dessa mesma estrutura, garantindo que representem o mesmo conflito.
    """
    definition = target_field(left)
    left_families = (frozenset(definition.tax_families) if definition is not None
                     else header_tax_families(left))
    right_families = header_tax_families(right)
    if left_families and right_families and left_families.isdisjoint(right_families):
        return "family", tuple(sorted(left_families)), tuple(sorted(right_families))

    left_qualifiers = (set(definition.tax_qualifiers) if definition is not None
                       else set(normalize_header(left).split())) & {"st"}
    right_qualifiers = set(normalize_header(right).split()) & {"st"}
    if (
        left_families
        and right_families
        and not left_families.isdisjoint(right_families)
        and left_qualifiers != right_qualifiers
    ):
        # Sem qualificador, o tributo é o normal (ICMS normal x ICMS ST).
        return (
            "qualifier",
            tuple(sorted(left_qualifiers)) or ("regular",),
            tuple(sorted(right_qualifiers)) or ("regular",),
        )

    left_role = _target_role(left)
    right_role = header_semantic_role(right)
    if left_role is not None and right_role is not None and left_role != right_role:
        return "role", (left_role,), (right_role,)
    return None


def semantic_header_conflict(left: object, right: object) -> str | None:
    """Descreve o conflito de tributo, qualificador ou papel entre dois cabeçalhos.

    Só compara atributos reconhecidos nos dois lados; a ausência de um atributo
    não constitui conflito. O texto integra o payload da IA e deve permanecer
    em inglês. Para o relatório, use ``describe_header_conflict_pt``.
    """
    parts = _header_conflict_parts(left, right)
    if parts is None:
        return None
    kind, left_side, right_side = parts
    if kind == "family":
        return f"tax-family conflict ({', '.join(left_side)} vs {', '.join(right_side)})"
    if kind == "qualifier":
        return f"tax-qualifier conflict ({', '.join(left_side)} vs {', '.join(right_side)})"
    return f"semantic-role conflict ({left_side[0]} vs {right_side[0]})"


def describe_header_conflict_pt(left: object, right: object) -> str | None:
    """Descreve em português o mesmo conflito identificado por ``semantic_header_conflict``."""
    parts = _header_conflict_parts(left, right)
    if parts is None:
        return None
    kind, left_side, right_side = parts
    if kind == "family":
        template, labels = HEADER_CONFLICT_FAMILY_PT, None
    elif kind == "qualifier":
        template, labels = HEADER_CONFLICT_QUALIFIER_PT, report_schema.TEXT_LABELS["tax_qualifier"]
    else:
        template, labels = HEADER_CONFLICT_ROLE_PT, report_schema.TEXT_LABELS["header_role"]

    def side(values: tuple[str, ...]) -> str:
        # Siglas de tributo em maiúsculas; qualificadores e papéis pelo rótulo.
        return ", ".join(
            value.upper() if labels is None else labels.get(value, value) for value in values
        )

    return template.format(left=side(left_side), right=side(right_side))


# ---------------------------------------------------------------------------
# Planilhas
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Perfil dos valores de uma coluna
# ---------------------------------------------------------------------------


def _json_value(value: object, max_length: int = 100) -> str:
    if isinstance(value, pd.Timestamp):
        text = value.isoformat()
    else:
        text = str(value).strip()
    return text if len(text) <= max_length else f"{text[: max_length - 1]}…"


def _numeric_value(value: object) -> float:
    """Converte um valor numérico, incluindo ``1.234,56``, ``10-`` e ``(10)``; retorna NaN se inválido."""
    if pd.isna(value):
        return float("nan")
    if isinstance(value, (int, float, np.number)) and not isinstance(value, bool):
        return float(value)
    text = str(value).strip().replace(" ", "")
    if not text:
        return float("nan")
    parenthesized_negative = text.startswith("(") and text.endswith(")")
    negative = text.endswith("-") or parenthesized_negative
    if text.endswith("-"):
        text = text[:-1]
    if parenthesized_negative:
        text = text[1:-1]
    text = re.sub(r"[^0-9,.-]", "", text)
    if not text or text in {"-", ".", ","}:
        return float("nan")
    if "," in text and "." in text:
        if text.rfind(",") > text.rfind("."):
            text = text.replace(".", "").replace(",", ".")
        else:
            text = text.replace(",", "")
    elif "," in text:
        text = text.replace(".", "").replace(",", ".")
    try:
        number = float(text)
    except ValueError:
        return float("nan")
    return -abs(number) if negative else number


def _sample_sort_key(text: str) -> tuple[int, float | str, str]:
    number = _numeric_value(text)
    if np.isfinite(number):
        return (0, number, text)
    return (1, normalize_header(text), text)


def sample_values(series: pd.Series, limit: int = 8) -> list[str]:
    """Seleciona uma amostra representativa que não depende da ordem das linhas.

    Inclui os dois valores mais frequentes e distribui o restante da seleção
    entre os valores distintos ordenados. Isso preserva exemplos da faixa de
    valores e das diferenças de formato.
    """
    if limit < 1:
        return []
    texts = [
        _json_value(value)
        for value in series
        if not pd.isna(value) and str(value).strip()
    ]
    if not texts:
        return []

    counts = pd.Series(texts, dtype="object").value_counts(sort=False).to_dict()
    ordered_unique = sorted(counts, key=_sample_sort_key)
    by_frequency = sorted(counts, key=lambda item: (-counts[item], _sample_sort_key(item)))
    selected: list[str] = []

    def add(value: str) -> None:
        if value not in selected and len(selected) < limit:
            selected.append(value)

    for value in by_frequency[:2]:
        add(value)
    if len(ordered_unique) == 1:
        return selected
    for position in np.linspace(0, len(ordered_unique) - 1, num=limit, dtype=int):
        add(ordered_unique[int(position)])
    for value in by_frequency:
        add(value)
    return selected


def _digits_only(value: object) -> str:
    return re.sub(r"\D", "", str(value).strip())


def _cpf_is_valid(digits: str) -> bool:
    if len(digits) != 11 or len(set(digits)) == 1:
        return False
    numbers = [int(value) for value in digits]
    for length in (9, 10):
        total = sum(numbers[index] * (length + 1 - index) for index in range(length))
        check = (total * 10) % 11
        if check == 10:
            check = 0
        if numbers[length] != check:
            return False
    return True


def _cnpj_is_valid(digits: str) -> bool:
    if len(digits) != 14 or len(set(digits)) == 1:
        return False
    numbers = [int(value) for value in digits]
    for length, weights in (
        (12, [5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2]),
        (13, [6, 5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2]),
    ):
        remainder = sum(numbers[index] * weights[index] for index in range(length)) % 11
        check = 0 if remainder < 2 else 11 - remainder
        if numbers[length] != check:
            return False
    return True


def _nfe_key_is_valid(digits: str) -> bool:
    if len(digits) != 44 or len(set(digits)) == 1:
        return False
    numbers = [int(value) for value in digits]
    weight = 2
    total = 0
    for number in reversed(numbers[:43]):
        total += number * weight
        weight = 2 if weight == 9 else weight + 1
    remainder = total % 11
    check = 0 if remainder in {0, 1} else 11 - remainder
    return numbers[43] == check


def _validation_fraction(texts: pd.Series, validator) -> float:
    return float(texts.map(lambda value: validator(_digits_only(value))).mean()) if len(texts) else 0.0


def summarize_values(series: pd.Series, limit: int = 8) -> dict[str, object]:
    """Monta o perfil da coluna com contagens, tipos, estatísticas, DVs e amostras.

    ``evidence_quality`` indica se a coluna está vazia (``empty``), contém apenas
    zeros (``all_zero``) ou tem dados informativos (``informative``). O perfil é
    enviado no payload da IA; suas chaves e a ordem delas fazem parte do input.
    """
    nonempty = series[series.notna()]
    if not nonempty.empty:
        nonempty = nonempty[nonempty.astype(str).str.strip() != ""]

    numeric = nonempty.map(_numeric_value).astype(float)
    numeric_fraction = float(numeric.notna().mean()) if len(nonempty) else 0.0
    numeric_values = numeric[numeric.notna()].astype(float)
    zero_fraction = (
        float(np.isclose(numeric_values, 0.0).mean()) if len(numeric_values) else 0.0
    )
    all_zero = bool(
        len(nonempty)
        and len(numeric_values) == len(nonempty)
        and np.isclose(numeric_values, 0.0).all()
    )
    evidence_quality = "empty" if not len(nonempty) else "all_zero" if all_zero else "informative"
    texts = nonempty.astype(str).str.strip()
    lengths = texts.str.len().astype(float)
    digit_counts = texts.str.count(r"\d").astype(float)
    width_counts = digit_counts[digit_counts > 0].value_counts()
    fixed_digit_width = int(width_counts.index[0]) if len(width_counts) else 0
    fixed_digit_width_fraction = (
        float(width_counts.iloc[0] / len(texts)) if len(width_counts) else 0.0
    )

    def character_fraction(text: str, predicate) -> float:
        return sum(bool(predicate(character)) for character in text) / len(text) if text else 0.0

    digit_fraction = (
        float(texts.map(lambda text: character_fraction(text, str.isdigit)).mean())
        if len(texts)
        else 0.0
    )
    alpha_fraction = (
        float(texts.map(lambda text: character_fraction(text, str.isalpha)).mean())
        if len(texts)
        else 0.0
    )

    def numeric_stat(compute) -> float | None:
        return round(float(compute(numeric_values)), 4) if len(numeric_values) else None

    return {
        "non_empty_count": int(len(nonempty)),
        "unique_count": int(nonempty.astype(str).nunique()) if len(nonempty) else 0,
        "inferred_type": pd.api.types.infer_dtype(nonempty, skipna=True)
        if len(nonempty)
        else "empty",
        "numeric_fraction": round(numeric_fraction, 3),
        "zero_fraction": round(zero_fraction, 3),
        "median_length": round(float(lengths.median()), 3) if len(lengths) else 0.0,
        "median_digit_count": round(float(digit_counts.median()), 3)
        if len(digit_counts)
        else 0.0,
        "digit_count_values": sorted(
            {int(value) for value in digit_counts if value > 0}
        ),
        "digit_fraction": round(digit_fraction, 3),
        "alpha_fraction": round(alpha_fraction, 3),
        "negative_fraction": round(
            float((numeric_values < 0).mean()) if len(numeric_values) else 0.0,
            3,
        ),
        "numeric_min": numeric_stat(lambda values: values.min()),
        "numeric_q25": numeric_stat(lambda values: values.quantile(0.25)),
        "numeric_median": numeric_stat(lambda values: values.median()),
        "numeric_q75": numeric_stat(lambda values: values.quantile(0.75)),
        "numeric_max": numeric_stat(lambda values: values.max()),
        "fixed_digit_width": fixed_digit_width,
        "fixed_digit_width_fraction": round(fixed_digit_width_fraction, 3),
        "valid_cpf_fraction": round(_validation_fraction(texts, _cpf_is_valid), 3),
        "valid_cnpj_fraction": round(_validation_fraction(texts, _cnpj_is_valid), 3),
        "valid_nfe_key_fraction": round(_validation_fraction(texts, _nfe_key_is_valid), 3),
        "evidence_quality": evidence_quality,
        "samples": sample_values(series, limit),
    }


#: Qualidades de perfil que não trazem evidência das linhas.
_WEAK_EVIDENCE = frozenset({"empty", "all_zero"})


def profile_similarity(
    left: dict[str, object], right: dict[str, object]
) -> float:
    """Calcula a similaridade de formato entre dois perfis; retorna 0 se algum não for informativo."""
    if (
        left["evidence_quality"] != "informative"
        or right["evidence_quality"] != "informative"
    ):
        return 0.0

    def closeness(key: str) -> float:
        return 1.0 - abs(float(left[key]) - float(right[key]))

    left_length = float(left["median_length"])
    right_length = float(right["median_length"])
    length_scale = max(left_length, right_length, 1.0)
    length_score = 1.0 - min(abs(left_length - right_length) / length_scale, 1.0)
    left_digits = float(left["median_digit_count"])
    right_digits = float(right["median_digit_count"])
    digit_scale = max(left_digits, right_digits, 1.0)
    digit_count_score = 1.0 - min(abs(left_digits - right_digits) / digit_scale, 1.0)
    left_digit_counts = set(left["digit_count_values"])
    right_digit_counts = set(right["digit_count_values"])
    digit_count_overlap = (
        len(left_digit_counts & right_digit_counts)
        / min(len(left_digit_counts), len(right_digit_counts))
        if left_digit_counts and right_digit_counts
        else 0.0
    )
    type_score = float(left["inferred_type"] == right["inferred_type"])
    score = (
        PROFILE_NUMERIC_FRACTION_WEIGHT * closeness("numeric_fraction")
        + PROFILE_DIGIT_FRACTION_WEIGHT * closeness("digit_fraction")
        + PROFILE_ALPHA_FRACTION_WEIGHT * closeness("alpha_fraction")
        + PROFILE_LENGTH_WEIGHT * length_score
        + PROFILE_DIGIT_COUNT_WEIGHT * digit_count_score
        + PROFILE_DIGIT_COUNT_OVERLAP_WEIGHT * digit_count_overlap
        + PROFILE_TYPE_WEIGHT * type_score
    )
    return round(max(0.0, min(score, 1.0)), 4)


def _distinctive_samples(profile: dict[str, object]) -> set[str]:
    """Seleciona amostras pouco sujeitas a coincidências, excluindo zeros e códigos curtos."""
    distinctive: set[str] = set()
    for value in profile["samples"]:
        normalized = normalize_header(value)
        compact = normalized.replace(" ", "")
        if not compact or compact in {"0", "00", "000", "0000"}:
            continue
        if compact.isdigit():
            if len(compact) >= DISTINCTIVE_MIN_DIGITS:
                distinctive.add(compact)
            continue
        if len(compact) >= DISTINCTIVE_MIN_CHARS or (
            len(compact) >= DISTINCTIVE_MIN_MIXED_CHARS
            and any(character.isalpha() for character in compact)
            and any(character.isdigit() for character in compact)
        ):
            distinctive.add(normalized)
    return distinctive


def distinctive_sample_overlap(
    left: dict[str, object], right: dict[str, object]
) -> float:
    """Calcula a proporção de amostras distintivas em comum em relação ao menor conjunto."""
    left_samples = _distinctive_samples(left)
    right_samples = _distinctive_samples(right)
    if not left_samples or not right_samples:
        return 0.0
    return round(
        len(left_samples & right_samples) / min(len(left_samples), len(right_samples)),
        4,
    )


def distinctive_sample_overlap_count(
    left: dict[str, object], right: dict[str, object]
) -> int:
    return len(_distinctive_samples(left) & _distinctive_samples(right))


def _overlap_evidence_score(overlap: float) -> float:
    return min(OVERLAP_SCORE_CAP, OVERLAP_SCORE_BASE + OVERLAP_SCORE_SLOPE * overlap)


def identifier_match_score(
    left: dict[str, object], right: dict[str, object],
    *, kinds: tuple[str, ...] | None = None,
) -> float:
    """Mede a fração de identificadores válidos dos tipos permitidos pelo campo.

    Sem uma definição de tipos, mantém a comparação de perfis para modelos
    alternativos. CPF, CNPJ e chave de NF-e têm comprimentos distintos, então
    suas frações podem ser somadas quando o campo permite mais de um tipo.
    """
    if kinds is not None:
        fraction = min(1.0, sum(float(right.get(f"valid_{kind}_fraction", 0.0)) for kind in kinds))
        return round(fraction, 4) if fraction >= IDENTIFIER_MIN_VALID_FRACTION else 0.0
    scores = []
    for key in (
        "valid_cpf_fraction",
        "valid_cnpj_fraction",
        "valid_nfe_key_fraction",
    ):
        left_score = float(left.get(key, 0.0))
        right_score = float(right.get(key, 0.0))
        if (
            left_score >= IDENTIFIER_MIN_VALID_FRACTION
            and right_score >= IDENTIFIER_MIN_VALID_FRACTION
        ):
            scores.append(min(left_score, right_score))
    return round(max(scores, default=0.0), 4)


def distinctive_profile_match(
    left: dict[str, object], right: dict[str, object]
) -> bool:
    """Verifica se os valores oferecem evidência além da semelhança de tipo."""
    if (
        left["evidence_quality"] != "informative"
        or right["evidence_quality"] != "informative"
    ):
        return False
    if distinctive_sample_overlap_count(left, right) >= MIN_SHARED_SAMPLES:
        return True
    return identifier_match_score(left, right) >= DISTINCTIVE_IDENTIFIER_SCORE


# ---------------------------------------------------------------------------
# Aritmética fiscal
# ---------------------------------------------------------------------------


def numeric_series(series: pd.Series) -> pd.Series:
    """Converte números no formato comum e no brasileiro, sem mudar a ordem das linhas."""
    if pd.api.types.is_numeric_dtype(series):
        return pd.to_numeric(series, errors="coerce").astype(float)
    return series.map(_numeric_value).astype(float)


def _identity_rows(columns: dict[str, pd.Series]) -> tuple[pd.DataFrame, int, float]:
    """Seleciona as linhas em que é possível testar a equação a x b = c.

    Uma linha é elegível quando a != 0 e (b != 0 ou |c| > dois centavos).
    Retorna as linhas elegíveis com os três valores numéricos, o total de
    elegíveis e a cobertura: linhas utilizáveis / linhas elegíveis.
    """
    first, second, third = columns
    frame = pd.concat(
        [numeric_series(series).rename(name) for name, series in columns.items()],
        axis=1,
    ).replace([np.inf, -np.inf], np.nan)
    eligible = frame[
        (frame[first].abs() > NONZERO)
        & ((frame[second].abs() > NONZERO) | (frame[third].abs() > CENTS_TOLERANCE))
    ]
    usable = eligible.dropna()
    coverage = len(usable) / len(eligible) if len(eligible) else 0.0
    return usable, len(eligible), coverage


def _within_tolerance(
    predicted: pd.Series, actual: pd.Series, relative_tolerance: float
) -> tuple[pd.Series, pd.Series]:
    """Identifica as linhas dentro da tolerância absoluta ou relativa e calcula seus erros relativos."""
    absolute_error = (predicted - actual).abs()
    scale = pd.concat([predicted.abs(), actual.abs()], axis=1).max(axis=1)
    relative_error = absolute_error / scale.clip(lower=CENTS_TOLERANCE)
    within = (absolute_error <= CENTS_TOLERANCE) | (relative_error <= relative_tolerance)
    return within, relative_error


def _identity_score(fit_score: float, coverage: float, rows: int, degenerate: bool) -> float:
    """Calcula aderência × √cobertura × min(1, √(linhas / 20)), com desconto para dados degenerados."""
    sample_factor = min(1.0, float(np.sqrt(rows / FULL_CONFIDENCE_ROWS)))
    variation_factor = DEGENERATE_DISCOUNT if degenerate else 1.0
    return fit_score * float(np.sqrt(coverage)) * sample_factor * variation_factor


def tax_calculation_evidence(
    base: pd.Series,
    rate: pd.Series,
    value: pd.Series,
    *,
    minimum_rows: int = MIN_RELATIONSHIP_ROWS,
) -> dict[str, object]:
    """Mede quanto os dados confirmam valor ~= base x alíquota, linha a linha.

    Testa a alíquota como percentual (18) e como fração (0,18), mantendo o
    resultado com melhor aderência. O dicionário retornado integra o payload da IA.
    """
    frame, eligible_count, coverage = _identity_rows(
        {"base": base, "rate": rate, "value": value}
    )
    if len(frame) < minimum_rows:
        return {
            "available": False,
            "rows_tested": int(len(frame)),
            "eligible_rows": int(eligible_count),
            "coverage": round(coverage, 4),
            "score": 0.0,
            "fit_score": 0.0,
            "rate_representation": None,
            "median_relative_error": None,
            "degenerate": True,
        }

    alternatives: list[tuple[float, float, int]] = []
    for divisor in (100, 1):
        within, relative_error = _within_tolerance(
            frame["base"] * frame["rate"] / divisor, frame["value"], TAX_RELATIVE_TOLERANCE
        )
        alternatives.append((float(within.mean()), float(relative_error.median()), divisor))
    fit_score, median_error, divisor = max(
        alternatives, key=lambda item: (item[0], -item[1])
    )
    degenerate = bool(
        frame["base"].nunique(dropna=True) < MIN_DISTINCT_VALUES
        or frame["value"].nunique(dropna=True) < MIN_DISTINCT_VALUES
    )
    score = _identity_score(fit_score, coverage, len(frame), degenerate)
    return {
        "available": True,
        "rows_tested": int(len(frame)),
        "eligible_rows": int(eligible_count),
        "coverage": round(coverage, 4),
        "score": round(score, 4),
        "fit_score": round(fit_score, 4),
        "rate_representation": "percent" if divisor == 100 else "decimal",
        "median_relative_error": round(median_error, 4),
        "degenerate": degenerate,
    }


def tax_calculation_scope(value: object) -> tuple[str, tuple[str, ...]] | None:
    """Identifica o tributo e seus qualificadores (ST, DIF etc.); retorna ``None`` se houver ambiguidade."""
    families = header_tax_families(value)
    if len(families) != 1:
        return None
    tokens = set(normalize_header(value).split())
    qualifiers = tuple(
        qualifier
        for qualifier in ("st", "icop", "dif", "diferencial")
        if qualifier in tokens
    )
    return next(iter(families)), qualifiers


def line_calculation_role(value: object) -> str | None:
    """Identifica pelo cabeçalho a posição na equação quantidade x preço unitário = valor do item."""
    tokens = semantic_header_signature(value)
    normalized = normalize_header(value)
    if {"quantidade", "quantity", "qtd"} & set(normalized.split()):
        return "quantity"
    if "preco" in tokens:
        return "unit_price"
    if (
        {"item", "linha"} & tokens
        and "valor" in tokens
        and "total" in tokens
    ):
        return "line_value"
    return None


def line_calculation_evidence(
    quantity: pd.Series,
    unit_price: pd.Series,
    line_value: pd.Series,
    *,
    minimum_rows: int = MIN_RELATIONSHIP_ROWS,
) -> dict[str, object]:
    """Mede quanto os dados confirmam quantidade x preço unitário ~= valor do item."""
    frame, eligible_count, coverage = _identity_rows(
        {"quantity": quantity, "unit_price": unit_price, "line_value": line_value}
    )
    if len(frame) < minimum_rows:
        return {
            "available": False,
            "rows_tested": int(len(frame)),
            "eligible_rows": int(eligible_count),
            "coverage": round(coverage, 4),
            "score": 0.0,
            "fit_score": 0.0,
            "median_relative_error": None,
            "degenerate": True,
        }
    within, relative_error = _within_tolerance(
        frame["quantity"] * frame["unit_price"], frame["line_value"], LINE_RELATIVE_TOLERANCE
    )
    fit_score = float(within.mean())
    degenerate = bool(
        frame["quantity"].nunique(dropna=True) < MIN_DISTINCT_VALUES
        or frame["line_value"].nunique(dropna=True) < MIN_DISTINCT_VALUES
    )
    score = _identity_score(fit_score, coverage, len(frame), degenerate)
    return {
        "available": True,
        "rows_tested": int(len(frame)),
        "eligible_rows": int(eligible_count),
        "coverage": round(coverage, 4),
        "score": round(score, 4),
        "fit_score": round(fit_score, 4),
        "median_relative_error": round(float(relative_error.median()), 4),
        "degenerate": degenerate,
    }


def _relationship_score(rules: Sequence[dict[str, object]]) -> float:
    return max(
        (
            float(rule["score"])
            for rule in rules
            if bool(rule.get("available"))
        ),
        default=0.0,
    )


def _relationship_is_strong_contradiction(
    rules: Sequence[dict[str, object]],
    *,
    minimum_rows: int = CONTRADICTION_MIN_ROWS,
    minimum_coverage: float = CONTRADICTION_MIN_COVERAGE,
    maximum_fit: float = CONTRADICTION_MAX_FIT,
) -> bool:
    return any(
        bool(rule.get("available"))
        and int(rule.get("rows_tested", 0)) >= minimum_rows
        and float(rule.get("coverage", 0.0)) >= minimum_coverage
        and float(rule.get("fit_score", rule.get("score", 0.0))) <= maximum_fit
        for rule in rules
    )


def discover_client_tax_trios(
    client_table: SheetTable,
) -> dict[tuple[str, tuple[str, ...]], list[dict[str, object]]]:
    """Procura trios de base, alíquota e valor que satisfaçam a equação nos dados do cliente.

    A relação valor = base x alíquota permite comparar os dados sem depender
    dos nomes usados pelo ERP. Retorna os trios válidos de cada escopo definido
    por ``tax_calculation_scope``, ordenados pela qualidade da evidência.
    ``alternatives`` informa quantos blocos sem colunas em comum foram encontrados,
    pois o cliente pode ter mais de um bloco para o mesmo tributo.
    """
    by_scope: dict[tuple[str, tuple[str, ...]], dict[str, list[int]]] = {}
    for index, column in enumerate(client_table.columns):
        role = header_semantic_role(column.name)
        if role not in _TRIO_ROLES:
            continue
        scope = tax_calculation_scope(column.name)
        if scope is None:
            continue
        by_scope.setdefault(scope, {}).setdefault(role, []).append(index)

    series_cache: dict[int, pd.Series] = {}

    def values(index: int) -> pd.Series:
        if index not in series_cache:
            series_cache[index] = client_table.series(client_table.columns[index])
        return series_cache[index]

    proven: dict[tuple[str, tuple[str, ...]], list[dict[str, object]]] = {}
    for scope, roles in by_scope.items():
        if not _TRIO_ROLES <= roles.keys():
            continue
        found: list[tuple[float, int, dict[str, object]]] = []
        for base_index in roles["base"]:
            for rate_index in roles["rate"]:
                for value_index in roles["value"]:
                    evidence = tax_calculation_evidence(
                        values(base_index), values(rate_index), values(value_index)
                    )
                    fit = float(evidence.get("fit_score", 0.0))
                    rows = int(evidence.get("rows_tested", 0))
                    if fit < TRIO_MIN_FIT or rows < TRIO_MIN_ROWS:
                        continue
                    if evidence.get("degenerate"):
                        continue
                    found.append(
                        (
                            fit,
                            rows,
                            {
                                "base": base_index,
                                "rate": rate_index,
                                "value": value_index,
                                "fit_score": round(fit, 4),
                                "rows_tested": rows,
                                "tax_scope": scope[0],
                                "tax_qualifiers": list(scope[1]),
                            },
                        )
                    )
        if not found:
            continue
        found.sort(key=lambda item: (item[0], item[1]), reverse=True)
        trios = [item[2] for item in found]
        disjoint = _disjoint_trio_count(trios)
        for trio in trios:
            trio["alternatives"] = disjoint
        proven[scope] = trios
    return proven


def _disjoint_trio_count(trios: Sequence[dict[str, object]]) -> int:
    """Conta os blocos de trios que não compartilham colunas.

    Trios com uma coluna em comum são tratados como o mesmo bloco. Isso ocorre,
    por exemplo, quando duas colunas de alíquota usam a mesma base e o mesmo valor.
    """
    blocks: list[set[int]] = []
    for trio in trios:
        columns = {int(trio["base"]), int(trio["rate"]), int(trio["value"])}
        if any(columns & block for block in blocks):
            continue
        blocks.append(columns)
    return len(blocks)


def trio_corroboration(
    trios: dict[tuple[str, tuple[str, ...]], list[dict[str, object]]],
    model_column_name: str,
    client_index: int,
) -> dict[str, object] | None:
    """Busca um trio que confirme o papel da coluna do cliente na coluna correspondente do modelo."""
    role = _target_role(model_column_name)
    if role not in _TRIO_ROLES:
        return None
    scope = _target_tax_scope(model_column_name)
    if scope is None:
        return None
    for trio in trios.get(scope, ()):
        if trio[role] == client_index:
            return trio
    return None


def _trio_proof_text(trio: dict[str, object] | None) -> str | None:
    """Descreve a prova aritmética, quando disponível.

    Se houver dois blocos independentes do mesmo tributo, a equação pode valer
    para ambos. Nesse caso, o texto informa que ela não basta para escolher a coluna.
    """
    if trio is None:
        return None
    tax_scope = str(trio["tax_scope"]).upper()
    text = PROOF_TEXT.format(
        tax_scope=tax_scope,
        rows_tested=trio["rows_tested"],
        fit_score=report_schema.format_decimal_br(trio["fit_score"]),
    )
    alternatives = int(trio.get("alternatives", 1))
    if alternatives > 1:
        text += PROOF_NOT_DECISIVE_SUFFIX.format(
            alternatives=alternatives, tax_scope=tax_scope
        )
    return text


def client_column_is_vacuous(table: SheetTable, column: ColumnRef) -> bool:
    """Verifica se todas as células da coluna estão vazias.

    Uma coluna preenchida com zeros contém dados e não é considerada vazia.
    Aceitar uma coluna sem nenhuma célula preenchida produziria uma coluna
    marcada como mapeada, mas sem dados na saída.
    """
    series = table.series(column)
    return int(series.notna().sum()) == 0 or not any(
        str(value).strip() for value in series.dropna()
    )


# ---------------------------------------------------------------------------
# Pontuação dos cabeçalhos
# ---------------------------------------------------------------------------


class HeaderScorer(Protocol):
    def scores(self, model_headers: Sequence[str], client_headers: Sequence[str]) -> np.ndarray:
        """Matriz com formato (cabeçalhos do modelo, cabeçalhos do cliente)."""


class LexicalHeaderScorer:
    """Avaliador de cabeçalhos offline, usado nos testes e nas execuções locais (--offline)."""

    def scores(self, model_headers: Sequence[str], client_headers: Sequence[str]) -> np.ndarray:
        matrix = np.zeros((len(model_headers), len(client_headers)), dtype=float)
        client_features = [
            (normalize_header(header), semantic_header_signature(header))
            for header in client_headers
        ]
        for i, target in enumerate(model_headers):
            left = normalize_header(target)
            left_tokens = semantic_header_signature(target)
            for j, (right, right_tokens) in enumerate(client_features):
                if left and left == right:
                    matrix[i, j] = IDENTICAL_HEADER_SCORE
                    continue
                union = left_tokens | right_tokens
                jaccard = len(left_tokens & right_tokens) / len(union) if union else 0.0
                sequence = SequenceMatcher(None, left, right).ratio()
                matrix[i, j] = LEXICAL_SEQUENCE_WEIGHT * sequence + LEXICAL_JACCARD_WEIGHT * jaccard
                if left and left_tokens and left_tokens == right_tokens:
                    matrix[i, j] = max(matrix[i, j], SEMANTIC_EQUIVALENCE_SCORE)
        return matrix


class OpenAIEmbeddingHeaderScorer:
    """Avaliador de cabeçalhos por embeddings da OpenAI (similaridade de cosseno).

    O prefixo enviado com cada cabeçalho fica em inglês, byte a byte: mudá-lo
    mudaria os vetores e as pontuações de nome.
    """

    def __init__(
        self,
        client: OpenAI,
        model: str = DEFAULT_EMBEDDING_MODEL,
        batch_size: int = 512,
    ) -> None:
        self.client = client
        self.model = model
        self.batch_size = batch_size

    def _embeddings(self, headers: Sequence[str]) -> np.ndarray:
        unique_headers = list(dict.fromkeys(headers))
        vectors: dict[str, list[float]] = {}
        for start in range(0, len(unique_headers), self.batch_size):
            batch = unique_headers[start : start + self.batch_size]
            response = self.client.embeddings.create(
                model=self.model,
                input=[f"Brazilian invoice spreadsheet column: {header}" for header in batch],
                encoding_format="float",
            )
            for header, item in zip(batch, response.data, strict=True):
                vectors[header] = item.embedding
        return np.asarray([vectors[header] for header in headers], dtype=float)

    def scores(self, model_headers: Sequence[str], client_headers: Sequence[str]) -> np.ndarray:
        model_vectors = self._embeddings(model_headers)
        client_vectors = self._embeddings(client_headers)
        model_norms = np.linalg.norm(model_vectors, axis=1, keepdims=True)
        client_norms = np.linalg.norm(client_vectors, axis=1, keepdims=True)
        if np.any(model_norms == 0) or np.any(client_norms == 0):
            raise ValueError("A OpenAI retornou um embedding de comprimento zero")
        similarities = (model_vectors / model_norms) @ (client_vectors / client_norms).T
        similarities = np.clip(similarities, 0.0, 1.0)

        # Cabeçalhos idênticos recebem 1,0. Assinaturas equivalentes elevam
        # o score até 0,98, preservando a diferença entre os dois casos.
        client_features = [
            (normalize_header(header), semantic_header_signature(header))
            for header in client_headers
        ]
        for i, target in enumerate(model_headers):
            left = normalize_header(target)
            left_tokens = semantic_header_signature(target)
            for j, (right, right_tokens) in enumerate(client_features):
                if left == right:
                    similarities[i, j] = IDENTICAL_HEADER_SCORE
                elif left_tokens and left_tokens == right_tokens:
                    similarities[i, j] = max(
                        similarities[i, j], SEMANTIC_EQUIVALENCE_SCORE
                    )
        return similarities


# ---------------------------------------------------------------------------
# Resolver de IA
# ---------------------------------------------------------------------------


class DataMappingDecision(BaseModel):
    client_column_id: str | None = Field(
        description="One candidate client_column_id, or null when none is compatible."
    )
    data_confidence: float = Field(ge=0.0, le=1.0)
    final_confidence: float = Field(ge=0.0, le=1.0)
    reason: str = Field(description="Short explanation grounded in names and sample values.")


class DataResolver(Protocol):
    def resolve(
        self,
        model_column: ColumnRef,
        model_values: pd.Series,
        candidates: Sequence[tuple[ColumnRef, pd.Series, float]],
        sample_size: int,
        relationship_evidence: dict[str, list[dict[str, object]]] | None = None,
        catalog_evidence: dict[str, list[dict[str, object]]] | None = None,
    ) -> DataMappingDecision:
        """Escolhe, entre os candidatos do cliente, a coluna para uma coluna do modelo (ou nenhuma)."""


class OpenAIDataResolver:
    """Consulta a OpenAI e recebe uma decisão no formato ``DataMappingDecision``.

    O prompt, o payload JSON e o schema de saída permanecem em inglês. O golden
    test verifica esses inputs; a justificativa retornada pelo modelo também
    é mantida no idioma original.
    """

    def __init__(self, client: OpenAI, model: str = DEFAULT_LLM_MODEL) -> None:
        self.client = client
        self.model = model

    def resolve(
        self,
        model_column: ColumnRef,
        model_values: pd.Series,
        candidates: Sequence[tuple[ColumnRef, pd.Series, float]],
        sample_size: int,
        relationship_evidence: dict[str, list[dict[str, object]]] | None = None,
        catalog_evidence: dict[str, list[dict[str, object]]] | None = None,
        *,
        target_profile: dict[str, object] | None = None,
        candidate_profiles: dict[str, dict[str, object]] | None = None,
    ) -> DataMappingDecision:
        if target_profile is None:
            target_profile = summarize_values(model_values, sample_size)
        if candidate_profiles is None:
            candidate_profiles = {
                column.id: summarize_values(values, sample_size)
                for column, values, _ in candidates
            }
        payload = {
            "model_column": {
                "name": model_column.name,
                "profile": target_profile,
            },
            "client_candidates": [
                {
                    "client_column_id": column.id,
                    "name": column.name,
                    "column_number": column.index + 1,
                    "name_confidence": round(name_confidence, 4),
                    "semantic_header_conflict": semantic_header_conflict(
                        model_column.name, column.name
                    ),
                    "profile_similarity": profile_similarity(
                        target_profile, candidate_profiles[column.id]
                    ),
                    "distinctive_sample_overlap": distinctive_sample_overlap(
                        target_profile, candidate_profiles[column.id]
                    ),
                    "relationship_evidence": (relationship_evidence or {}).get(
                        column.id, []
                    ),
                    "catalog_evidence": (catalog_evidence or {}).get(column.id, []),
                    "profile": candidate_profiles[column.id],
                }
                for column, values, name_confidence in candidates
            ],
        }
        response = self.client.responses.parse(
            model=self.model,
            store=False,
            temperature=0,
            instructions=(
                "Map one client spreadsheet column to one canonical Brazilian invoice column. "
                "The column names and name_confidence remain the primary semantic evidence throughout "
                "the decision; compare how close the candidate name scores are and never forget them "
                "after inspecting values. A client header can be bogus or misleading, so distinctive "
                "content and format evidence may override a weak name; generic numeric similarity "
                "alone is not enough. profile_similarity is a generic value-shape retrieval signal, "
                "not proof of a mapping. Then use representative "
                "values, inferred "
                "types, formats, lengths, and numeric scales. Recognize dates, monetary values, "
                "tax bases, tax rates, tax amounts, CFOP, NCM, CNPJ/CPF, invoice numbers, series, "
                "and 44-digit NF-e keys. First identify the semantic role of each header: a tax "
                "situation/code is not a tax base, rate, or amount even when all contain numbers. "
                "semantic_header_conflict identifies explicit tax-family or role disagreement; "
                "treat it as strong contrary evidence. A conflicting candidate may remain viable "
                "only when distinctive row content indicates that its header is bogus or wrong. "
                "Most canonical fields may be absent from a client export. Do not choose the best "
                "of bad candidates: return null unless the evidence supports the same business "
                "meaning. Matching only a broad type, generic numbers, zeros, dates, or strings is "
                "not semantic evidence. distinctive_sample_overlap reports exact overlap after "
                "excluding zeros and trivial short numbers; a positive value is strong row "
                "evidence and should be examined together with the headers. "
                "relationship_evidence contains conservative row-wise fiscal checks such as tax "
                "value approximately equaling base times rate, or line value approximately "
                "equaling quantity times unit price. A high score across many rows is strong "
                "evidence; a low score is contrary evidence, but legitimate adjustments, "
                "rounding, exemptions, credits, and reversals mean it is not an absolute rule. "
                "catalog_evidence reports membership in versioned official Brazilian fiscal-code "
                "tables. coverage is the fraction of all nonblank values found in the catalog; "
                "score also discounts short, collision-prone domains such as two-digit CST. High "
                "coverage in CFOP, NCM, municipality, or cClassTrib is distinctive content evidence. "
                "CST values overlap across taxes, especially PIS and COFINS, so use the header and "
                "tax-family context to disambiguate them. all_zero_only is not useful evidence. "
                "State, municipal, and federal registrations are distinct and cannot be selected "
                "only because their values have similar shapes. CPF/CNPJ values may contain 11 or "
                "14 digits, may include punctuation, and spreadsheet numeric cells may lose a "
                "leading zero. Prefer a candidate that explicitly names the compatible identifier "
                "over a generic document-number field. When candidates are otherwise close, prefer "
                "the header with the same semantic role and no unrelated extra qualifier. CFOP may "
                "appear as four digits (5102) or with an ERP suffix "
                "(5102/AA); those formats are compatible. Do not choose a candidate merely because "
                "both are numeric. A profile whose evidence_quality is empty or all_zero provides "
                "little or no row evidence: zeros neither confirm a mapping nor contradict a strong "
                "name match. When row evidence is weak, keep final_confidence close to the relevant "
                "name_confidence. "
                "Return null when the data does not support a mapping. final_confidence must reflect "
                "both the name and data evidence. Keep the reason to one sentence."
            ),
            input=json.dumps(payload, ensure_ascii=False),
            text_format=DataMappingDecision,
        )
        if response.output_parsed is None:
            raise RuntimeError("A OpenAI não retornou uma decisão de mapeamento interpretável")
        decision = response.output_parsed
        selected = next(
            (
                (name_confidence, candidate_profiles[column.id])
                for column, _, name_confidence in candidates
                if column.id == decision.client_column_id
            ),
            None,
        )
        if selected is not None:
            # Sem evidência nas linhas, a confiança devolvida não cai abaixo do nome.
            name_confidence, selected_profile = selected
            weak_data = (
                target_profile["evidence_quality"] in _WEAK_EVIDENCE
                or selected_profile["evidence_quality"] in _WEAK_EVIDENCE
            )
            if weak_data and decision.final_confidence < name_confidence:
                decision = decision.model_copy(update={"final_confidence": name_confidence})
        return decision


# ---------------------------------------------------------------------------
# Catálogos oficiais de códigos
# ---------------------------------------------------------------------------


def _catalog_score(evidence: Sequence[dict[str, object]]) -> float:
    best = best_catalog_evidence(evidence)
    return float(best["score"]) if best is not None else 0.0


def _catalog_label(catalog: object) -> str:
    """Retorna o nome do catálogo usado nas mensagens, como ``CFOP`` para ``cfop``."""
    return report_schema.CATALOG_LABELS.get(str(catalog), str(catalog))


def catalog_evidence_is_distinctive(
    evidence: Mapping[str, object] | None,
    *,
    final_threshold: float,
) -> bool:
    """Verifica se a evidência de catálogo pode identificar o tipo da coluna.

    Catálogos com especificidade 1,0 usam ``DISTINCTIVE_CATALOG_FLOOR`` como
    score mínimo. Os demais precisam alcançar ``final_threshold``; com o
    padrão de 0,90, nenhum deles atinge esse valor. A aceitação final ainda
    depende das regras de confiança e dos guards.
    """
    if evidence is None:
        return False
    specificity = float(evidence["specificity"])
    score = float(evidence["score"])
    if bool(evidence["all_zero_only"]):
        return False
    if (
        int(evidence["nonempty_count"]) < CATALOG_MIN_VALUES
        or float(evidence["format_fraction"]) < CATALOG_MIN_FORMAT_FRACTION
    ):
        return False
    if specificity >= UNIQUE_DOMAIN_SPECIFICITY:
        return (
            score >= DISTINCTIVE_CATALOG_FLOOR
            and int(evidence["unique_valid_count"]) >= DISTINCTIVE_CATALOG_MIN_CODES
        )
    return score >= final_threshold


def _catalog_contradicts_accept(evidence: Mapping[str, object]) -> bool:
    """Detecta uma coluna de códigos bem formatados, mas quase todos ausentes do catálogo específico."""
    return (
        float(evidence["specificity"]) >= AUDIT_MIN_SPECIFICITY
        and int(evidence["nonempty_count"]) >= CATALOG_MIN_VALUES
        and float(evidence["format_fraction"]) >= CATALOG_MIN_FORMAT_FRACTION
        and float(evidence["coverage"]) < AUDIT_MAX_COVERAGE
        and not bool(evidence["all_zero_only"])
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
            distinct_coverage=report_schema.format_percent_br(
                float(evidence.get("distinct_coverage", evidence["coverage"]))
            ),
            unique_formatted_count=int(evidence["unique_formatted_count"]),
            coverage=report_schema.format_percent_br(float(evidence["coverage"])),
        ),
    )


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
    arithmetic_proof: str | None = None,
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
    if relationship_score > 0.0 or report_schema.proof_is_decisive(arithmetic_proof):
        steps.append("arithmetic")
    return steps


@dataclass
class MappingResult:
    """Representa a decisão de uma coluna do modelo na aba Mapeamentos.

    ``status`` usa os valores internos ``accepted``, ``review`` e ``no_match``;
    ``report_schema`` os converte ao gravar o arquivo. A ordem dos campos define
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
    #: Evidências e guard registrados para a decisão (ver mapping_steps.py).
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
    proof = _trio_proof_text(
        trio_corroboration(run.client_tax_trios, model_column.name, client_index)
    )
    steps = _supporting_step_ids(header_step=run.header_step, arithmetic_proof=proof, **scores)
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


def _target_role(header: str) -> str | None:
    definition = target_field(header)
    return definition.role if definition is not None else header_semantic_role(header)


def _target_tax_scope(header: str) -> tuple[str, tuple[str, ...]] | None:
    definition = target_field(header)
    return definition.tax_scope if definition is not None else tax_calculation_scope(header)


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
    if choice.column is None or choice.decision.final_confidence == 0.0:
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
    """Reforça a confiança quando o cabeçalho e a relação fiscal apoiam a mesma escolha.

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
                "data_confidence": max(decision.data_confidence, relationship),
                "final_confidence": max(decision.final_confidence, round(joint_score, 4)),
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
            client_column=report_schema.display_name_to_external(chosen.display_name)
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


# ---------------------------------------------------------------------------
# Abas auxiliares: colunas do cliente sem uso e fila de revisão
# ---------------------------------------------------------------------------


@dataclass
class UnmatchedColumn:
    """Descreve uma coluna do cliente que não foi usada em um mapeamento aceito.

    Inclui colunas citadas apenas em propostas pendentes de revisão. ``signals``
    contém IDs internos separados por vírgula; ``signal_detail`` contém a
    explicação em português exibida na aba Colunas do Cliente sem Uso.
    """

    client_column_number: int
    client_column: str
    disposition: str
    review_priority: str
    rows_with_value: int
    distinct_values: int
    signals: str
    signal_detail: str
    closest_model_column: str
    closest_name_confidence: float


def _unmatched_signals(
    *,
    column: ColumnRef,
    profile: dict[str, object],
    values: pd.Series,
    reference_catalogs: ReferenceCatalogs | None,
    trio_slots: dict[int, str],
) -> tuple[list[str], str]:
    """Identifica sinais nos dados que ajudam a priorizar uma coluna ainda sem uso.

    Considera trios aritméticos, catálogos, dígitos verificadores e datas, sem
    filtrar pelas palavras do cabeçalho. Retorna os IDs dos sinais e suas
    descrições em português.
    """
    signals: list[str] = []
    details: list[str] = []

    slot = trio_slots.get(column.index)
    if slot is not None:
        signals.append("arithmetic")
        details.append(
            SIGNAL_TRIO.format(slot=report_schema.TEXT_LABELS["trio_slot"].get(slot, slot))
        )

    if reference_catalogs is not None:
        evidence = best_catalog_evidence(
            [
                reference_catalogs.analyze(values, name)
                for name in reference_catalogs.catalogs
            ]
        )
        if evidence is not None and catalog_evidence_is_distinctive(
            evidence, final_threshold=UNMATCHED_CATALOG_THRESHOLD
        ):
            signals.append("catalog")
            details.append(
                SIGNAL_CATALOG.format(
                    distinct_coverage=report_schema.format_percent_br(
                        float(evidence["distinct_coverage"])
                    ),
                    unique_formatted_count=int(evidence["unique_formatted_count"]),
                    catalog=_catalog_label(evidence["catalog"]),
                )
            )

    for kind in ("nfe_key", "cnpj", "cpf"):
        fraction = float(profile.get(f"valid_{kind}_fraction", 0.0) or 0.0)
        if fraction >= IDENTIFIER_MIN_VALID_FRACTION:
            signals.append(f"{kind}_checkdigit")
            details.append(
                SIGNAL_CHECKDIGIT.format(
                    fraction=report_schema.format_percent_br(fraction),
                    kind=report_schema.TEXT_LABELS["identifier_kind"].get(kind, kind),
                )
            )
            break

    if profile.get("inferred_type") == "datetime":
        signals.append("date")
        details.append(SIGNAL_DATES)

    if not signals:
        quality = profile.get("evidence_quality")
        if quality == "empty":
            details.append(SIGNAL_EMPTY)
        elif quality == "all_zero":
            details.append(SIGNAL_ALL_ZERO)
        else:
            details.append(SIGNAL_NO_SIGNAL)
    return signals, "; ".join(details)


def summarize_unmatched_columns(
    *,
    model_table: SheetTable,
    client_table: SheetTable,
    results: dict[int, MappingResult],
    similarities: np.ndarray,
    reference_catalogs: ReferenceCatalogs | None,
    client_tax_trios: dict[tuple[str, tuple[str, ...]], list[dict[str, object]]],
    sample_size: int,
) -> list[UnmatchedColumn]:
    """Lista todas as colunas sem uso, colocando as prioritárias primeiro.

    Colunas sem sinais relevantes continuam na lista, com prioridade menor.
    No modo offline, as propostas da segunda passagem ficam em revisão, o que
    pode aumentar a quantidade de colunas na lista prioritária.
    """
    consumed: set[int] = set()
    referenced: set[int] = set()
    for result in results.values():
        if result.client_column_number is None:
            continue
        index = result.client_column_number - 1
        referenced.add(index)
        if result.status == "accepted":
            consumed.add(index)

    trio_slots: dict[int, str] = {}
    for scope_trios in client_tax_trios.values():
        for trio in scope_trios:
            for slot in ("base", "rate", "value"):
                trio_slots[int(trio[slot])] = slot

    rows: list[UnmatchedColumn] = []
    for index, column in enumerate(client_table.columns):
        if index in consumed:
            continue
        values = client_table.series(column)
        profile = summarize_values(values, sample_size)
        signals, detail = _unmatched_signals(
            column=column,
            profile=profile,
            values=values,
            reference_catalogs=reference_catalogs,
            trio_slots=trio_slots,
        )
        informative = profile["evidence_quality"] == "informative"
        disposition = (
            "proposed_not_accepted" if index in referenced else "never_referenced"
        )
        # Uma proposta não precisa de sinal: o mapeador já produziu evidência para ela.
        shortlist = informative and (
            bool(signals) or disposition == "proposed_not_accepted"
        )
        best_model = int(np.argmax(similarities[:, index]))
        rows.append(
            UnmatchedColumn(
                client_column_number=index + 1,
                client_column=column.display_name,
                disposition=disposition,
                review_priority="shortlist" if shortlist else "low",
                rows_with_value=int(profile["non_empty_count"]),
                distinct_values=int(profile["unique_count"]),
                signals=",".join(signals),
                signal_detail=detail,
                closest_model_column=model_table.columns[best_model].name,
                closest_name_confidence=round(float(similarities[best_model, index]), 4),
            )
        )
    rows.sort(key=lambda row: (row.review_priority != "shortlist", row.client_column_number))
    return rows


@dataclass
class AdjudicationRow:
    """Representa uma proposta pendente na aba Fila de Revisão.

    A fila organiza a consulta por coluna do modelo. O parecer deve ser
    preenchido na aba Mapeamentos, que o apply usa ao gerar a saída.
    ``why_held`` e ``resolver_disagrees`` já contêm texto em português.
    """

    rank: int
    model_column_number: int
    model_column: str
    proposed_client_column_number: int | None
    proposed_client_column: str
    final_confidence: float
    gate_distance: float
    name_margin: float
    rows_with_value: int
    resolver_disagrees: str
    arithmetic_proof: str
    client_sample_values: str
    why_held: str


def summarize_adjudication_queue(
    *,
    client_table: SheetTable,
    results: Sequence[MappingResult],
    final_threshold: float,
    sample_size: int,
) -> list[AdjudicationRow]:
    """Ordena as propostas em revisão pela proximidade dos critérios de aceitação."""
    rows: list[AdjudicationRow] = []
    for result in results:
        if result.status == "accepted" or result.client_column_number is None:
            continue
        index = result.client_column_number - 1
        if index < 0 or index >= len(client_table.columns):
            continue
        column = client_table.columns[index]
        profile = summarize_values(client_table.series(column), sample_size)
        final = float(result.final_confidence or 0.0)
        if final <= 0.0:
            continue
        # Confirmar uma proposta sobre uma coluna vazia entregaria uma coluna
        # em branco; ela fica na fila, mas no fim.
        vacuous = client_column_is_vacuous(client_table, column)
        llm_final = result.llm_final_confidence
        disagrees = (
            RESOLVER_DISAGREES.format(llm_final=report_schema.format_decimal_br(llm_final))
            if llm_final is not None and float(llm_final) < final - RESOLVER_DISAGREEMENT_GAP
            else ""
        )
        # Scores limitados a 0,8999 não permitem comparar a força das propostas entre si.
        clamped = abs(final - (final_threshold - CEILING_GAP)) < FLOAT_TOLERANCE
        rows.append(
            AdjudicationRow(
                rank=0,
                model_column_number=result.model_column_number,
                model_column=result.model_column,
                proposed_client_column_number=result.client_column_number,
                proposed_client_column=result.client_column or "",
                final_confidence=round(final, 4),
                gate_distance=round(final_threshold - final, 4),
                name_margin=round(float(result.name_margin or 0.0), 4),
                rows_with_value=int(profile["non_empty_count"]),
                resolver_disagrees=disagrees,
                arithmetic_proof=result.arithmetic_proof or "",
                client_sample_values=" | ".join(
                    str(value) for value in profile["samples"][:6]
                ),
                why_held=(
                    WHY_HELD_VACUOUS
                    if vacuous
                    else WHY_HELD_NAME_ONLY
                    if clamped
                    else WHY_HELD_BELOW_THRESHOLD
                ),
            )
        )
    # Colunas preenchidas primeiro, depois a confiança; a margem só desempata
    # dentro de uma faixa de confiança (na prática, o teto).
    rows.sort(
        key=lambda row: (
            row.rows_with_value > 0,
            round(row.final_confidence / _CLAMPED_BAND),
            row.name_margin,
            row.rows_with_value,
        ),
        reverse=True,
    )
    for position, row in enumerate(rows, 1):
        row.rank = position
    return rows


# ---------------------------------------------------------------------------
# Gravação do relatório
# ---------------------------------------------------------------------------


def file_sha256(path: str | Path) -> str:
    """Calcula o SHA-256 do arquivo lendo blocos de 1 MiB."""
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _external_rows(
    rows: Sequence[UnmatchedColumn] | Sequence[AdjudicationRow],
    row_type: type,
    table: str,
) -> pd.DataFrame:
    """Converte as linhas auxiliares para o relatório; se a lista estiver vazia, preserva os cabeçalhos."""
    frame = pd.DataFrame(
        [asdict(row) for row in rows]
        or [{field: "" for field in row_type.__annotations__}]
    )
    return report_schema.to_external_frame(frame, table)


def write_results(
    results: Sequence[MappingResult],
    output_path: str | Path,
    metadata: dict[str, object] | None = None,
    unmatched: Sequence[UnmatchedColumn] | None = None,
    queue: Sequence[AdjudicationRow] | None = None,
) -> None:
    """Grava o relatório em XLSX ou CSV, usando o schema em português.

    ``report_schema`` converte os nomes e valores estruturados. As mensagens
    já chegam no idioma em que devem ser gravadas. O CSV contém apenas
    a tabela Mapeamentos.
    """
    output = Path(output_path)
    frame = pd.DataFrame([asdict(result) for result in results])
    for column in REVIEWER_COLUMNS:
        frame[column] = ""
    frame = report_schema.to_external_frame(frame, "mappings")
    if output.suffix.lower() == ".csv":
        frame.to_csv(output, index=False)
    elif output.suffix.lower() == ".xlsx":
        with pd.ExcelWriter(output, engine="openpyxl") as writer:
            frame.to_excel(writer, sheet_name=report_schema.sheet_name("mappings"), index=False)
            if metadata:
                report_schema.to_external_metadata(metadata).to_excel(
                    writer, sheet_name=report_schema.sheet_name("run_metadata"), index=False
                )
            # Mantém Mapeamentos na posição 0, usada pelo apply. As abas auxiliares
            # são gravadas mesmo sem registros, indicando que a análise foi
            # feita e não encontrou itens para listar.
            if unmatched is not None:
                _external_rows(unmatched, UnmatchedColumn, "unmatched").to_excel(
                    writer, sheet_name=report_schema.sheet_name("unmatched"), index=False
                )
            if queue is not None:
                _external_rows(queue, AdjudicationRow, "queue").to_excel(
                    writer, sheet_name=report_schema.sheet_name("queue"), index=False
                )
            # Mantém os scores numéricos; o Excel os apresenta como porcentagem.
            for sheet in writer.sheets.values():
                for header in sheet[1]:
                    if str(header.value).startswith("confianca_"):
                        for row in sheet.iter_rows(min_row=2, min_col=header.column,
                                                   max_col=header.column):
                            row[0].number_format = "0.00%"
    else:
        raise ValueError("A saída deve terminar em .xlsx ou .csv")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _sheet_argument(value: str) -> str | int:
    """Interpreta dígitos como índice de aba a partir de zero e os demais valores como nome."""
    return int(value) if value.isdigit() else value


def build_parser() -> PortugueseArgumentParser:
    """Configura os argumentos e a ajuda da CLI."""
    parser = PortugueseArgumentParser(
        description=(
            "Identifica as colunas do cliente correspondentes aos campos de model.xlsx "
            "e gera um relatório para revisão e aplicação do mapeamento."
        )
    )
    parser.add_argument(
        "client", metavar="EXPORTACAO", help="exportação .xlsx do cliente"
    )
    parser.add_argument(
        "--model",
        default="data/model.xlsx",
        metavar="ARQUIVO",
        help="planilha do modelo base (padrão: %(default)s)",
    )
    parser.add_argument(
        "--output",
        metavar="ARQUIVO",
        help=(
            "caminho do relatório de saída, em .xlsx ou .csv "
            "(padrão: mapping_<nome do arquivo do cliente>.xlsx)"
        ),
    )
    parser.add_argument(
        "--model-sheet",
        default="0",
        metavar="ABA",
        help="índice (a partir de 0) ou nome da aba do modelo (padrão: %(default)s)",
    )
    parser.add_argument(
        "--client-sheet",
        default="0",
        metavar="ABA",
        help="índice (a partir de 0) ou nome da aba do cliente (padrão: %(default)s)",
    )
    parser.add_argument(
        "--model-header-row",
        type=int,
        default=1,
        metavar="LINHA",
        help="linha do cabeçalho na aba do modelo, contada a partir de 1 (padrão: %(default)s)",
    )
    parser.add_argument(
        "--client-header-row",
        type=int,
        default=1,
        metavar="LINHA",
        help="linha do cabeçalho na aba do cliente, contada a partir de 1 (padrão: %(default)s)",
    )
    parser.add_argument(
        "--name-threshold",
        type=float,
        default=MapperConfig.name_threshold,
        metavar="VALOR",
        help=(
            "score mínimo do cabeçalho para aceitação na primeira passagem, "
            "de 0 a 1 (padrão: %(default)s)"
        ),
    )
    parser.add_argument(
        "--name-margin",
        type=float,
        default=MapperConfig.name_margin_threshold,
        metavar="VALOR",
        help=(
            "margem mínima entre os scores das duas melhores candidatas pelo "
            "cabeçalho, de 0 a 1 (padrão: %(default)s)"
        ),
    )
    parser.add_argument(
        "--final-threshold",
        type=float,
        default=MapperConfig.final_threshold,
        metavar="VALOR",
        help="threshold da confiança final para aceitação, de 0 a 1 (padrão: %(default)s)",
    )
    parser.add_argument(
        "--candidates",
        type=int,
        default=MapperConfig.candidate_count,
        metavar="N",
        help=(
            "quantidade de candidatos por critério na segunda passagem "
            "(padrão: %(default)s)"
        ),
    )
    parser.add_argument(
        "--sample-size",
        type=int,
        default=MapperConfig.sample_size,
        metavar="N",
        help=(
            "quantidade de valores de amostra por coluna em cada perfil "
            "(padrão: %(default)s)"
        ),
    )
    parser.add_argument(
        "--embedding-model",
        default=os.getenv("OPENAI_EMBEDDING_MODEL", DEFAULT_EMBEDDING_MODEL),
        metavar="MODELO",
        help=(
            "modelo de embeddings da OpenAI usado para comparar os cabeçalhos "
            "(padrão: %(default)s; a variável OPENAI_EMBEDDING_MODEL, se definida, "
            "substitui o padrão)"
        ),
    )
    parser.add_argument(
        "--llm-model",
        default=os.getenv("OPENAI_MODEL", DEFAULT_LLM_MODEL),
        metavar="MODELO",
        help=(
            "modelo de linguagem da OpenAI usado pelo resolver "
            "(padrão: %(default)s; a variável OPENAI_MODEL, se definida, substitui o "
            "padrão)"
        ),
    )
    parser.add_argument(
        "--reference-data",
        default=str(Path(__file__).with_name("reference_data")),
        metavar="DIRETORIO",
        help=(
            "diretório com o manifest.json e os catálogos versionados de códigos "
            "fiscais (padrão: a pasta reference_data ao lado deste programa)"
        ),
    )
    parser.add_argument(
        "--no-reference-data",
        action="store_true",
        help="desativa a evidência dos catálogos oficiais de códigos fiscais",
    )
    parser.add_argument(
        "--offline",
        action="store_true",
        help=(
            "compara os cabeçalhos pelo texto, sem chamadas à OpenAI; "
            "as propostas da segunda passagem seguem para revisão"
        ),
    )
    return parser


def _error_message(exc: ValueError | OSError) -> str:
    """Formata a mensagem de erro de leitura ou gravação exibida pela CLI."""
    if isinstance(exc, FileNotFoundError):
        return f"Arquivo não encontrado: {exc.filename}"
    if isinstance(exc, PermissionError):
        return f"Sem permissão para acessar o arquivo: {exc.filename}"
    if isinstance(exc, OSError):
        target = f" {exc.filename}" if exc.filename else ""
        return f"Não foi possível ler ou gravar o arquivo{target}: {exc.strerror or exc}"
    return f"Não foi possível gerar o relatório de mapeamento: {exc}"


def main(argv: Sequence[str] | None = None) -> int:
    """Ponto de entrada da CLI; devolve 0 em caso de sucesso.

    Erros de valor e de arquivo saem com uma mensagem em português e código 1,
    sem traceback.
    """
    args = build_parser().parse_args(argv)
    if not 0.0 <= args.name_threshold <= 1.0:
        raise SystemExit("--name-threshold deve estar entre 0 e 1")
    if not 0.0 <= args.name_margin <= 1.0:
        raise SystemExit("--name-margin deve estar entre 0 e 1")
    if not 0.0 <= args.final_threshold <= 1.0:
        raise SystemExit("--final-threshold deve estar entre 0 e 1")
    if args.candidates < 1 or args.sample_size < 1:
        raise SystemExit("--candidates e --sample-size devem ser positivos")
    if args.model_header_row < 1 or args.client_header_row < 1:
        raise SystemExit("--model-header-row e --client-header-row devem ser positivos")
    try:
        return _run(args)
    except (ValueError, OSError) as exc:
        raise SystemExit(_error_message(exc)) from exc


def _run(args) -> int:
    """Executa o mapeamento com os argumentos já validados e grava o relatório."""
    model_path = Path(args.model)
    client_path = Path(args.client)
    output_path = Path(args.output or f"mapping_{client_path.stem}.xlsx")

    model_table = SheetTable.from_excel(
        model_path,
        _sheet_argument(args.model_sheet),
        args.model_header_row - 1,
    )
    client_table = SheetTable.from_excel(
        client_path,
        _sheet_argument(args.client_sheet),
        args.client_header_row - 1,
    )
    reference_catalogs: ReferenceCatalogs | None = None
    if not args.no_reference_data:
        try:
            reference_catalogs = ReferenceCatalogs.from_directory(args.reference_data)
        except (OSError, ValueError, KeyError, json.JSONDecodeError) as exc:
            raise SystemExit(
                f"Não foi possível carregar os catálogos de referência de "
                f"{args.reference_data}: {exc}. Execute update_reference_data.py ou "
                "use --no-reference-data."
            ) from exc

    if args.offline:
        header_scorer: HeaderScorer = LexicalHeaderScorer()
        data_resolver: DataResolver | None = None
    else:
        if not os.getenv("OPENAI_API_KEY"):
            raise SystemExit(
                "OPENAI_API_KEY não está definida. Exporte uma chave válida ou use --offline."
            )
        openai_client = OpenAI()
        header_scorer = OpenAIEmbeddingHeaderScorer(
            openai_client,
            model=args.embedding_model,
        )
        data_resolver = OpenAIDataResolver(openai_client, model=args.llm_model)

    run = _start_run(
        model_table=model_table,
        client_table=client_table,
        header_scorer=header_scorer,
        config=MapperConfig(
            name_threshold=args.name_threshold,
            name_margin_threshold=args.name_margin,
            final_threshold=args.final_threshold,
            candidate_count=args.candidates,
            sample_size=args.sample_size,
        ),
        reference_catalogs=reference_catalogs,
        online=data_resolver is not None,
    )
    results = _map_run(run, data_resolver)
    manifest_path = Path(args.reference_data) / "manifest.json"
    # Chaves e sentinelas internos (offline, disabled); write_results os grava em português.
    metadata = {
        "mapping_schema_version": MAPPING_SCHEMA_VERSION,
        "generated_at_utc": datetime.now(UTC).isoformat(),
        "prompt_version": PROMPT_VERSION,
        "client_file": client_path.name,
        "client_sha256": file_sha256(client_path),
        "model_file": model_path.name,
        "model_sha256": file_sha256(model_path),
        "client_sheet": args.client_sheet,
        "model_sheet": args.model_sheet,
        "client_header_row": args.client_header_row,
        "model_header_row": args.model_header_row,
        "embedding_model": "offline" if args.offline else args.embedding_model,
        "llm_model": "offline" if args.offline else args.llm_model,
        "name_threshold": args.name_threshold,
        "name_margin": args.name_margin,
        "final_threshold": args.final_threshold,
        "candidate_count": args.candidates,
        "sample_size": args.sample_size,
        "reference_manifest_sha256": (
            file_sha256(manifest_path)
            if not args.no_reference_data and manifest_path.exists()
            else "disabled"
        ),
    }
    # A sugestão da coluna mais próxima usa comparação textual para evitar
    # novas chamadas à API de embeddings apenas para esse dado auxiliar.
    unmatched = summarize_unmatched_columns(
        model_table=model_table,
        client_table=client_table,
        results={index: result for index, result in enumerate(results)},
        similarities=LexicalHeaderScorer().scores(
            [column.name for column in model_table.columns],
            [column.name for column in client_table.columns],
        ),
        reference_catalogs=reference_catalogs,
        client_tax_trios=run.client_tax_trios,
        sample_size=args.sample_size,
    )
    queue = summarize_adjudication_queue(
        client_table=client_table,
        results=results,
        final_threshold=args.final_threshold,
        sample_size=args.sample_size,
    )
    write_results(
        results, output_path, metadata=metadata, unmatched=unmatched, queue=queue
    )

    accepted = sum(result.status == "accepted" for result in results)
    review = sum(result.status == "review" for result in results)
    no_match = sum(result.status == "no_match" for result in results)
    print(f"Arquivo gravado: {output_path}")
    print(f"aceito={accepted} revisao={review} sem_correspondencia={no_match}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
