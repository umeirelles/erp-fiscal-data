"""Mapeador em duas passagens das colunas da planilha de notas fiscais do cliente para as colunas do model.xlsx.

O código pensa em inglês nos valores de máquina (situação ``accepted``, ids de
etapa como ``semantic``, nome de exibição ``Nome (occurrence 2)``) e escreve
em português todas as frases que uma pessoa lê: justificativa, prova
aritmética, detalhe dos sinais, motivo de retenção e mensagens. A tradução dos
nomes de aba, cabeçalhos, valores enumerados e chaves de metadados acontece só
na gravação, em ``report_schema``.

O que é entrada dos modelos de IA continua em inglês, idêntico byte a byte: o
prompt do ``OpenAIDataResolver``, o prefixo do embedding, o esquema
``DataMappingDecision``, o payload enviado ao resolvedor, o texto de
``semantic_header_conflict`` e ``ColumnRef.name`` / ``ColumnRef.display_name``.
Mudar qualquer um deles mudaria as decisões online; o teste
``tests/test_model_input_golden.py`` garante que isso não acontece.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sys
import unicodedata
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
from mapping_steps import deciding_step, format_steps
from reference_catalog import ReferenceCatalogs, best_catalog_evidence, expected_catalogs


DEFAULT_EMBEDDING_MODEL = "text-embedding-3-large"
DEFAULT_LLM_MODEL = "gpt-4o-mini"
SEMANTIC_EQUIVALENCE_SCORE = 0.98
#: Versão do formato do relatório. A 3 é o esquema em português (abas,
#: cabeçalhos, valores e chaves de metadados); a 2, o antigo, não é mais lida.
MAPPING_SCHEMA_VERSION = 3
PROMPT_VERSION = "2026-08-04.2"
#: Colunas que o revisor preenche na aba de mapeamentos. Os nomes são os
#: internos; o cabeçalho em português sai de ``report_schema`` na gravação.
REVIEWER_COLUMNS = ["reviewer_status", "correct_client_column", "reviewer_note"]

_HEADER_STOPWORDS = {
    "44",
    "a",
    "as",
    "da",
    "das",
    "de",
    "do",
    "dos",
    "digit",
    "for",
    "of",
    "o",
    "os",
    "para",
    "the",
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


def normalize_header(value: object) -> str:
    text = "" if value is None else str(value).strip()
    text = unicodedata.normalize("NFKD", text)
    text = text.encode("ascii", "ignore").decode("ascii").lower()
    return re.sub(r"[^a-z0-9]+", " ", text).strip()


def semantic_header_signature(value: object) -> frozenset[str]:
    """Normaliza termos reutilizáveis em português e inglês, sem regras do layout de um cliente."""
    normalized = normalize_header(value)
    # CST é a abreviação brasileira padrão do código de situação tributária.
    # Esta troca é vocabulário do domínio, não um de-para aprendido de um cliente.
    normalized = re.sub(r"\btax\s+situation\b", "cst", normalized)
    normalized = re.sub(r"\bsituacao\s+tributaria\b", "cst", normalized)
    # No Livro Registro de Entradas/Saídas - e no campo VL_DOC do registro
    # C100 da EFD ICMS/IPI - "valor contábil" É o valor total do documento
    # fiscal. As colunas 40 a 51 do modelo são o bloco de valores desse livro,
    # então isto diz o que o modelo base significa, não como um cliente o
    # chama. A palavra de valor é essencial: é ela que deixa de fora "Conta
    # Contábil", "Lançamento Contábil" e "Data Contábil", porque cada um
    # mantém uma palavra que a coluna do modelo não tem.
    normalized = re.sub(r"\b(?:valor|vlr|val|vl)\s+contab\w*", "valor total nf", normalized)
    tokens = []
    for token in normalized.split():
        if token in _HEADER_STOPWORDS:
            continue
        tokens.append(_HEADER_TOKEN_ALIASES.get(token, token))
    # Alguns ERPs truncam PIS para PI nos cabeçalhos. Só expande quando o
    # contexto de CST (situação tributária) deixa o sentido inequívoco.
    if "cst" in tokens:
        tokens = ["pis" if token == "pi" else token for token in tokens]
    return frozenset(tokens)


def semantically_equivalent_headers(left: object, right: object) -> bool:
    left_signature = semantic_header_signature(left)
    return bool(left_signature) and left_signature == semantic_header_signature(right)


def underspecified_header_match(model_header: object, client_header: object) -> bool:
    """Diz se o cabeçalho do cliente perde todos os conceitos do modelo, menos um.

    Uma palavra genérica sozinha, como ``Centro``, serve como proposta para
    ``Centro custo``, mas não basta para o aceite automático. Evidência
    distintiva nas linhas ou no catálogo ainda pode promover a proposta na
    segunda passagem.
    """
    model_signature = semantic_header_signature(model_header)
    client_signature = semantic_header_signature(client_header)
    return (
        len(model_signature) > 1
        and len(client_signature) == 1
        and client_signature < model_signature
    )


_TAX_FAMILY_ALIASES = {
    "cof": "cofins",
    "cofi": "cofins",
    "fcp": "fecp",
    "ibss": "ibs",
}

_TAX_FAMILIES = {
    "cbs",
    "cofins",
    "csll",
    "fecp",
    "icap",
    "icep",
    "icms",
    "icsp",
    "ibs",
    "ibsm",
    "inss",
    "ipi",
    "irpj",
    "iss",
    "iva",
    "pis",
}


def header_tax_families(value: object) -> frozenset[str]:
    """Famílias de tributo citadas explicitamente num cabeçalho, pelo vocabulário do domínio."""
    families = {
        _TAX_FAMILY_ALIASES.get(token, token)
        for token in semantic_header_signature(value)
    }
    return frozenset(families & _TAX_FAMILIES)


#: Palavras de papel agrupadas pelo conceito que nomeiam. Todo ERP trunca os
#: cabeçalhos - o SAP em cerca de 20 caracteres, o Datasul em 12 -, então a
#: palavra de papel chega cortada na maioria das vezes: "Vlr Isen ICM",
#: "Vlr Outr IPI".
#:
#: Estes grupos não têm prefixo ambíguo: medido nos tamanhos de 2 a 6, nenhum
#: prefixo de palavra daqui alcança dois conceitos diferentes. É isso que torna
#: a expansão segura por construção, e não por ajuste fino; um teste verifica a
#: propriedade, para que um acréscimo futuro não a quebre sem ninguém notar.
_ROLE_CONCEPT_WORDS: dict[str, frozenset[str]] = {
    "other": frozenset({"outra", "outras", "outro", "outros", "other"}),
    "exempt": frozenset({"isenta", "isentas", "isento", "isentos", "exempt"}),
    "reduced": frozenset({"reduzida", "reduzido", "reduced"}),
}

#: Abaixo deste tamanho, uma palavra é ruído, não truncamento.
_MIN_ROLE_PREFIX = 3


def _role_prefix_index() -> dict[str, str]:
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
_ROLE_CANONICAL = {
    "other": "outras",
    "exempt": "isentas",
    "reduced": "reduzida",
}


def expand_truncated_role_words(tokens: set[str]) -> set[str]:
    """Acrescenta a palavra de papel completa às palavras que são truncamentos dela.

    Um prefixo só se expande quando alcança exatamente um conceito, então isto
    não transforma um "outbound" em "outras" por alcançar dois sentidos ao
    mesmo tempo - só erra se uma palavra que não é truncamento por acaso for
    prefixo de uma palavra de papel e de mais nenhuma. A expansão é aditiva: a
    palavra original fica, então nada que já funcionava deixa de funcionar.
    """
    known = {word for words in _ROLE_CONCEPT_WORDS.values() for word in words}
    expanded = set(tokens)
    for token in tokens:
        if token in known or len(token) < _MIN_ROLE_PREFIX:
            continue
        concept = _ROLE_PREFIXES.get(token)
        if concept is not None:
            expanded.add(_ROLE_CANONICAL[concept])
    return expanded


def header_semantic_role(value: object) -> str | None:
    """Papel fiscal explícito no cabeçalho, sem adivinhar pelos valores das linhas."""
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
    # O nome de uma família de tributo pode contrabandear um sinal de alíquota
    # para a assinatura: DIF_ALIQ gera a palavra ``aliq``, que a tabela de
    # sinônimos expande para ``aliquota``, então toda coluna desse bloco -
    # Base, Valor, Isentas, Outras, Base Reduzida - tinha papel ``rate`` antes
    # desta proteção. Quando o cabeçalho também diz uma posição explícita, a
    # palavra explícita vence; a leitura como alíquota sobrevive onde é a única
    # evidência de papel, e é isso que mantém funcionando abreviações como
    # ``Aliq. PIS`` e ``Alíq. ICMS``.
    explicit_slot = (
        {"outra", "outras", "outro", "other"} & tokens
        or {"isenta", "isentas", "isento", "exempt"} & tokens
        or {"reduzida", "reduzido", "reduced"} & tokens
        or {"base", "basis"} & tokens
        or {"valor", "value", "amount"} & tokens
    )
    # ...mas só quando a leitura como alíquota depende apenas da abreviação.
    # Um cabeçalho que traz a palavra de alíquota por extenso continua sendo
    # alíquota mesmo ao lado de uma palavra de posição; é isso que mantém
    # "Tax rate (Tax/Base)" do sap_terumo como alíquota.
    rate_is_abbreviation_only = not (
        {"aliquota", "rate", "taxa", "tximp"} & raw_tokens
        or " tx imposto " in padded
    )
    if (
        {"aliquota", "rate", "taxa", "tximp"} & tokens
        or " tx imposto " in padded
    ) and not (explicit_slot and rate_is_abbreviation_only):
        return "rate"
    if {"outra", "outras", "outro", "other"} & tokens:
        return "other"
    if (
        {"isenta", "isentas", "isento", "exempt"} & tokens
        or ({"exc", "excl", "excluded"} & tokens and {"base", "basis"} & tokens)
    ):
        return "exempt"
    if {"reduzida", "reduzido", "reduced"} & tokens and {"base", "basis"} & tokens:
        return "reduced_base"
    # O SAP costuma abreviar o montante tributável (a base de cálculo) como
    # ``Mont.`` ou ``Monta`` numa coluna de tributo (por exemplo, PIS Mont. ao
    # lado de PIS Taxa e Valor do PIS). Fora do contexto de uma família de
    # tributo, ``mont.`` mantém o sentido comum de montante (valor).
    if header_tax_families(value) and {"mont", "monta", "montante"} & tokens:
        return "base"
    if {"base", "basis"} & tokens:
        return "base"
    if (
        {"valor", "value", "amount"} & tokens
        or ({"total"} & tokens and {"item", "linha"} & tokens)
    ):
        return "value"
    if {"mont", "monta", "montante"} & tokens:
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


def _header_conflict_parts(
    left: object, right: object
) -> tuple[str, tuple[str, ...], tuple[str, ...]] | None:
    """Conflito explícito entre dois cabeçalhos, em forma estruturada.

    Devolve ``(tipo, lado_esquerdo, lado_direito)``, com ``tipo`` igual a
    ``"family"`` (família de tributo), ``"qualifier"`` (qualificador do
    tributo, ST ou normal) ou ``"role"`` (papel semântico), e cada lado como
    ids internos ordenados; ``None`` quando não há conflito.

    É a fonte única de ``semantic_header_conflict`` (texto em inglês, que é
    entrada da IA) e de ``describe_header_conflict_pt`` (o mesmo conflito em
    português, para pessoas), para que os dois nunca discordem.
    """
    left_families = header_tax_families(left)
    right_families = header_tax_families(right)
    if left_families and right_families and left_families.isdisjoint(right_families):
        return "family", tuple(sorted(left_families)), tuple(sorted(right_families))

    left_qualifiers = set(normalize_header(left).split()) & {"st"}
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

    left_role = header_semantic_role(left)
    right_role = header_semantic_role(right)
    if left_role is not None and right_role is not None and left_role != right_role:
        return "role", (left_role,), (right_role,)
    return None


def semantic_header_conflict(left: object, right: object) -> str | None:
    """Descreve, em inglês, um conflito explícito de família de tributo ou de papel entre cabeçalhos.

    É deliberadamente conservador: um cabeçalho ausente ou sem sentido não tem
    assinatura explícita e por isso não é recusado. Conflitos explícitos
    continuam disponíveis para revisão humana, porque o conteúdo distintivo das
    linhas pode mostrar que um cabeçalho está errado.

    O texto fica em inglês, byte a byte: ele vai no payload do resolvedor de IA
    (campo ``semantic_header_conflict``) e o prompt cita esse campo. Para
    mostrar o conflito a uma pessoa, use ``describe_header_conflict_pt``.
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


#: O conflito de cabeçalho em português, para frases lidas por pessoas. Os
#: lados chegam prontos: famílias em maiúsculas unidas por ", " (``ICMS``),
#: qualificadores e papéis pelos rótulos de ``report_schema.TEXT_LABELS``
#: (``ST``/``normal``; ``base de cálculo``, ``alíquota`` ...).
HEADER_CONFLICT_FAMILY_PT = "conflito de família de tributo ({left} vs {right})"
HEADER_CONFLICT_QUALIFIER_PT = "conflito de qualificador do tributo ({left} vs {right})"
HEADER_CONFLICT_ROLE_PT = "conflito de papel semântico ({left} vs {right})"


def describe_header_conflict_pt(left: object, right: object) -> str | None:
    """O mesmo conflito de ``semantic_header_conflict``, escrito em português.

    Calculado a partir das mesmas entradas estruturadas, então existe
    exatamente quando o texto em inglês existe e aponta o mesmo tipo de
    conflito. Serve só para pessoas (justificativa do relatório); o texto em
    inglês continua sendo o que vai para a IA.
    """
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


def _header_text(value: object, index: int) -> str:
    # Fica em inglês: o nome vai para o embedding e para o payload da IA. Nas
    # células do relatório ele sai como "Coluna sem nome N" (report_schema).
    if pd.isna(value) or not str(value).strip():
        return f"Unnamed column {index + 1}"
    return str(value).strip()


@dataclass(frozen=True)
class ColumnRef:
    """Uma coluna de planilha: id estável, posição (a partir de 0), cabeçalho e ocorrência.

    ``name`` e ``display_name`` ficam em inglês em memória (``Unnamed column
    N``, ``(occurrence N)``): o nome é entrada da IA e o nome de exibição é
    comparado pelo ``apply``. A forma em português (``Coluna sem nome N``,
    ``(ocorrência N)``) é aplicada só nas células gravadas, por
    ``report_schema.display_name_to_external``.
    """

    id: str
    index: int
    name: str
    occurrence: int

    @property
    def display_name(self) -> str:
        suffix = f" (occurrence {self.occurrence})" if self.occurrence > 1 else ""
        return f"{self.name}{suffix}"


@dataclass
class SheetTable:
    """Uma aba lida do Excel: as colunas (``ColumnRef``) e os dados abaixo do cabeçalho."""

    columns: list[ColumnRef]
    data: pd.DataFrame

    @classmethod
    def from_excel(
        cls,
        path: str | Path,
        sheet_name: str | int = 0,
        header_row: int = 0,
    ) -> "SheetTable":
        raw = pd.read_excel(path, sheet_name=sheet_name, header=None, dtype=object)
        if raw.empty:
            raise ValueError(f"A planilha não tem linhas: {path}")
        if header_row < 0 or header_row >= len(raw):
            raise ValueError(
                f"A linha de cabeçalho {header_row + 1} está fora das linhas da planilha: {path}"
            )

        headers = [
            _header_text(value, i) for i, value in enumerate(raw.iloc[header_row])
        ]
        occurrences: dict[str, int] = {}
        columns: list[ColumnRef] = []
        for index, name in enumerate(headers):
            normalized = normalize_header(name)
            occurrences[normalized] = occurrences.get(normalized, 0) + 1
            columns.append(
                ColumnRef(
                    id=f"column_{index + 1:03d}",
                    index=index,
                    name=name,
                    occurrence=occurrences[normalized],
                )
            )

        data = raw.iloc[header_row + 1 :].reset_index(drop=True)
        data.columns = range(data.shape[1])
        return cls(columns=columns, data=data)

    def series(self, column: ColumnRef) -> pd.Series:
        return self.data.iloc[:, column.index]


def _json_value(value: object, max_length: int = 100) -> str:
    if isinstance(value, pd.Timestamp):
        text = value.isoformat()
    else:
        text = str(value).strip()
    return text if len(text) <= max_length else f"{text[: max_length - 1]}…"


def _numeric_value(value: object) -> float:
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
    """Valores representativos e estáveis, independentes da ordem das linhas da planilha."""
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
    selected: list[str] = []

    def add(value: str) -> None:
        if value not in selected and len(selected) < limit:
            selected.append(value)

    # Os valores frequentes representam a distribuição dominante, e os valores
    # espaçados por igual mostram a faixa e as diferenças de formato. As duas
    # seleções são estáveis.
    for value in sorted(counts, key=lambda item: (-counts[item], _sample_sort_key(item)))[:2]:
        add(value)
    if len(ordered_unique) == 1:
        return selected
    for position in np.linspace(0, len(ordered_unique) - 1, num=limit, dtype=int):
        add(ordered_unique[int(position)])
    for value in sorted(counts, key=lambda item: (-counts[item], _sample_sort_key(item))):
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
        "numeric_min": round(float(numeric_values.min()), 4)
        if len(numeric_values)
        else None,
        "numeric_q25": round(float(numeric_values.quantile(0.25)), 4)
        if len(numeric_values)
        else None,
        "numeric_median": round(float(numeric_values.median()), 4)
        if len(numeric_values)
        else None,
        "numeric_q75": round(float(numeric_values.quantile(0.75)), 4)
        if len(numeric_values)
        else None,
        "numeric_max": round(float(numeric_values.max()), 4)
        if len(numeric_values)
        else None,
        "fixed_digit_width": fixed_digit_width,
        "fixed_digit_width_fraction": round(fixed_digit_width_fraction, 3),
        "valid_cpf_fraction": round(_validation_fraction(texts, _cpf_is_valid), 3),
        "valid_cnpj_fraction": round(_validation_fraction(texts, _cnpj_is_valid), 3),
        "valid_nfe_key_fraction": round(_validation_fraction(texts, _nfe_key_is_valid), 3),
        "evidence_quality": evidence_quality,
        "samples": sample_values(series, limit),
    }


def profile_similarity(
    left: dict[str, object], right: dict[str, object]
) -> float:
    """Compara o formato genérico dos valores, sem embutir nenhum de-para de campo da nota."""
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
        0.05 * closeness("numeric_fraction")
        + 0.05 * closeness("digit_fraction")
        + 0.05 * closeness("alpha_fraction")
        + 0.05 * length_score
        + 0.10 * digit_count_score
        + 0.55 * digit_count_overlap
        + 0.15 * type_score
    )
    return round(max(0.0, min(score, 1.0)), 4)


def _distinctive_samples(profile: dict[str, object]) -> set[str]:
    """Valores de amostra normalizados que dificilmente coincidem por acaso."""
    distinctive: set[str] = set()
    for value in profile["samples"]:
        normalized = normalize_header(value)
        compact = normalized.replace(" ", "")
        if not compact or compact in {"0", "00", "000", "0000"}:
            continue
        if compact.isdigit():
            if len(compact) >= 6:
                distinctive.add(compact)
            continue
        if len(compact) >= 4 or (
            len(compact) >= 3
            and any(character.isalpha() for character in compact)
            and any(character.isdigit() for character in compact)
        ):
            distinctive.add(normalized)
    return distinctive


def distinctive_sample_overlap(
    left: dict[str, object], right: dict[str, object]
) -> float:
    """Mede a coincidência exata de amostras não triviais, sem zeros nem identificadores curtos."""
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


def identifier_match_score(
    left: dict[str, object], right: dict[str, object]
) -> float:
    """Evidência forte de conteúdo, só para identificadores brasileiros validados (CPF, CNPJ, chave de acesso)."""
    scores = []
    for key in (
        "valid_cpf_fraction",
        "valid_cnpj_fraction",
        "valid_nfe_key_fraction",
    ):
        left_score = float(left.get(key, 0.0))
        right_score = float(right.get(key, 0.0))
        if left_score >= 0.80 and right_score >= 0.80:
            scores.append(min(left_score, right_score))
    return round(max(scores, default=0.0), 4)


def distinctive_profile_match(
    left: dict[str, object], right: dict[str, object]
) -> bool:
    """Diz se as linhas trazem evidência mais forte do que a simples coincidência de tipo."""
    if (
        left["evidence_quality"] != "informative"
        or right["evidence_quality"] != "informative"
    ):
        return False
    if distinctive_sample_overlap_count(left, right) >= 2:
        return True
    return identifier_match_score(left, right) >= 0.90


def numeric_series(series: pd.Series) -> pd.Series:
    """Converte números no formato comum e no brasileiro, sem mudar a ordem das linhas."""
    if pd.api.types.is_numeric_dtype(series):
        return pd.to_numeric(series, errors="coerce").astype(float)
    return series.map(_numeric_value).astype(float)


def tax_calculation_evidence(
    base: pd.Series,
    rate: pd.Series,
    value: pd.Series,
    *,
    minimum_rows: int = 5,
) -> dict[str, object]:
    """Pontua, linha a linha, a relação valor ~= base x alíquota.

    As duas representações comuns de alíquota são testadas: pontos
    percentuais (18) e fração decimal (0.18). A pontuação é a fração de linhas
    utilizáveis dentro da tolerância de 2% relativos ou de dois centavos.
    """
    frame = pd.concat(
        [
            numeric_series(base).rename("base"),
            numeric_series(rate).rename("rate"),
            numeric_series(value).rename("value"),
        ],
        axis=1,
    ).replace([np.inf, -np.inf], np.nan)
    eligible = frame[
        (frame["base"].abs() > 1e-12)
        & ((frame["rate"].abs() > 1e-12) | (frame["value"].abs() > 0.02))
    ]
    eligible_count = len(eligible)
    frame = eligible.dropna()
    coverage = len(frame) / eligible_count if eligible_count else 0.0
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
        predicted = frame["base"] * frame["rate"] / divisor
        absolute_error = (predicted - frame["value"]).abs()
        scale = pd.concat([predicted.abs(), frame["value"].abs()], axis=1).max(axis=1)
        relative_error = absolute_error / scale.clip(lower=0.02)
        within_tolerance = (absolute_error <= 0.02) | (relative_error <= 0.02)
        alternatives.append(
            (
                float(within_tolerance.mean()),
                float(relative_error.median()),
                divisor,
            )
        )

    fit_score, median_error, divisor = max(
        alternatives, key=lambda item: (item[0], -item[1])
    )
    degenerate = bool(
        frame["base"].nunique(dropna=True) < 3
        or frame["value"].nunique(dropna=True) < 3
    )
    sample_factor = min(1.0, float(np.sqrt(len(frame) / 20.0)))
    variation_factor = 0.75 if degenerate else 1.0
    score = fit_score * float(np.sqrt(coverage)) * sample_factor * variation_factor
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
    """Família de tributo e qualificadores reutilizáveis (ST, DIF ...) de um campo de cálculo."""
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
    """Identifica, de forma conservadora, o trio quantidade x preço unitário = valor do item."""
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
    minimum_rows: int = 5,
) -> dict[str, object]:
    """Pontua quantidade x preço unitário ~= valor do item, com a tolerância de um relatório fiscal."""
    frame = pd.concat(
        [
            numeric_series(quantity).rename("quantity"),
            numeric_series(unit_price).rename("unit_price"),
            numeric_series(line_value).rename("line_value"),
        ],
        axis=1,
    ).replace([np.inf, -np.inf], np.nan)
    eligible = frame[
        (frame["quantity"].abs() > 1e-12)
        & (
            (frame["unit_price"].abs() > 1e-12)
            | (frame["line_value"].abs() > 0.02)
        )
    ]
    eligible_count = len(eligible)
    frame = eligible.dropna()
    coverage = len(frame) / eligible_count if eligible_count else 0.0
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
    predicted = frame["quantity"] * frame["unit_price"]
    absolute_error = (predicted - frame["line_value"]).abs()
    scale = pd.concat([predicted.abs(), frame["line_value"].abs()], axis=1).max(axis=1)
    relative_error = absolute_error / scale.clip(lower=0.02)
    within_tolerance = (absolute_error <= 0.02) | (relative_error <= 0.03)
    fit_score = float(within_tolerance.mean())
    degenerate = bool(
        frame["quantity"].nunique(dropna=True) < 3
        or frame["line_value"].nunique(dropna=True) < 3
    )
    sample_factor = min(1.0, float(np.sqrt(len(frame) / 20.0)))
    variation_factor = 0.75 if degenerate else 1.0
    score = fit_score * float(np.sqrt(coverage)) * sample_factor * variation_factor
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


class HeaderScorer(Protocol):
    def scores(self, model_headers: Sequence[str], client_headers: Sequence[str]) -> np.ndarray:
        """Matriz com formato (cabeçalhos do modelo, cabeçalhos do cliente)."""


class LexicalHeaderScorer:
    """Avaliador de cabeçalhos offline, usado nos testes e nas execuções locais (--offline)."""

    def scores(self, model_headers: Sequence[str], client_headers: Sequence[str]) -> np.ndarray:
        matrix = np.zeros((len(model_headers), len(client_headers)), dtype=float)
        for i, target in enumerate(model_headers):
            left = normalize_header(target)
            left_tokens = semantic_header_signature(target)
            for j, source in enumerate(client_headers):
                right = normalize_header(source)
                if left and left == right:
                    matrix[i, j] = 1.0
                    continue
                right_tokens = semantic_header_signature(source)
                union = left_tokens | right_tokens
                jaccard = len(left_tokens & right_tokens) / len(union) if union else 0.0
                sequence = SequenceMatcher(None, left, right).ratio()
                matrix[i, j] = 0.7 * sequence + 0.3 * jaccard
                if left and semantically_equivalent_headers(target, source):
                    matrix[i, j] = max(matrix[i, j], SEMANTIC_EQUIVALENCE_SCORE)
        return matrix


class OpenAIEmbeddingHeaderScorer:
    """Avaliador de cabeçalhos por embeddings da OpenAI (similaridade de cosseno).

    O prefixo enviado com cada cabeçalho fica em inglês, byte a byte: é entrada
    do modelo, e mudá-lo mudaria os vetores e as pontuações de nome.
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

        # Termos reutilizáveis traduzidos, abreviados ou em outra ordem podem
        # reforçar a pontuação do embedding, mas não ganham uma probabilidade
        # inventada de 1.0.
        for i, target in enumerate(model_headers):
            for j, source in enumerate(client_headers):
                if normalize_header(target) == normalize_header(source):
                    similarities[i, j] = 1.0
                elif semantically_equivalent_headers(target, source):
                    similarities[i, j] = max(
                        similarities[i, j], SEMANTIC_EQUIVALENCE_SCORE
                    )
        return similarities


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
        """Decide uma coluna do modelo de baixa confiança entre os candidatos do cliente."""


class OpenAIDataResolver:
    """Resolvedor de IA: pede à OpenAI uma decisão estruturada (``DataMappingDecision``).

    O prompt (``instructions``), o payload em JSON e o esquema de saída ficam
    em inglês, byte a byte: são entrada do modelo, e o teste dourado
    (``tests/test_model_input_golden.py``) falha se qualquer um deles mudar. Por
    isso a justificativa que o modelo escreve (``reason``) também chega em
    inglês e passa intacta para o relatório.
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
    ) -> DataMappingDecision:
        target_profile = summarize_values(model_values, sample_size)
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
            name_confidence, selected_profile = selected
            weak_data = (
                target_profile["evidence_quality"] in {"empty", "all_zero"}
                or selected_profile["evidence_quality"] in {"empty", "all_zero"}
            )
            if weak_data and decision.final_confidence < name_confidence:
                decision = decision.model_copy(update={"final_confidence": name_confidence})
        return decision


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
    """Quais etapas nomeadas de fato sustentaram este mapeamento.

    Lidas das mesmas pontuações em que a decisão foi tomada, para que o
    relatório não alegue evidência que o processo não usou. Uma etapa entra na
    lista quando produziu um sinal positivo para a coluna escolhida, e não
    apenas porque rodou.
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
    # Uma prova que não distingue uma coluna confirma o escopo do tributo, não
    # este mapeamento, então não é evidência para ele. Ver _trio_proof_text;
    # report_schema.proof_is_decisive procura o marcador que a frase traz
    # (report_schema.PROOF_NOT_DECISIVE_PT).
    if relationship_score > 0.0 or report_schema.proof_is_decisive(arithmetic_proof):
        steps.append("arithmetic")
    return steps


@dataclass
class MappingResult:
    """Uma linha da aba Mapeamentos: a decisão para uma coluna do modelo.

    Os valores de máquina ficam no vocabulário interno (``status`` igual a
    ``accepted``, ``review`` ou ``no_match``; ``method``, etapas e vetos com os
    ids de ``mapping_steps``); ``report_schema`` os traduz na gravação. As
    frases (``reason`` e ``arithmetic_proof``) já são escritas em português,
    exceto a justificativa escrita pela IA, que passa intacta em inglês.
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
    catalog_coverage: float | None = None
    #: Cobertura sobre os códigos distintos. catalog_coverage continua
    #: ponderada pelas linhas, para que a coluna do relatório mantenha o
    #: sentido; as travas usam esta, porque é dela que catalog_confidence é
    #: calculada hoje.
    catalog_distinct_coverage: float | None = None
    catalog_confidence: float | None = None
    #: Preenchida quando um trio aritmético do próprio cliente põe esta coluna
    #: aqui de forma independente. Não depende do cabeçalho, então é o que diz
    #: ao revisor - e à avaliação - que este mapeamento sobreviveria a um ERP
    #: desconhecido.
    arithmetic_proof: str | None = None
    #: As etapas nomeadas da análise por trás desta linha - ver mapping_steps.py.
    supported_by: str = ""
    decided_by: str = ""
    vetoed_by: str = ""
    llm_data_confidence: float | None = None
    llm_final_confidence: float | None = None


@dataclass(frozen=True)
class MapperConfig:
    """Limiares e tamanhos da execução (os mesmos das opções da linha de comando)."""

    name_threshold: float = 0.90
    name_margin_threshold: float = 0.05
    final_threshold: float = 0.90
    candidate_count: int = 10
    sample_size: int = 8


def _accepted_source_index(result: MappingResult) -> int | None:
    if result.status != "accepted" or result.client_column_number is None:
        return None
    return result.client_column_number - 1


def _accepted_tax_anchors(
    model_table: SheetTable,
    results: dict[int, MappingResult],
    scope: tuple[str, tuple[str, ...]],
    *,
    exclude_target: int | None = None,
) -> dict[str, int]:
    anchors: dict[str, int] = {}
    for target_index, result in results.items():
        if target_index == exclude_target:
            continue
        model_column = model_table.columns[target_index]
        role = header_semantic_role(model_column.name)
        if (
            role not in {"base", "rate", "value"}
            or tax_calculation_scope(model_column.name) != scope
        ):
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
    *,
    target_index: int,
    candidate_indices: set[int],
    model_table: SheetTable,
    client_table: SheetTable,
    results: dict[int, MappingResult],
    numeric_client_series: dict[int, pd.Series] | None = None,
) -> dict[int, list[dict[str, object]]]:
    """Avalia relações reutilizáveis entre linhas contra as âncoras já aceitas."""
    evidence: dict[int, list[dict[str, object]]] = {
        index: [] for index in candidate_indices
    }
    model_column = model_table.columns[target_index]

    def client_values(index: int) -> pd.Series:
        if numeric_client_series is not None:
            return numeric_client_series[index]
        return client_table.series(client_table.columns[index])

    tax_role = header_semantic_role(model_column.name)
    tax_scope = tax_calculation_scope(model_column.name)
    if tax_role in {"base", "rate", "value"} and tax_scope is not None:
        anchors = _accepted_tax_anchors(
            model_table, results, tax_scope, exclude_target=target_index
        )
        other_roles = {"base", "rate", "value"} - {tax_role}
        if other_roles <= anchors.keys():
            for candidate_index in candidate_indices:
                role_sources = dict(anchors)
                role_sources[tax_role] = candidate_index
                relationship = tax_calculation_evidence(
                    client_values(role_sources["base"]),
                    client_values(role_sources["rate"]),
                    client_values(role_sources["value"]),
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

    line_role = line_calculation_role(model_column.name)
    if line_role is not None:
        anchors = _accepted_line_anchors(
            model_table, results, exclude_target=target_index
        )
        other_roles = {"quantity", "unit_price", "line_value"} - {line_role}
        if other_roles <= anchors.keys():
            for candidate_index in candidate_indices:
                role_sources = dict(anchors)
                role_sources[line_role] = candidate_index
                relationship = line_calculation_evidence(
                    client_values(role_sources["quantity"]),
                    client_values(role_sources["unit_price"]),
                    client_values(role_sources["line_value"]),
                )
                relationship.update(
                    {
                        "rule": "line_value_equals_quantity_times_unit_price",
                        "target_role": line_role,
                    }
                )
                evidence[candidate_index].append(relationship)

    return {index: rules for index, rules in evidence.items() if rules}


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
    minimum_rows: int = 20,
    minimum_coverage: float = 0.80,
    maximum_fit: float = 0.20,
) -> bool:
    return any(
        bool(rule.get("available"))
        and int(rule.get("rows_tested", 0)) >= minimum_rows
        and float(rule.get("coverage", 0.0)) >= minimum_coverage
        and float(rule.get("fit_score", rule.get("score", 0.0))) <= maximum_fit
        for rule in rules
    )


def _catalog_score(evidence: Sequence[dict[str, object]]) -> float:
    best = best_catalog_evidence(evidence)
    return float(best["score"]) if best is not None else 0.0


def _candidate_catalog_evidence(
    *,
    target_index: int,
    candidate_indices: set[int],
    model_table: SheetTable,
    client_table: SheetTable,
    reference_catalogs: ReferenceCatalogs | None,
    analysis_cache: dict[tuple[str, int], dict[str, object]],
) -> dict[int, list[dict[str, object]]]:
    if reference_catalogs is None:
        return {}
    catalog_names = [
        name
        for name in expected_catalogs(model_table.columns[target_index].name)
        if name in reference_catalogs.catalogs
    ]
    evidence: dict[int, list[dict[str, object]]] = {}
    for index in candidate_indices:
        matches = []
        for name in catalog_names:
            key = name, index
            if key not in analysis_cache:
                analysis_cache[key] = reference_catalogs.analyze(
                    client_table.series(client_table.columns[index]), name
                )
            matches.append(analysis_cache[key])
        evidence[index] = matches
    return {index: matches for index, matches in evidence.items() if matches}


def _audit_accepted_catalog_matches(
    *,
    model_table: SheetTable,
    client_table: SheetTable,
    results: dict[int, MappingResult],
    available: set[int],
    low_confidence_targets: list[int],
    reference_catalogs: ReferenceCatalogs | None,
    analysis_cache: dict[tuple[str, int], dict[str, object]],
) -> None:
    """Anexa a evidência de catálogo e reabre as contradições claras da primeira passagem."""
    if reference_catalogs is None:
        return
    for target_index, result in list(results.items()):
        source_index = _accepted_source_index(result)
        if source_index is None:
            continue
        names = expected_catalogs(model_table.columns[target_index].name)
        evidence = []
        for name in names:
            if name not in reference_catalogs.catalogs:
                continue
            key = name, source_index
            if key not in analysis_cache:
                analysis_cache[key] = reference_catalogs.analyze(
                    client_table.series(client_table.columns[source_index]), name
                )
            evidence.append(analysis_cache[key])
        selected = best_catalog_evidence(evidence)
        if selected is None:
            continue
        result.catalog_name = str(selected["catalog"])
        result.catalog_coverage = round(float(selected["coverage"]), 4)
        result.catalog_distinct_coverage = round(
            float(selected.get("distinct_coverage", selected["coverage"])), 4
        )
        result.catalog_confidence = round(float(selected["score"]), 4)
        strong_contradiction = (
            float(selected["specificity"]) >= 0.90
            and int(selected["nonempty_count"]) >= 5
            and float(selected["format_fraction"]) >= 0.90
            and float(selected["coverage"]) < 0.10
            and not bool(selected["all_zero_only"])
        )
        if not strong_contradiction:
            continue
        available.add(source_index)
        del results[target_index]
        if target_index not in low_confidence_targets:
            low_confidence_targets.append(target_index)


#: Piso para a evidência de catálogo de um domínio de códigos único no país.
#:
#: Medido em 348 pares coluna-catálogo pontuados nos quatro clientes de
#: amostra: nos catálogos de especificidade 1.0 (CFOP, NCM, município do IBGE)
#: as seis colunas de código verdadeiras pontuam de 0.8824 a 1.0000, enquanto
#: todos os outros 1.301 pares pontuam de 0.0000 a 0.0137 - um intervalo vazio
#: de 64 vezes. Qualquer piso dentro desse intervalo separa perfeitamente e
#: resiste à validação deixando um cliente de fora; 0.90 não, porque fica na
#: borda de cima do grupo dos códigos e não no intervalo, e é por isso que uma
#: coluna de CFOP de manual, com 15 dos 17 códigos oficiais, era recusada.
#:
#: O piso NÃO se aplica, de propósito, aos catálogos de especificidade menor.
#: São domínios pequenos e fechados - cst_ipi tem 14 códigos, cst_pis_cofins
#: 33 - e qualquer coluna de inteiros pequenos casa com eles: com 0.50 o mesmo
#: corpus admite 123 pares em vez de 6, entre eles 'Means of Transport' contra
#: cst_ipi e 'Valor Frete' contra cst_icms. Nesses catálogos não existe
#: intervalo nenhum, então o limiar atual continua valendo e eles seguem como
#: evidência para a IA, não como evidência de aceite.
DISTINCTIVE_CATALOG_FLOOR = 0.50

#: Mesmo um domínio único no país precisa de códigos distintos suficientes
#: para ser evidência: dois valores coincidentes não provam nada, por mais
#: oficial que seja a tabela.
DISTINCTIVE_CATALOG_MIN_CODES = 5


def catalog_evidence_is_distinctive(
    evidence: Mapping[str, object] | None,
    *,
    final_threshold: float,
) -> bool:
    """Diz se a evidência de catálogo é forte o bastante para decidir um mapeamento sozinha."""
    if evidence is None:
        return False
    specificity = float(evidence["specificity"])
    score = float(evidence["score"])
    if bool(evidence["all_zero_only"]):
        return False
    if int(evidence["nonempty_count"]) < 5 or float(evidence["format_fraction"]) < 0.90:
        return False
    if specificity >= 1.0:
        return (
            score >= DISTINCTIVE_CATALOG_FLOOR
            and int(evidence["unique_valid_count"]) >= DISTINCTIVE_CATALOG_MIN_CODES
        )
    return score >= final_threshold


def _catalog_label(catalog: object) -> str:
    """Nome legível de um catálogo de reference_data para frases (``cfop`` -> ``CFOP``)."""
    return report_schema.CATALOG_LABELS.get(str(catalog), str(catalog))


#: Justificativa da decisão local por catálogo. ``catalog`` é o nome legível
#: (``_catalog_label``); ``distinct_coverage`` e ``coverage`` chegam formatados
#: com ``report_schema.format_percent_br`` (``88,2%``).
REASON_CATALOG_DECISION = (
    "A tabela oficial versionada de {catalog} cobre {distinct_coverage} dos "
    "{unique_formatted_count} códigos distintos usados ({coverage} das linhas) e "
    "identifica este candidato de forma única entre as colunas disponíveis."
)


def _unique_distinctive_catalog_decision(
    *,
    candidates: Sequence[tuple[ColumnRef, pd.Series, float]],
    catalog_evidence: dict[int, list[dict[str, object]]],
    minimum_score: float = 0.90,
    minimum_lead: float = 0.10,
) -> DataMappingDecision | None:
    """Decide localmente só os domínios de códigos oficiais distintivos e de alta cobertura.

    A decisão não passa pela IA; a justificativa é escrita aqui, em português.
    """
    ranked: list[tuple[float, float, ColumnRef, dict[str, object]]] = []
    for column, _, name_score in candidates:
        best = best_catalog_evidence(catalog_evidence.get(column.index, []))
        if best is None:
            continue
        if not catalog_evidence_is_distinctive(best, final_threshold=minimum_score):
            continue
        ranked.append((float(best["score"]), name_score, column, best))
    ranked.sort(key=lambda item: (-item[0], -item[1], item[2].index))
    if not ranked:
        return None
    runner_up = ranked[1][0] if len(ranked) > 1 else 0.0
    catalog_score, name_score, column, evidence = ranked[0]
    if catalog_score - runner_up < minimum_lead:
        return None
    final_confidence = max(0.80, 0.35 * name_score + 0.65 * catalog_score)
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


def _demote_inconsistent_tax_header_matches(
    *,
    model_table: SheetTable,
    client_table: SheetTable,
    similarities: np.ndarray,
    results: dict[int, MappingResult],
    available: set[int],
    low_confidence_targets: list[int],
    numeric_client_series: dict[int, pd.Series],
    minimum_relationship_score: float = 0.90,
) -> None:
    """Reabre um ponto fora da curva claro de um trio fiscal quando uma alternativa informativa se encaixa."""
    scopes = {
        scope
        for column in model_table.columns
        if (scope := tax_calculation_scope(column.name)) is not None
    }
    for scope in sorted(scopes):
        anchors = _accepted_tax_anchors(model_table, results, scope)
        if {"base", "rate", "value"} - anchors.keys():
            continue

        current = tax_calculation_evidence(
            numeric_client_series[anchors["base"]],
            numeric_client_series[anchors["rate"]],
            numeric_client_series[anchors["value"]],
        )
        if bool(current["available"]) and float(current["score"]) >= minimum_relationship_score:
            continue
        strong_contradiction = _relationship_is_strong_contradiction([current])

        role_targets = {
            header_semantic_role(model_table.columns[index].name): index
            for index, result in results.items()
            if result.status == "accepted"
            and tax_calculation_scope(model_table.columns[index].name) == scope
            and header_semantic_role(model_table.columns[index].name)
            in {"base", "rate", "value"}
        }
        alternatives: list[tuple[float, float, str, int, int]] = []
        for role, target_index in role_targets.items():
            current_source = anchors[role]
            current_rules = _candidate_relationship_evidence(
                target_index=target_index,
                candidate_indices={current_source},
                model_table=model_table,
                client_table=client_table,
                results=results,
                numeric_client_series=numeric_client_series,
            ).get(current_source, [])
            current_score = _relationship_score(current_rules)
            candidates = available | {current_source}
            candidate_rules = _candidate_relationship_evidence(
                target_index=target_index,
                candidate_indices=candidates,
                model_table=model_table,
                client_table=client_table,
                results=results,
                numeric_client_series=numeric_client_series,
            )
            for candidate_index, rules in candidate_rules.items():
                if candidate_index == current_source:
                    continue
                candidate_profile = summarize_values(
                    client_table.series(client_table.columns[candidate_index]), 8
                )
                if candidate_profile["evidence_quality"] != "informative":
                    continue
                relationship_score = _relationship_score(rules)
                if relationship_score < minimum_relationship_score:
                    continue
                name_score = float(similarities[target_index, candidate_index])
                alternatives.append(
                    (
                        relationship_score - current_score,
                        name_score,
                        role,
                        target_index,
                        candidate_index,
                    )
                )

        if alternatives:
            improvement, _, _, target_index, _ = max(alternatives)
            if improvement < 0.50:
                continue
        elif strong_contradiction:
            # Reabre a mais fraca das três âncoras. Ela passa pela etapa dos
            # dados das linhas, onde a contradição impede o aceite silencioso.
            target_index = min(
                role_targets.values(),
                key=lambda index: (
                    results[index].name_confidence,
                    results[index].name_margin,
                    -index,
                ),
            )
        else:
            continue
        current_source = _accepted_source_index(results[target_index])
        if current_source is None:
            continue
        available.add(current_source)
        del results[target_index]
        if target_index not in low_confidence_targets:
            low_confidence_targets.append(target_index)


def _rank_available(
    target_index: int,
    similarities: np.ndarray,
    available: set[int],
    limit: int,
) -> list[tuple[int, float]]:
    ranked = sorted(
        ((index, float(similarities[target_index, index])) for index in available),
        key=lambda item: (-item[1], item[0]),
    )
    return ranked[:limit]


def _rank_header_compatible(
    target_index: int,
    similarities: np.ndarray,
    available: set[int],
    model_column: ColumnRef,
    client_columns: Sequence[ColumnRef],
    limit: int,
) -> list[tuple[int, float]]:
    """Prefere a compatibilidade semântica explícita no casamento só por cabeçalho.

    Cabeçalhos em conflito ainda entram na passagem dos dados das linhas, onde
    o conteúdo distintivo pode mostrar que um cabeçalho do cliente está
    errado. Eles não valem como segunda melhor opção para o aceite automático
    só por cabeçalho.
    """
    ranked = _rank_available(target_index, similarities, available, len(available))
    compatible = [
        item
        for item in ranked
        if semantic_header_conflict(
            model_column.name, client_columns[item[0]].name
        )
        is None
    ]
    return (compatible or ranked)[:limit]


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
    """Une os candidatos mais fortes por cabeçalho, formato das linhas, relação fiscal e catálogo."""
    name_ranked = _rank_available(target_index, similarities, available, limit)
    profile_ranked = sorted(
        (
            (index, profile_similarity(target_profile, client_profiles[index]))
            for index in available
        ),
        key=lambda item: (-item[1], item[0]),
    )
    profile_ranked = [item for item in profile_ranked if item[1] > 0.0][:limit]
    candidate_indices = {index for index, _ in name_ranked}
    candidate_indices.update(index for index, _ in profile_ranked)
    relationship_ranked = sorted(
        (
            (index, _relationship_score(rules))
            for index, rules in (relationship_evidence or {}).items()
            if index in available
        ),
        key=lambda item: (-item[1], item[0]),
    )
    candidate_indices.update(
        index for index, score in relationship_ranked[:limit] if score > 0.0
    )
    catalog_ranked = sorted(
        (
            (index, _catalog_score(matches))
            for index, matches in (catalog_evidence or {}).items()
            if index in available
        ),
        key=lambda item: (-item[1], item[0]),
    )
    candidate_indices.update(
        index for index, score in catalog_ranked[:limit] if score > 0.0
    )
    return sorted(
        (
            (index, float(similarities[target_index, index]))
            for index in candidate_indices
        ),
        key=lambda item: (-item[1], item[0]),
    )


def _name_score_context(
    ranked: Sequence[tuple[int, float]],
    chosen_index: int | None = None,
) -> tuple[float, float, float]:
    """Pontuação escolhida, a da alternativa mais forte e a diferença entre elas."""
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
    config: MapperConfig,
) -> bool:
    """Recusa as escolhas forçadas e mantém os candidatos plausíveis para revisão."""
    near_name_threshold = max(
        0.0, config.name_threshold - config.name_margin_threshold
    )
    weak_name_threshold = max(
        0.0, config.name_threshold - 2 * config.name_margin_threshold
    )
    rows_are_informative = (
        target_profile["evidence_quality"] == "informative"
        and selected_profile["evidence_quality"] == "informative"
    )
    distinctive_rows = distinctive_profile_match(target_profile, selected_profile)
    relationship_is_strong = relationship_score >= config.final_threshold
    distinctive_catalog = (
        catalog_score >= config.final_threshold and catalog_coverage >= 0.90
    )

    if header_conflict is not None:
        return bool(
            (distinctive_rows or relationship_is_strong)
            and data_confidence >= config.final_threshold
            and final_confidence >= config.final_threshold
        ) or bool(
            distinctive_catalog
            and data_confidence >= config.final_threshold
            and final_confidence
            >= max(0.0, config.final_threshold - 2 * config.name_margin_threshold)
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


#: Um trio descoberto precisa passar destes limites para valer como prova. A
#: barra de aderência é alta porque a identidade é aritmética exata, não uma
#: correlação, e a barra de linhas existe porque um encaixe perfeito em
#: poucas linhas não prova nada - o corpus tem encaixes degenerados de 12
#: linhas idênticos aos verdadeiros.
TRIO_MIN_FIT = 0.97
TRIO_MIN_ROWS = 30

#: Prova aritmética (coluna prova_aritmetica). ``tax_scope`` é a sigla do
#: tributo em maiúsculas (``PIS``); ``fit_score`` chega com vírgula decimal
#: (``report_schema.format_decimal_br``: ``0,998``).
PROOF_TEXT = (
    "valor = base x alíquota se confirma para {tax_scope} em {rows_tested} linhas, "
    "com aderência {fit_score}"
)
#: Complemento da prova quando o cliente tem mais de um bloco do mesmo tributo.
#: Termina com o marcador ``report_schema.PROOF_NOT_DECISIVE_PT``, que é o que
#: ``report_schema.proof_is_decisive`` procura, em memória e no arquivo.
PROOF_NOT_DECISIVE_SUFFIX = (
    " - mas este cliente tem {alternatives} blocos disjuntos de {tax_scope} que "
    "satisfazem a equação, portanto ela " + report_schema.PROOF_NOT_DECISIVE_PT
)


def _trio_proof_text(trio: dict[str, object] | None) -> str | None:
    """Uma frase que o revisor consegue conferir, ou nada.

    A identidade confirma um *escopo*, não uma coluna. Quando um cliente tem
    dois blocos disjuntos para um mesmo tributo - um bloco de apuração e um de
    escrituração, por exemplo -, os dois satisfazem valor = base x alíquota,
    então a prova vale para qualquer um e não distingue nenhum deles. Dizer
    isso é o ponto central: uma prova sem ressalva no bloco errado parece um
    aval, e foi assim que o m213 do datasul acabou com uma.
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


def discover_client_tax_trios(
    client_table: SheetTable,
) -> dict[tuple[str, tuple[str, ...]], list[dict[str, object]]]:
    """Encontra os trios base/alíquota/valor que satisfazem valor = base x alíquota.

    Roda só sobre as colunas do próprio cliente - sem modelo, sem aceite
    anterior, sem vocabulário além das palavras de família e de papel que já
    são necessárias em outros pontos. É essa a ideia: ``valor = base x
    alíquota`` é legislação tributária brasileira, e não uma propriedade de
    uma exportação, então um trio comprovado aqui é evidência que vale para um
    ERP que ninguém viu.

    A evidência de relação que já existia não faz isso. Ela pede a
    _accepted_tax_anchors dois papéis já ACEITOS no mesmo escopo, então só
    confirma o que o casamento de cabeçalhos já resolveu; medida nos quatro
    clientes de amostra, ela não destrava nada que o aceite não tivesse
    destravado antes.

    Devolve TODOS os trios qualificados de cada escopo, o melhor primeiro, com
    a mesma chave de tax_calculation_scope. Guardar só o melhor estava errado:
    um cliente pode ter dois blocos disjuntos para um tributo - o datasul tem
    um bloco de apuração (Base PIS Ap x Aliq. PIS = Vlr PIS Ap, aderência
    0.9996 em 5.501 linhas) e um de escrituração (Base Pis x Aliq. PIS = Valor
    Pis, aderência 0.9857 em 70) - e o maior não é automaticamente o que as
    posições do modelo querem. Cada trio traz ``alternatives``: quantos blocos
    mutuamente disjuntos o escopo tem, que é o que diz ao leitor se a prova
    distingue a coluna.
    """
    by_scope: dict[tuple[str, tuple[str, ...]], dict[str, list[int]]] = {}
    for index, column in enumerate(client_table.columns):
        role = header_semantic_role(column.name)
        if role not in {"base", "rate", "value"}:
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
        if not {"base", "rate", "value"} <= roles.keys():
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
        # Dois trios que compartilham uma coluna são o mesmo bloco descrito
        # duas vezes - o sap_terumo nomeia cada alíquota duas vezes, como
        # 'ICMS Taxa' e 'Tax rate ICMS', e as duas fecham com a mesma base e o
        # mesmo valor. Só blocos sem nada em comum são leituras rivais das
        # posições do modelo.
        disjoint = _disjoint_trio_count(trios)
        for trio in trios:
            trio["alternatives"] = disjoint
        proven[scope] = trios
    return proven


def _disjoint_trio_count(trios: Sequence[dict[str, object]]) -> int:
    """Quantos blocos sem colunas em comum estes trios representam."""
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
    """O trio comprovado que, de forma independente, põe esta coluna do cliente aqui, se houver."""
    role = header_semantic_role(model_column_name)
    if role not in {"base", "rate", "value"}:
        return None
    scope = tax_calculation_scope(model_column_name)
    if scope is None:
        return None
    for trio in trios.get(scope, ()):
        if trio[role] == client_index:
            return trio
    return None


def client_column_is_vacuous(table: SheetTable, column: ColumnRef) -> bool:
    """Diz se uma coluna do cliente não tem nenhuma célula preenchida.

    Deliberadamente mais estreito que ``evidence_quality == "all_zero"``. Uma
    coluna de zeros é dado fiscal comum - uma operação isenta tem base zero, e
    seis das colunas zeradas do oracle estão no gabarito escrito pelo cliente
    -, enquanto uma coluna vazia não pode ser a correspondente de nenhuma
    coluna do modelo. Um mapeamento aceito para ela chega ao cliente parecendo
    mapeado e silenciosamente em branco, que é a única falha mais cara do que
    uma coluna faltando.
    """
    series = table.series(column)
    return int(series.notna().sum()) == 0 or not any(
        str(value).strip() for value in series.dropna()
    )


@dataclass
class UnmatchedColumn:
    """Uma coluna do cliente cujos dados não chegam ao modelo base (aba Colunas do Cliente sem Uso).

    Sem uso quer dizer NÃO CONSUMIDA POR UM MAPEAMENTO ACEITO, que é o que
    decide se o dado de fato chega: o apply_invoice_mapping só preenche as
    linhas aceitas e as confirmadas pelo revisor. Definir como "nunca citada
    por nenhuma linha" esconderia a maioria das perdas reais - medida contra os
    gabaritos dos clientes, essa definição mostra 10 de 21 respostas certas não
    consumidas, e quatro das escondidas nem aparecem numa visão indexada pelo
    modelo, porque nenhuma linha as oferece para o conceito verdadeiro.

    ``signals`` guarda ids de sinal internos separados por vírgula (traduzidos
    na gravação); ``signal_detail`` já é escrito em português.
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


#: Frases da coluna detalhe_sinais, unidas por "; ". ``slot``, ``catalog`` e
#: ``kind`` chegam como rótulos legíveis (``report_schema.TEXT_LABELS``);
#: ``distinct_coverage`` e ``fraction``, formatados com
#: ``report_schema.format_percent_br`` (``88,2%``).
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
    "Contém dados, mas nenhum sinal foi identificado. Listada apenas para que a "
    "relação fique completa."
)


def _unmatched_signals(
    *,
    column: ColumnRef,
    profile: dict[str, object],
    values: pd.Series,
    reference_catalogs: ReferenceCatalogs | None,
    trio_slots: dict[int, str],
) -> tuple[list[str], str]:
    """Motivos pelos quais uma coluna que sobrou pode importar, só por evidência universal.

    Todo sinal aqui é uma propriedade do dado fiscal brasileiro, e não do
    vocabulário de um cliente: pertencer a uma tabela oficial de códigos, ter
    dígito verificador, participar de valor = base x alíquota. É de propósito.
    Uma ordenação montada com as palavras dos cabeçalhos dos clientes de
    amostra ordenaria as colunas do próximo cliente pela semelhança com estes
    quatro, que é exatamente o contrário do que se quer.

    Dois cortes por vocabulário foram testados e recusados: exigir uma palavra
    fiscal elimina 'Cod. Produto' do datasul e 'ORGANIZACAO' do oracle, e uma
    lista de bloqueio de termos contábeis elimina 'Vlr Contabil' do datasul,
    que é uma resposta certa. Nada é filtrado por palavra.

    Devolve os ids de sinal (internos, traduzidos na gravação) e o detalhe já
    em português.
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
            evidence, final_threshold=1.0
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
        if fraction >= 0.80:
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
    """Todas as colunas do cliente cujos dados não chegam ao modelo base.

    Nada é omitido. Uma lista prioritária é marcada, porque 294 linhas não é
    um relatório que alguém leia, mas as demais continuam gravadas com uma
    frase dizendo por que ficaram de lado - duas regras de corte candidatas
    foram medidas e cada uma eliminava uma resposta certa, então o máximo que
    se faz aqui é rebaixar a prioridade.

    Uma lista longa aqui NÃO é sinal de defeito. O modelo base é um
    superconjunto de propósito, e um cliente legitimamente fornece só uma
    minoria dele, então a maioria das colunas do cliente não ter
    correspondente no modelo é o caso normal.

    Uma ressalva ao ler a lista prioritária: --offline é um regime degenerado
    para ela. Sem resolvedor, toda coluna não resolvida vira revisão em vez de
    sem correspondência, então "foi proposta" não traz informação e quase tudo
    entra na lista prioritária - 28 das 30 do oracle, contra 15 de 30 no mesmo
    cliente com o resolvedor ligado. Avalie a lista prioritária numa execução
    completa.
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
        # Uma proposta não precisa de sinal: o mapeador já produziu evidência
        # para ela, são poucas, e condicioná-las aos detectores derrubou, na
        # medição, a recuperação das respostas conhecidas de 21/21 para 18/21.
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


#: Justificativas escritas pelo próprio mapeador (coluna justificativa). A
#: justificativa que a IA escreve (``DataMappingDecision.reason``) passa
#: intacta, em inglês, porque o prompt continua em inglês.
REASON_NO_UNUSED = "Não restam colunas do cliente disponíveis."
REASON_PASS_ONE_ACCEPTED = (
    "Aceito pela semelhança do cabeçalho, com vantagem clara sobre a segunda melhor opção."
)
REASON_OFFLINE = (
    "A pontuação do cabeçalho ou a margem sobre a segunda melhor opção foi insuficiente; "
    "a comparação de dados estava desativada."
)
#: ``exc_type`` é o nome da classe da exceção; ``exc``, a mensagem dela.
REASON_DATA_ERROR = "A comparação de dados falhou: {exc_type}: {exc}"
REASON_UNIQUE_RELATIONSHIP = (
    "Uma relação fiscal única e de alta cobertura sustenta esta coluna frente às "
    "alternativas (por exemplo, base de cálculo vezes alíquota reproduz o valor)."
)
REASON_OVERLAP = (
    "Valores de amostra distintivos e diferentes de zero coincidem exatamente com este "
    "candidato, e não com as colunas concorrentes."
)
REASON_JOINT = (
    "O cabeçalho e uma relação fiscal verificada linha a linha sustentam juntos esta "
    "coluna, com vantagem clara sobre as alternativas."
)
REASON_NULL_FALLBACK = (
    "O resolvedor de IA encontrou evidências favoráveis, mas não escolheu nenhuma coluna; "
    "o candidato com o cabeçalho mais forte fica para revisão."
)
REASON_PRESERVE_BEST = (
    "O candidato com o cabeçalho mais forte tem evidência fraca nas linhas; o mapeamento "
    "por nome é mantido para revisão, em vez de ser trocado por uma coluna que só tem "
    "formato de valores parecido."
)
REASON_UNSUPPORTED_NO_MATCH = (
    "Nenhum candidato reuniu evidência suficiente de cabeçalho e de linhas distintivas; "
    "a exportação do cliente pode não conter este campo do modelo."
)
#: ``header_conflict`` é o conflito em português (``describe_header_conflict_pt``).
REASON_CONFLICT_REVIEW = (
    "Os cabeçalhos apresentam {header_conflict}; esta proposta fica para revisão porque "
    "o conteúdo das linhas traz evidência distintiva de que o cabeçalho do cliente pode "
    "estar errado."
)
REASON_CONTRADICTION = (
    "Uma equação fiscal de alta cobertura contradiz fortemente esta proposta; ela fica "
    "para revisão em vez de ser aceita automaticamente."
)
REASON_AMBIGUOUS_EMPTY = (
    "A correspondência de cabeçalho é ambígua e as amostras das linhas estão vazias ou "
    "zeradas; este mapeamento fica para revisão."
)
#: ``client_column`` é o nome de exibição já em português
#: (``report_schema.display_name_to_external``); sai entre aspas, por ``!r``.
REASON_VACUITY = (
    "{client_column!r} não tem linhas preenchidas, portanto não pode ser a origem desta "
    "coluna, por mais que os cabeçalhos coincidam. Não aceito."
)


def map_columns(
    model_table: SheetTable,
    client_table: SheetTable,
    header_scorer: HeaderScorer,
    data_resolver: DataResolver | None,
    config: MapperConfig = MapperConfig(),
    reference_catalogs: ReferenceCatalogs | None = None,
) -> list[MappingResult]:
    """Mapeia cada coluna do modelo para uma coluna do cliente, em duas passagens.

    A primeira passagem aceita só pelo cabeçalho, quando a pontuação e a
    vantagem sobre a segunda melhor opção são claras. A segunda revisita as
    colunas de baixa confiança com os dados das linhas: relações fiscais,
    catálogos oficiais, coincidência de amostras e, online, o resolvedor de
    IA. Devolve um ``MappingResult`` por coluna do modelo, na ordem do modelo.
    """
    if not client_table.columns:
        raise ValueError("A planilha do cliente não tem colunas")

    # Qual etapa de cabeçalho rodou é uma propriedade do avaliador, não de
    # uma coluna.
    header_step = "lexical" if isinstance(header_scorer, LexicalHeaderScorer) else "semantic"
    similarities = header_scorer.scores(
        [column.name for column in model_table.columns],
        [column.name for column in client_table.columns],
    )
    expected_shape = (len(model_table.columns), len(client_table.columns))
    if similarities.shape != expected_shape:
        raise ValueError(
            f"O avaliador de cabeçalhos retornou {similarities.shape}; esperado {expected_shape}"
        )

    # Descobertos uma vez, das colunas do próprio cliente. Independem do
    # modelo, da ordem de aceite e de qualquer vocabulário de cabeçalho além de
    # família e papel.
    client_tax_trios = discover_client_tax_trios(client_table)

    available = set(range(len(client_table.columns)))
    results: dict[int, MappingResult] = {}
    low_confidence_targets: list[int] = []
    catalog_analysis_cache: dict[tuple[str, int], dict[str, object]] = {}

    # Primeira passagem: percorre as colunas do modelo da esquerda para a
    # direita usando só os nomes.
    for target_index, model_column in enumerate(model_table.columns):
        ranked = _rank_header_compatible(
            target_index,
            similarities,
            available,
            model_column,
            client_table.columns,
            2,
        )
        if not ranked:
            results[target_index] = MappingResult(
                model_column_number=model_column.index + 1,
                model_column=model_column.name,
                client_column_number=None,
                client_column=None,
                name_confidence=0.0,
                name_runner_up_confidence=0.0,
                name_margin=0.0,
                data_confidence=None,
                final_confidence=0.0,
                method="name",
                status="no_match",
                reason=REASON_NO_UNUSED,
            )
            continue

        client_index, name_confidence = ranked[0]
        _, runner_up_confidence, name_margin = _name_score_context(ranked)
        client_column = client_table.columns[client_index]
        header_conflict = semantic_header_conflict(
            model_column.name, client_column.name
        )
        if (
            name_confidence >= config.name_threshold
            and name_margin >= config.name_margin_threshold
            and header_conflict is None
            and not underspecified_header_match(
                model_column.name, client_column.name
            )
            and not client_column_is_vacuous(client_table, client_column)
        ):
            results[target_index] = MappingResult(
                model_column_number=model_column.index + 1,
                model_column=model_column.name,
                client_column_number=client_column.index + 1,
                client_column=client_column.display_name,
                name_confidence=round(name_confidence, 4),
                name_runner_up_confidence=round(runner_up_confidence, 4),
                name_margin=round(name_margin, 4),
                data_confidence=None,
                final_confidence=round(name_confidence, 4),
                method="name",
                status="accepted",
                reason=REASON_PASS_ONE_ACCEPTED,
                arithmetic_proof=(proof := _trio_proof_text(
                    trio_corroboration(client_tax_trios, model_column.name, client_column.index)
                )),
                supported_by=format_steps(
                    steps := _supporting_step_ids(
                        header_step=header_step,
                        name_confidence=name_confidence,
                        arithmetic_proof=proof,
                    )
                ),
                decided_by=deciding_step(steps),
            )
            available.remove(client_index)
        else:
            low_confidence_targets.append(target_index)

    _audit_accepted_catalog_matches(
        model_table=model_table,
        client_table=client_table,
        results=results,
        available=available,
        low_confidence_targets=low_confidence_targets,
        reference_catalogs=reference_catalogs,
        analysis_cache=catalog_analysis_cache,
    )

    numeric_client_series = {
        index: numeric_series(client_table.series(column))
        for index, column in enumerate(client_table.columns)
    }
    _demote_inconsistent_tax_header_matches(
        model_table=model_table,
        client_table=client_table,
        similarities=similarities,
        results=results,
        available=available,
        low_confidence_targets=low_confidence_targets,
        numeric_client_series=numeric_client_series,
    )
    low_confidence_targets.sort()

    client_profiles = (
        {
            index: summarize_values(
                client_table.series(client_table.columns[index]), config.sample_size
            )
            for index in range(len(client_table.columns))
        }
        if data_resolver is not None
        else {}
    )
    # Segunda passagem: revisita só as colunas do modelo de baixa confiança,
    # usando os nomes e os perfis das linhas.
    for target_index in low_confidence_targets:
        model_column = model_table.columns[target_index]
        target_profile = summarize_values(
            model_table.series(model_column), config.sample_size
        )
        candidate_relationships = (
            _candidate_relationship_evidence(
                target_index=target_index,
                candidate_indices=available,
                model_table=model_table,
                client_table=client_table,
                results=results,
                numeric_client_series=numeric_client_series,
            )
            if data_resolver is not None
            else {}
        )
        candidate_catalogs = _candidate_catalog_evidence(
            target_index=target_index,
            candidate_indices=available,
            model_table=model_table,
            client_table=client_table,
            reference_catalogs=reference_catalogs,
            analysis_cache=catalog_analysis_cache,
        )
        if data_resolver is None:
            ranked = _rank_available(
                target_index,
                similarities,
                available,
                config.candidate_count,
            )
        else:
            ranked = _rank_name_and_profile_candidates(
                target_index,
                similarities,
                available,
                target_profile,
                client_profiles,
                config.candidate_count,
                candidate_relationships,
                candidate_catalogs,
            )
        if not ranked:
            results[target_index] = MappingResult(
                model_column_number=model_column.index + 1,
                model_column=model_column.name,
                client_column_number=None,
                client_column=None,
                name_confidence=0.0,
                name_runner_up_confidence=0.0,
                name_margin=0.0,
                data_confidence=None,
                final_confidence=0.0,
                method="data",
                status="no_match",
                reason=REASON_NO_UNUSED,
            )
            continue

        compatible_ranked = [
            item
            for item in ranked
            if semantic_header_conflict(
                model_column.name, client_table.columns[item[0]].name
            )
            is None
        ]
        fallback_ranked = compatible_ranked or ranked
        best_client_index, best_name_confidence = fallback_ranked[0]
        _, best_runner_up_confidence, best_name_margin = _name_score_context(
            fallback_ranked
        )
        best_client = client_table.columns[best_client_index]
        if data_resolver is None:
            results[target_index] = MappingResult(
                model_column_number=model_column.index + 1,
                model_column=model_column.name,
                client_column_number=best_client.index + 1,
                client_column=best_client.display_name,
                name_confidence=round(best_name_confidence, 4),
                name_runner_up_confidence=round(best_runner_up_confidence, 4),
                name_margin=round(best_name_margin, 4),
                data_confidence=None,
                final_confidence=round(best_name_confidence, 4),
                method="name_only",
                status="review",
                reason=REASON_OFFLINE,
            )
            continue

        candidates = [
            (
                client_table.columns[client_index],
                client_table.series(client_table.columns[client_index]),
                name_confidence,
            )
            for client_index, name_confidence in ranked
        ]
        catalog_decision = _unique_distinctive_catalog_decision(
            candidates=candidates,
            catalog_evidence=candidate_catalogs,
        )
        decision_method = "catalog" if catalog_decision is not None else "data"
        try:
            decision = catalog_decision or data_resolver.resolve(
                model_column=model_column,
                model_values=model_table.series(model_column),
                candidates=candidates,
                sample_size=config.sample_size,
                relationship_evidence={
                    client_table.columns[index].id: rules
                    for index, rules in candidate_relationships.items()
                    if any(column.index == index for column, _, _ in candidates)
                },
                catalog_evidence={
                    client_table.columns[index].id: matches
                    for index, matches in candidate_catalogs.items()
                    if any(column.index == index for column, _, _ in candidates)
                },
            )
        except Exception as exc:  # Segue com o relatório mesmo que uma decisão da API falhe.
            results[target_index] = MappingResult(
                model_column_number=model_column.index + 1,
                model_column=model_column.name,
                client_column_number=best_client.index + 1,
                client_column=best_client.display_name,
                name_confidence=round(best_name_confidence, 4),
                name_runner_up_confidence=round(best_runner_up_confidence, 4),
                name_margin=round(best_name_margin, 4),
                data_confidence=None,
                final_confidence=round(best_name_confidence, 4),
                method="data_error",
                status="review",
                reason=REASON_DATA_ERROR.format(exc_type=type(exc).__name__, exc=exc),
            )
            continue

        llm_data_confidence = (
            round(decision.data_confidence, 4) if catalog_decision is None else None
        )
        llm_final_confidence = (
            round(decision.final_confidence, 4) if catalog_decision is None else None
        )

        candidate_by_id = {column.id: column for column, _, _ in candidates}
        candidate_name_scores = {
            column.index: name_confidence
            for column, _, name_confidence in candidates
        }
        chosen = candidate_by_id.get(decision.client_column_id or "")
        relationship_is_unique = False
        relationship_ranked = sorted(
            (
                (
                    _relationship_score(candidate_relationships.get(column.index, [])),
                    candidate_name_scores[column.index],
                    column,
                )
                for column, _, _ in candidates
                if semantic_header_conflict(model_column.name, column.name) is None
            ),
            key=lambda item: (-item[0], -item[1], item[2].index),
        )
        if relationship_ranked:
            best_relationship_score, relational_name_score, relational_column = (
                relationship_ranked[0]
            )
            second_relationship_score = (
                relationship_ranked[1][0] if len(relationship_ranked) > 1 else 0.0
            )
            relationship_is_unique = (
                best_relationship_score >= config.final_threshold
                and best_relationship_score - second_relationship_score >= 0.10
            )
            if relationship_is_unique:
                same_candidate = (
                    chosen is not None and chosen.index == relational_column.index
                )
                chosen = relational_column
                decision_method = "data"
                relational_final_confidence = round(
                    0.4 * relational_name_score + 0.6 * best_relationship_score,
                    4,
                )
                decision = decision.model_copy(
                    update={
                        "client_column_id": relational_column.id,
                        "data_confidence": (
                            max(decision.data_confidence, best_relationship_score)
                            if same_candidate
                            else best_relationship_score
                        ),
                        "final_confidence": (
                            max(decision.final_confidence, relational_final_confidence)
                            if same_candidate
                            else relational_final_confidence
                        ),
                        "reason": REASON_UNIQUE_RELATIONSHIP,
                    }
                )
        if not relationship_is_unique:
            overlap_ranked = sorted(
                (
                    (
                        distinctive_sample_overlap(
                            target_profile, client_profiles[column.index]
                        ),
                        distinctive_sample_overlap_count(
                            target_profile, client_profiles[column.index]
                        ),
                        candidate_name_scores[column.index],
                        column,
                    )
                    for column, _, _ in candidates
                ),
                key=lambda item: (-item[1], -item[0], -item[2], item[3].index),
            )
            best_overlap, best_overlap_count, overlap_name_score, overlap_column = (
                overlap_ranked[0]
            )
            second_overlap_count = overlap_ranked[1][1] if len(overlap_ranked) > 1 else 0
            second_overlap = overlap_ranked[1][0] if len(overlap_ranked) > 1 else 0.0
            chosen_overlap = (
                distinctive_sample_overlap(
                    target_profile, client_profiles[chosen.index]
                )
                if chosen is not None
                else 0.0
            )
            chosen_name_score = (
                candidate_name_scores[chosen.index] if chosen is not None else 0.0
            )
            if (
                best_overlap_count >= 2
                and (
                    best_overlap_count > second_overlap_count
                    or best_overlap > second_overlap
                )
                and best_overlap > chosen_overlap
                and (
                    overlap_name_score >= chosen_name_score - 0.10
                    or best_overlap >= 0.50
                )
            ):
                chosen = overlap_column
                decision_method = "data"
                overlap_data_confidence = min(0.99, 0.90 + 0.09 * best_overlap)
                overlap_final_confidence = max(
                    0.80,
                    0.35 * overlap_name_score + 0.65 * overlap_data_confidence,
                )
                if best_overlap >= 0.50:
                    overlap_final_confidence = max(
                        0.90, overlap_final_confidence
                    )
                decision = decision.model_copy(
                    update={
                        "client_column_id": overlap_column.id,
                        "data_confidence": round(overlap_data_confidence, 4),
                        "final_confidence": round(overlap_final_confidence, 4),
                        "reason": REASON_OVERLAP,
                    }
                )

        # Uma equação fiscal também pode convencer quando mais de uma coluna
        # numérica por acaso se encaixa nela. Nesse caso, combina a relação com
        # a pontuação do cabeçalho e exige vantagem clara na pontuação
        # combinada. Isso evita descartar um bom mapeamento de base, alíquota
        # ou valor só porque uma coluna de total amplo produz, por
        # coincidência, uma aritmética parecida.
        if chosen is not None:
            selected_relationship_score = _relationship_score(
                candidate_relationships.get(chosen.index, [])
            )
            if selected_relationship_score >= config.final_threshold:
                joint_ranked = sorted(
                    (
                        (
                            0.4 * candidate_name_scores[column.index]
                            + 0.6
                            * _relationship_score(
                                candidate_relationships.get(column.index, [])
                            ),
                            column,
                        )
                        for column, _, _ in candidates
                        if semantic_header_conflict(model_column.name, column.name)
                        is None
                    ),
                    key=lambda item: (-item[0], item[1].index),
                )
                selected_joint_score = 0.4 * candidate_name_scores[chosen.index] + 0.6 * (
                    selected_relationship_score
                )
                second_joint_score = max(
                    (
                        score
                        for score, column in joint_ranked
                        if column.index != chosen.index
                    ),
                    default=0.0,
                )
                if (
                    selected_joint_score >= config.final_threshold
                    and selected_joint_score - second_joint_score
                    >= config.name_margin_threshold
                ):
                    decision = decision.model_copy(
                        update={
                            "data_confidence": max(
                                decision.data_confidence,
                                selected_relationship_score,
                            ),
                            "final_confidence": max(
                                decision.final_confidence,
                                round(selected_joint_score, 4),
                            ),
                            "reason": REASON_JOINT,
                        }
                    )
        best_same_explicit_role = (
            header_semantic_role(model_column.name) is not None
            and header_semantic_role(model_column.name)
            == header_semantic_role(best_client.name)
        )
        if (
            chosen is None
            and (
                (
                    decision.data_confidence
                    >= max(
                        0.0,
                        config.final_threshold
                        - 2 * config.name_margin_threshold,
                    )
                    and best_name_confidence
                    >= config.name_threshold - config.name_margin_threshold
                )
                or (
                    best_same_explicit_role
                    and best_name_confidence
                    >= config.name_threshold - 2 * config.name_margin_threshold
                )
            )
        ):
            chosen = best_client
            decision = decision.model_copy(
                update={
                    "client_column_id": best_client.id,
                    "final_confidence": best_name_confidence,
                    "reason": REASON_NULL_FALLBACK,
                }
            )
        best_profile = client_profiles[best_client_index]
        best_rows_are_weak = (
            target_profile["evidence_quality"] in {"empty", "all_zero"}
            or best_profile["evidence_quality"] in {"empty", "all_zero"}
        )
        preserve_best_name = (
            best_name_confidence
            >= config.name_threshold - config.name_margin_threshold
            and best_name_margin >= config.name_margin_threshold
            and best_rows_are_weak
        )
        if preserve_best_name and (
            chosen is None or chosen.index != best_client_index
        ) and (
            chosen is None
            or _relationship_score(candidate_relationships.get(chosen.index, []))
            < config.final_threshold
        ) and (
            chosen is None
            or _catalog_score(candidate_catalogs.get(chosen.index, []))
            < config.final_threshold
        ):
            chosen = best_client
            decision = decision.model_copy(
                update={
                    "client_column_id": best_client.id,
                    "data_confidence": 0.0,
                    "final_confidence": best_name_confidence,
                    "reason": REASON_PRESERVE_BEST,
                }
            )
        if chosen is None or decision.final_confidence == 0.0:
            results[target_index] = MappingResult(
                model_column_number=model_column.index + 1,
                model_column=model_column.name,
                client_column_number=None,
                client_column=None,
                name_confidence=round(best_name_confidence, 4),
                name_runner_up_confidence=round(best_runner_up_confidence, 4),
                name_margin=round(best_name_margin, 4),
                data_confidence=round(decision.data_confidence, 4),
                final_confidence=0.0,
                method="data",
                status="no_match",
                reason=decision.reason,
                llm_data_confidence=llm_data_confidence,
                llm_final_confidence=llm_final_confidence,
            )
            continue

        compatible_ranked = [
            item
            for item in ranked
            if semantic_header_conflict(
                model_column.name, client_table.columns[item[0]].name
            )
            is None
        ]
        name_context_ranked = (
            compatible_ranked
            if any(index == chosen.index for index, _ in compatible_ranked)
            else ranked
        )
        chosen_name_confidence, runner_up_confidence, name_margin = _name_score_context(
            name_context_ranked, chosen.index
        )
        selected_profile = client_profiles[chosen.index]
        selected_relationship_score = _relationship_score(
            candidate_relationships.get(chosen.index, [])
        )
        selected_catalog = best_catalog_evidence(
            candidate_catalogs.get(chosen.index, [])
        )
        selected_catalog_score = (
            float(selected_catalog["score"]) if selected_catalog is not None else 0.0
        )
        selected_catalog_coverage = (
            float(selected_catalog["coverage"])
            if selected_catalog is not None
            else 0.0
        )
        # As travas leem a cobertura distinta - a estatística da qual
        # catalog_confidence é calculada -, enquanto a coluna catalog_coverage
        # do relatório mantém o sentido original, ponderado pelas linhas, para
        # que os relatórios existentes continuem legíveis.
        selected_catalog_distinct_coverage = (
            float(selected_catalog.get("distinct_coverage", selected_catalog["coverage"]))
            if selected_catalog is not None
            else 0.0
        )
        header_conflict = semantic_header_conflict(
            model_column.name, chosen.name
        )
        name_is_decisive = (
            chosen_name_confidence >= config.name_threshold
            and name_margin >= config.name_margin_threshold
            and not underspecified_header_match(model_column.name, chosen.name)
        )
        same_explicit_role = (
            header_semantic_role(model_column.name) is not None
            and header_semantic_role(model_column.name)
            == header_semantic_role(chosen.name)
        )
        overlap = distinctive_sample_overlap(target_profile, selected_profile)
        overlap_count = distinctive_sample_overlap_count(
            target_profile, selected_profile
        )
        overlap_score = (
            min(0.99, 0.90 + 0.09 * overlap) if overlap_count >= 2 else 0.0
        )
        validated_identifier_score = identifier_match_score(
            target_profile, selected_profile
        )
        catalog_objective_score = (
            selected_catalog_score
            if catalog_evidence_is_distinctive(
                selected_catalog, final_threshold=config.final_threshold
            )
            else 0.0
        )
        objective_data_score = max(
            selected_relationship_score,
            catalog_objective_score,
            overlap_score,
            validated_identifier_score,
        )
        generic_profile_score = min(
            0.84, profile_similarity(target_profile, selected_profile)
        )
        local_data_confidence = round(
            max(objective_data_score, generic_profile_score), 4
        )
        if name_is_decisive:
            final_confidence = round(chosen_name_confidence, 4)
        elif objective_data_score >= config.final_threshold:
            objective_floor = (
                config.final_threshold
                if same_explicit_role
                or chosen_name_confidence
                >= config.name_threshold - 2 * config.name_margin_threshold
                else 0.80
            )
            final_confidence = round(
                max(
                    objective_floor,
                    0.40 * chosen_name_confidence + 0.60 * objective_data_score,
                ),
                4,
            )
        else:
            final_confidence = round(
                min(
                    config.final_threshold - 0.0001,
                    max(
                        chosen_name_confidence,
                        0.70 * chosen_name_confidence
                        + 0.30 * local_data_confidence,
                    ),
                ),
                4,
            )
        relationship_contradiction = _relationship_is_strong_contradiction(
            candidate_relationships.get(chosen.index, [])
        )
        data_is_decisive = objective_data_score >= config.final_threshold
        accepted = (
            final_confidence >= config.final_threshold
            and (name_is_decisive or data_is_decisive)
            and header_conflict is None
            and not relationship_contradiction
            and not client_column_is_vacuous(client_table, chosen)
        )
        reason = decision.reason
        review_is_supported = _review_proposal_is_supported(
            name_confidence=chosen_name_confidence,
            data_confidence=local_data_confidence,
            final_confidence=final_confidence,
            header_conflict=header_conflict,
            target_profile=target_profile,
            selected_profile=selected_profile,
            relationship_score=selected_relationship_score,
            catalog_score=selected_catalog_score,
            catalog_coverage=selected_catalog_distinct_coverage,
            same_explicit_role=same_explicit_role,
            config=config,
        )
        if not accepted and not review_is_supported:
            results[target_index] = MappingResult(
                model_column_number=model_column.index + 1,
                model_column=model_column.name,
                client_column_number=None,
                client_column=None,
                name_confidence=round(chosen_name_confidence, 4),
                name_runner_up_confidence=round(runner_up_confidence, 4),
                name_margin=round(name_margin, 4),
                data_confidence=local_data_confidence,
                final_confidence=0.0,
                method="data",
                status="no_match",
                reason=REASON_UNSUPPORTED_NO_MATCH,
                llm_data_confidence=llm_data_confidence,
                llm_final_confidence=llm_final_confidence,
            )
            continue
        if header_conflict is not None:
            # header_conflict (inglês) decide; a frase mostra o mesmo conflito
            # em português, calculado das mesmas entradas.
            reason = REASON_CONFLICT_REVIEW.format(
                header_conflict=describe_header_conflict_pt(model_column.name, chosen.name)
            )
        elif relationship_contradiction:
            reason = REASON_CONTRADICTION
        elif final_confidence >= config.final_threshold and not accepted:
            reason = REASON_AMBIGUOUS_EMPTY
        accepted_but_for_vacuity = (
            not accepted
            and final_confidence >= config.final_threshold
            and (name_is_decisive or data_is_decisive)
            and header_conflict is None
            and not relationship_contradiction
        )
        if accepted_but_for_vacuity:
            # Diz isso com todas as letras. Todo o resto desta proposta pode
            # parecer forte, e um revisor informado só de que a pontuação do
            # cabeçalho foi insuficiente iria procurar o problema errado.
            reason = REASON_VACUITY.format(
                client_column=report_schema.display_name_to_external(chosen.display_name)
            )
        results[target_index] = MappingResult(
            model_column_number=model_column.index + 1,
            model_column=model_column.name,
            client_column_number=chosen.index + 1,
            client_column=chosen.display_name,
            name_confidence=round(chosen_name_confidence, 4),
            name_runner_up_confidence=round(runner_up_confidence, 4),
            name_margin=round(name_margin, 4),
            data_confidence=local_data_confidence,
            final_confidence=final_confidence,
            method=decision_method,
            status="accepted" if accepted else "review",
            reason=reason,
            arithmetic_proof=(proof := _trio_proof_text(
                trio_corroboration(client_tax_trios, model_column.name, chosen.index)
            )),
            supported_by=format_steps(
                steps := _supporting_step_ids(
                    header_step=header_step,
                    name_confidence=chosen_name_confidence,
                    relationship_score=selected_relationship_score,
                    catalog_score=catalog_objective_score,
                    overlap_score=overlap_score,
                    identifier_score=validated_identifier_score,
                    arithmetic_proof=proof,
                    resolver_ran=llm_final_confidence is not None,
                )
            ),
            decided_by=deciding_step(steps),
            vetoed_by=(
                "vacuity" if accepted_but_for_vacuity
                else "family_conflict" if header_conflict is not None
                else ""
            ),
            catalog_name=(
                str(selected_catalog["catalog"])
                if selected_catalog is not None
                else None
            ),
            catalog_coverage=(
                round(selected_catalog_coverage, 4)
                if selected_catalog is not None
                else None
            ),
            catalog_confidence=(
                round(selected_catalog_score, 4)
                if selected_catalog is not None
                else None
            ),
            llm_data_confidence=llm_data_confidence,
            llm_final_confidence=llm_final_confidence,
        )
        if accepted:
            available.remove(chosen.index)

    return [results[index] for index in range(len(model_table.columns))]


def file_sha256(path: str | Path) -> str:
    """SHA-256 do arquivo, lido em blocos de 1 MiB."""
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


@dataclass
class AdjudicationRow:
    """Uma coluna do modelo que recebeu um candidato real que o limiar de aceite não entregou.

    O espelho de UnmatchedColumn. Aquela aba responde "quais colunas do
    cliente nunca chegaram ao modelo"; esta responde "quais colunas do modelo
    tinham uma resposta que deixamos de entregar". Juntas, elas cobrem os dois
    lados do limiar, que é onde estão as perdas que restam ao mapeador: medido
    contra os gabaritos, o mapeador encontra 42 de 56 e entrega 31, e as 11 de
    diferença são todas linhas que aparecem aqui.

    É de propósito um ÍNDICE ordenado, e não um segundo lugar para escrever. O
    revisor decide nas colunas parecer_revisor / coluna_cliente_correta que já
    existem na aba Mapeamentos, que o apply_invoice_mapping já respeita e que
    agora recusam as duas entradas incoerentes que antes passavam em silêncio.
    Um segundo vocabulário significaria dois lugares para escrever, uma regra
    para juntá-los e um jeito novo de discordar de si mesmo.

    ``why_held`` e ``resolver_disagrees`` já são escritos em português.
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


#: Linhas tão próximas do limiar compartilham uma única confiança travada,
#: então a coluna de confiança deixa de ordená-las e outra coisa precisa
#: fazer isso.
_CLAMPED_BAND = 0.0002

#: Motivos de retenção (coluna motivo_retencao). São os valores em português
#: de ``report_schema.ENUMS["why_held"]``, que também traduz os legados.
WHY_HELD_BELOW_THRESHOLD = "abaixo do limiar de aceite"
WHY_HELD_NAME_ONLY = (
    "só há evidência de nome - este caminho não aceita automaticamente, por mais que "
    "os cabeçalhos coincidam"
)
WHY_HELD_VACUOUS = (
    "a coluna proposta não tem linhas preenchidas - confirmá-la entregaria uma coluna "
    "em branco"
)
#: Coluna divergencia_ia. ``llm_final`` chega com vírgula decimal
#: (``report_schema.format_decimal_br``: ``0,75``).
RESOLVER_DISAGREES = "o resolvedor de IA atribuiu confiança {llm_final}"


def summarize_adjudication_queue(
    *,
    client_table: SheetTable,
    results: Sequence[MappingResult],
    final_threshold: float,
    sample_size: int,
) -> list[AdjudicationRow]:
    """Ordena as propostas não entregues pela proximidade de cada uma do aceite."""
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
        # Uma proposta para uma coluna sem dados nunca pode ser confirmada:
        # confirmá-la entrega uma coluna em branco com a assinatura do revisor.
        # Ela continua na fila - o revisor deve ver que o cabeçalho casou e os
        # dados não -, mas não pode ocupar as primeiras posições. 'FCI number'
        # do sap_terumo casa com 'No FCI' a 0.98 e está vazia nas 727 linhas,
        # o que bastaria para pô-la em primeiro sem isto.
        vacuous = client_column_is_vacuous(client_table, column)
        llm_final = result.llm_final_confidence
        disagrees = (
            RESOLVER_DISAGREES.format(llm_final=report_schema.format_decimal_br(llm_final))
            if llm_final is not None and float(llm_final) < final - 0.10
            else ""
        )
        # A trava do ramo 'sem pontuação objetiva de dados' prende a linha logo
        # abaixo do limiar, qualquer que seja a evidência de nome; então
        # 'retida em 0.8999' diz algo sobre o ramo seguido, não sobre a força
        # desta linha.
        clamped = abs(final - (final_threshold - 0.0001)) < 1e-9
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
    # Primeiro a confiança, depois a margem - mas a margem só desempata dentro
    # da faixa travada. Promovê-la em geral seria ajustar a um cliente: a
    # vantagem aparente dela só existe no datasul, empata no oracle e cai
    # para perto de zero quando os cabeçalhos são truncados.
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


def _external_rows(
    rows: Sequence[UnmatchedColumn] | Sequence[AdjudicationRow],
    row_type: type,
    table: str,
) -> pd.DataFrame:
    """Linhas de uma aba auxiliar -> quadro no esquema em português.

    Uma lista vazia vira uma única linha em branco, para que a aba saia com os
    cabeçalhos (já em português) mesmo sem nenhuma linha.
    """
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
    """Grava o relatório de mapeamento no esquema em português (``.xlsx`` ou ``.csv``).

    Em memória tudo continua com os nomes e valores internos; a tradução de
    abas, cabeçalhos, valores enumerados, etapas, nomes de exibição e chaves de
    metadados acontece aqui, por ``report_schema``. As frases já chegam em
    português e passam intactas. O ``.csv`` leva só a aba Mapeamentos.
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
            # Acrescentadas POR ÚLTIMO, e de propósito. O apply_invoice_mapping
            # lê a aba de índice 0, sem nome, então qualquer aba inserida antes
            # de Mapeamentos o faz falhar com um erro enganoso de colunas
            # faltando.
            #
            # Gravadas sempre que a lista `is not None`, mesmo vazia: uma lista
            # vazia afirma "todas as suas colunas chegaram ao modelo", o que é
            # diferente, e mais forte, do que um relatório antigo que nem tem
            # essa aba.
            if unmatched is not None:
                _external_rows(unmatched, UnmatchedColumn, "unmatched").to_excel(
                    writer, sheet_name=report_schema.sheet_name("unmatched"), index=False
                )
            if queue is not None:
                _external_rows(queue, AdjudicationRow, "queue").to_excel(
                    writer, sheet_name=report_schema.sheet_name("queue"), index=False
                )
    else:
        raise ValueError("A saída deve terminar em .xlsx ou .csv")


def _sheet_argument(value: str) -> str | int:
    """Aba indicada na linha de comando: só dígitos é índice (a partir de 0), senão é nome."""
    return int(value) if value.isdigit() else value


def build_parser() -> PortugueseArgumentParser:
    """Linha de comando em português. As flags continuam em inglês."""
    parser = PortugueseArgumentParser(
        description=(
            "Mapeia as colunas da planilha do cliente para as colunas do model.xlsx "
            "em duas passagens e grava o relatório de mapeamento."
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
            "relatório de mapeamento a gravar, .xlsx ou .csv "
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
        default=0.90,
        metavar="VALOR",
        help=(
            "pontuação mínima do cabeçalho para o aceite na primeira passagem, "
            "de 0 a 1 (padrão: %(default)s)"
        ),
    )
    parser.add_argument(
        "--name-margin",
        type=float,
        default=0.05,
        metavar="VALOR",
        help=(
            "vantagem mínima sobre o segundo melhor candidato de cabeçalho para o "
            "aceite na primeira passagem, de 0 a 1 (padrão: %(default)s)"
        ),
    )
    parser.add_argument(
        "--final-threshold",
        type=float,
        default=0.90,
        metavar="VALOR",
        help="limiar de aceite da confiança final, de 0 a 1 (padrão: %(default)s)",
    )
    parser.add_argument(
        "--candidates",
        type=int,
        default=10,
        metavar="N",
        help=(
            "quantidade de candidatos por critério na segunda passagem "
            "(padrão: %(default)s)"
        ),
    )
    parser.add_argument(
        "--sample-size",
        type=int,
        default=8,
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
            "modelo de linguagem da OpenAI usado como resolvedor de IA "
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
            "usa só a comparação léxica dos nomes e dispensa a OpenAI (embeddings e "
            "comparação de dados)"
        ),
    )
    return parser


def _error_message(exc: ValueError | OSError) -> str:
    """Mensagem em português para um erro de leitura ou gravação na linha de comando."""
    if isinstance(exc, FileNotFoundError):
        return f"Arquivo não encontrado: {exc.filename}"
    if isinstance(exc, PermissionError):
        return f"Sem permissão para acessar o arquivo: {exc.filename}"
    if isinstance(exc, OSError):
        target = f" {exc.filename}" if exc.filename else ""
        return f"Não foi possível ler ou gravar o arquivo{target}: {exc.strerror or exc}"
    return f"Não foi possível gerar o relatório de mapeamento: {exc}"


def main(argv: Sequence[str] | None = None) -> int:
    """Ponto de entrada da linha de comando; devolve 0 em caso de sucesso.

    Erros de valor e de arquivo (``ValueError`` e ``OSError``) saem com uma
    mensagem em português e código de saída 1, sem traceback, como no
    ``apply_invoice_mapping``.
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
    # Nomes de arquivo continuam em inglês (mapping_<cliente>.xlsx), como os
    # dos relatórios em data/reports/.
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

    results = map_columns(
        model_table=model_table,
        client_table=client_table,
        header_scorer=header_scorer,
        data_resolver=data_resolver,
        config=MapperConfig(
            name_threshold=args.name_threshold,
            name_margin_threshold=args.name_margin,
            final_threshold=args.final_threshold,
            candidate_count=args.candidates,
            sample_size=args.sample_size,
        ),
        reference_catalogs=reference_catalogs,
    )
    manifest_path = Path(args.reference_data) / "manifest.json"
    # Chaves e sentinelas internos (offline, disabled); write_results os grava
    # em português pela aba Metadados da Execução.
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
    # A dica da coluna do modelo mais próxima é calculada de forma léxica, em
    # vez de reutilizar o avaliador da execução: é uma conveniência para o
    # revisor, e recalcular embeddings só para uma dica gastaria dinheiro à toa.
    unmatched = summarize_unmatched_columns(
        model_table=model_table,
        client_table=client_table,
        results={index: result for index, result in enumerate(results)},
        similarities=LexicalHeaderScorer().scores(
            [column.name for column in model_table.columns],
            [column.name for column in client_table.columns],
        ),
        reference_catalogs=reference_catalogs,
        client_tax_trios=discover_client_tax_trios(client_table),
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
    # Os rótulos são os valores da coluna situacao no relatório.
    print(f"aceito={accepted} revisao={review} sem_correspondencia={no_match}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
