"""Two-pass mapper from a client's invoice spreadsheet to model.xlsx columns."""

from __future__ import annotations

import argparse
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

from reference_catalog import ReferenceCatalogs, best_catalog_evidence, expected_catalogs


DEFAULT_EMBEDDING_MODEL = "text-embedding-3-large"
DEFAULT_LLM_MODEL = "gpt-4o-mini"
SEMANTIC_EQUIVALENCE_SCORE = 0.98
MAPPING_SCHEMA_VERSION = 2
PROMPT_VERSION = "2026-08-04.2"
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
    """Normalize reusable Portuguese/English terms without client-layout rules."""
    normalized = normalize_header(value)
    # CST is the standard Brazilian abbreviation for tax-situation code. This
    # replacement is domain vocabulary, not a mapping learned from a client file.
    normalized = re.sub(r"\btax\s+situation\b", "cst", normalized)
    normalized = re.sub(r"\bsituacao\s+tributaria\b", "cst", normalized)
    tokens = []
    for token in normalized.split():
        if token in _HEADER_STOPWORDS:
            continue
        tokens.append(_HEADER_TOKEN_ALIASES.get(token, token))
    # Some ERP headers truncate PIS to PI. Only expand it when CST/tax-situation
    # context makes the meaning unambiguous.
    if "cst" in tokens:
        tokens = ["pis" if token == "pi" else token for token in tokens]
    return frozenset(tokens)


def semantically_equivalent_headers(left: object, right: object) -> bool:
    left_signature = semantic_header_signature(left)
    return bool(left_signature) and left_signature == semantic_header_signature(right)


def underspecified_header_match(model_header: object, client_header: object) -> bool:
    """Return whether the client header drops all but one model concept.

    A single generic word such as ``Centro`` is useful as a proposal for
    ``Centro custo``, but it is not enough for automatic acceptance. Distinctive
    row or catalog evidence can still promote the proposal in the second pass.
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
    """Return explicit tax families found in a header using domain vocabulary."""
    families = {
        _TAX_FAMILY_ALIASES.get(token, token)
        for token in semantic_header_signature(value)
    }
    return frozenset(families & _TAX_FAMILIES)


#: Role words grouped by the concept they name. Every ERP truncates headers -
#: SAP to about 20 characters, Datasul to 12 - so the role word arrives cut
#: short far more often than not: "Vlr Isen ICM", "Vlr Outr IPI".
#:
#: These groups are prefix-unambiguous: measured over lengths 2 to 6, no prefix
#: of any word here reaches two different concepts. That is what makes
#: expansion safe by construction rather than by tuning, and it is asserted by
#: a test so a future addition cannot quietly break it.
_ROLE_CONCEPT_WORDS: dict[str, frozenset[str]] = {
    "other": frozenset({"outra", "outras", "outro", "outros", "other"}),
    "exempt": frozenset({"isenta", "isentas", "isento", "isentos", "exempt"}),
    "reduced": frozenset({"reduzida", "reduzido", "reduced"}),
}

#: Shorter than this and a token is noise rather than a truncation.
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
    """Add the full role word for tokens that are truncations of one.

    A prefix only expands when it reaches exactly one concept, so this cannot
    turn an "outbound" into an "outras" by reaching two meanings at once - it
    can only be wrong if a token that is not a truncation happens to prefix a
    role word and nothing else. Expansion is additive: the original token stays,
    so nothing that already worked stops working.
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
    """Return an explicit tax-value role without guessing from row values."""
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
    # A tax-family name can smuggle a rate signal into the signature: DIF_ALIQ
    # tokenises to include ``aliq``, which the alias table expands to
    # ``aliquota``, so every column of that block - Base, Valor, Isentas,
    # Outras, Base Reduzida - reported role ``rate`` before this guard. When
    # the header also states an explicit slot, the explicit word wins; the
    # rate reading survives wherever it is the only role evidence, which is
    # what keeps abbreviations like ``Aliq. PIS`` and ``Alíq. ICMS`` working.
    explicit_slot = (
        {"outra", "outras", "outro", "other"} & tokens
        or {"isenta", "isentas", "isento", "exempt"} & tokens
        or {"reduzida", "reduzido", "reduced"} & tokens
        or {"base", "basis"} & tokens
        or {"valor", "value", "amount"} & tokens
    )
    # ...but only when the rate reading rests on the abbreviation alone. A
    # header that states a full rate word keeps it even beside a slot word,
    # which is what leaves sap_terumo's "Tax rate (Tax/Base)" a rate.
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
    # SAP commonly abbreviates the taxable amount/base as ``Mont.`` or
    # ``Monta`` in a tax-family column (for example, PIS Mont. beside PIS
    # Taxa and Valor do PIS). Outside tax-family context, ``mont.`` retains
    # its ordinary amount/value meaning.
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


def semantic_header_conflict(left: object, right: object) -> str | None:
    """Describe an explicit tax-family or semantic-role conflict between headers.

    This is deliberately conservative: an absent or bogus header has no explicit
    signature and therefore is not rejected. Explicit conflicts remain viable for
    human review because distinctive row content can show that a header is wrong.
    """
    left_families = header_tax_families(left)
    right_families = header_tax_families(right)
    if left_families and right_families and left_families.isdisjoint(right_families):
        return (
            "tax-family conflict "
            f"({', '.join(sorted(left_families))} vs {', '.join(sorted(right_families))})"
        )

    left_qualifiers = set(normalize_header(left).split()) & {"st"}
    right_qualifiers = set(normalize_header(right).split()) & {"st"}
    if (
        left_families
        and right_families
        and not left_families.isdisjoint(right_families)
        and left_qualifiers != right_qualifiers
    ):
        return (
            "tax-qualifier conflict "
            f"({', '.join(sorted(left_qualifiers)) or 'regular'} vs "
            f"{', '.join(sorted(right_qualifiers)) or 'regular'})"
        )

    left_role = header_semantic_role(left)
    right_role = header_semantic_role(right)
    if left_role is not None and right_role is not None and left_role != right_role:
        return f"semantic-role conflict ({left_role} vs {right_role})"
    return None


def _header_text(value: object, index: int) -> str:
    if pd.isna(value) or not str(value).strip():
        return f"Unnamed column {index + 1}"
    return str(value).strip()


@dataclass(frozen=True)
class ColumnRef:
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
            raise ValueError(f"Spreadsheet has no rows: {path}")
        if header_row < 0 or header_row >= len(raw):
            raise ValueError(
                f"Header row {header_row + 1} is outside spreadsheet rows: {path}"
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
    """Return stable, representative values independent of spreadsheet row order."""
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

    # Frequent values represent the dominant distribution, while evenly spaced
    # values expose range and format differences. Both selections are stable.
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
    """Compare generic value shape without encoding any invoice-field mapping."""
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
    """Return normalized sample values unlikely to match by accident."""
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
    """Measure exact overlap of nontrivial samples, excluding zeros and short IDs."""
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
    """Return strong content evidence only for validated Brazilian identifiers."""
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
    """Return whether rows carry evidence stronger than a generic type match."""
    if (
        left["evidence_quality"] != "informative"
        or right["evidence_quality"] != "informative"
    ):
        return False
    if distinctive_sample_overlap_count(left, right) >= 2:
        return True
    return identifier_match_score(left, right) >= 0.90


def numeric_series(series: pd.Series) -> pd.Series:
    """Parse ordinary and Brazilian-formatted numbers without changing row order."""
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
    """Score the row-wise relationship value ~= base * rate.

    Both common rate representations are tested: percentage points (18) and a
    decimal fraction (0.18). The score is the fraction of usable rows within a
    2% relative or two-cent absolute tolerance.
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
    """Return a tax family and reusable qualifiers for calculation fields."""
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
    """Identify the conservative quantity * unit-price = line-value trio."""
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
    """Score quantity * unit price ~= line value with fiscal-report tolerance."""
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
        """Return a matrix with shape (model headers, client headers)."""


class LexicalHeaderScorer:
    """Offline scorer used for tests and local dry runs."""

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
            raise ValueError("OpenAI returned a zero-length embedding")
        similarities = (model_vectors / model_norms) @ (client_vectors / client_norms).T
        similarities = np.clip(similarities, 0.0, 1.0)

        # Reusable translated, abbreviated, or reordered terms can strengthen the
        # embedding score, but do not receive a fabricated probability of 1.0.
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
        """Resolve one low-confidence model column against client candidates."""


class OpenAIDataResolver:
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
            raise RuntimeError("OpenAI returned no parsed mapping decision")
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


@dataclass
class MappingResult:
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
    #: Coverage over distinct codes. catalog_coverage stays row-weighted so the
    #: reported column keeps its meaning; the gates use this one, because it is
    #: what catalog_confidence is now computed from.
    catalog_distinct_coverage: float | None = None
    catalog_confidence: float | None = None
    #: Set when a client-side arithmetic trio independently places this column
    #: here. Header-independent, so it is what tells a reviewer - and the eval
    #: harness - that this mapping would survive an unfamiliar ERP.
    arithmetic_proof: str | None = None
    llm_data_confidence: float | None = None
    llm_final_confidence: float | None = None


@dataclass(frozen=True)
class MapperConfig:
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
    """Evaluate reusable row relationships against already accepted anchors."""
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
    """Attach catalog evidence and reopen clear pass-one contradictions."""
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


#: Floor for catalog evidence from a nationally-unique code domain.
#:
#: Measured over 348 scored column-catalog pairs across the four sample
#: clients: for the specificity-1.0 catalogs (CFOP, NCM, IBGE municipality)
#: the six true code columns score 0.8824-1.0000 while all 1,301 other pairs
#: score 0.0000-0.0137 - a 64x empty gap. Any floor inside that gap separates
#: perfectly and survives leave-one-client-out; 0.90 does not, because it sits
#: at the top edge of the code cluster rather than in the gap, which is why a
#: textbook CFOP column with 15 of 17 official codes was rejected.
#:
#: The floor is deliberately NOT applied to lower-specificity catalogs. Those
#: are small closed domains - cst_ipi has 14 codes, cst_pis_cofins 33 - and
#: any column of small integers matches them: at 0.50 the same corpus admits
#: 123 pairs instead of 6, including 'Means of Transport' against cst_ipi and
#: 'Valor Frete' against cst_icms. For those catalogs there is no gap at all,
#: so the existing threshold stands and they remain LLM evidence rather than
#: accepting evidence.
DISTINCTIVE_CATALOG_FLOOR = 0.50

#: A nationally-unique domain still needs enough distinct codes to be evidence:
#: two matching values prove nothing however official the table.
DISTINCTIVE_CATALOG_MIN_CODES = 5


def catalog_evidence_is_distinctive(
    evidence: Mapping[str, object] | None,
    *,
    final_threshold: float,
) -> bool:
    """Whether catalog evidence is strong enough to decide a mapping alone."""
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


def _unique_distinctive_catalog_decision(
    *,
    candidates: Sequence[tuple[ColumnRef, pd.Series, float]],
    catalog_evidence: dict[int, list[dict[str, object]]],
    minimum_score: float = 0.90,
    minimum_lead: float = 0.10,
) -> DataMappingDecision | None:
    """Resolve only distinctive, high-coverage official-code domains locally."""
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
        reason=(
            f"Versioned official {evidence['catalog']} codes cover "
            f"{float(evidence.get('distinct_coverage', evidence['coverage'])):.1%} "
            f"of the {int(evidence['unique_formatted_count'])} distinct codes used "
            f"({float(evidence['coverage']):.1%} of rows) and uniquely "
            "identify this candidate among the available columns."
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
    """Reopen one clear tax-trio outlier when an informative alternative fits."""
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
            # Reopen the weakest of the three anchors. It will pass through the
            # row-data stage, where the contradiction prevents silent acceptance.
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
    """Prefer explicit semantic compatibility during header-only matching.

    Conflicting headers still enter the row-data pass, where distinctive content
    can demonstrate that a client header is bogus. They are not valid runner-ups
    for automatic header-only acceptance.
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
    """Union the strongest header, row-shape, relationship, and catalog candidates."""
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
    """Return selected score, strongest alternative, and the gap between them."""
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
    """Reject forced fallbacks while retaining plausible review candidates."""
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


#: A discovered trio must clear these to count as proof. The fit bar is high
#: because the identity is exact arithmetic, not a correlation, and the row bar
#: exists because a perfect fit over a handful of rows proves nothing - the
#: corpus contains 12-row degenerate fits that look identical to real ones.
TRIO_MIN_FIT = 0.97
TRIO_MIN_ROWS = 30


def _trio_proof_text(trio: dict[str, object] | None) -> str | None:
    """A reviewer-checkable sentence, or nothing.

    The identity corroborates a *scope*, not a column. Where a client carries
    two disjoint blocks for one tax - an apuracao block and an escrituracao
    block, say - both satisfy valor = base x aliquota, so the proof is true of
    either and discriminates between them not at all. Saying so is the whole
    point: an unqualified proof on the losing block reads as an endorsement,
    and that is how datasul m213 came to carry one.
    """
    if trio is None:
        return None
    text = (
        f"valor = base x aliquota holds for {trio['tax_scope']} on "
        f"{trio['rows_tested']} rows at fit {trio['fit_score']}"
    )
    alternatives = int(trio.get("alternatives", 1))
    if alternatives > 1:
        text += (
            f" - but this client has {alternatives} disjoint {trio['tax_scope']} "
            "blocks that each satisfy it, so this does not single out this column"
        )
    return text


def discover_client_tax_trios(
    client_table: SheetTable,
) -> dict[tuple[str, tuple[str, ...]], list[dict[str, object]]]:
    """Find base/rate/value trios that satisfy valor = base x aliquota.

    Runs over the client's own columns alone - no model, no prior acceptance,
    no vocabulary beyond the family and role words already needed elsewhere.
    That is the point: ``valor = base x aliquota`` is Brazilian tax law rather
    than a property of any one export, so a trio proven here is evidence that
    transfers to an ERP nobody has seen.

    The existing relationship evidence cannot do this. It asks
    _accepted_tax_anchors for two already-ACCEPTED roles in the same scope, so
    it can only confirm what header matching already settled; measured on the
    four sample clients it unlocks nothing that acceptance had not unlocked
    first.

    Returns EVERY qualifying trio per scope, best first, keyed exactly like
    tax_calculation_scope. Keeping only the argmax was wrong: a client may run
    two disjoint blocks for one tax - datasul carries both an apuracao block
    (Base PIS Ap x Aliq. PIS = Vlr PIS Ap, fit 0.9996 over 5,501 rows) and an
    escrituracao block (Base Pis x Aliq. PIS = Valor Pis, fit 0.9857 over 70)
    - and the larger one is not automatically the one the model's slots want.
    Each trio carries ``alternatives``: how many mutually disjoint blocks the
    scope holds, which is what tells a reader whether the proof discriminates.
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
        # Two trios that share a column are the same block described twice -
        # sap_terumo names every rate twice, as 'ICMS Taxa' and 'Tax rate
        # ICMS', and both close on the same base and value. Only blocks that
        # share nothing are rival readings of the model's slots.
        disjoint = _disjoint_trio_count(trios)
        for trio in trios:
            trio["alternatives"] = disjoint
        proven[scope] = trios
    return proven


def _disjoint_trio_count(trios: Sequence[dict[str, object]]) -> int:
    """How many mutually column-disjoint blocks these trios represent."""
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
    """Whether a proven trio independently places this client column here."""
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
    """Whether a client column has no populated cell at all.

    Deliberately narrower than ``evidence_quality == "all_zero"``. A column of
    zeros is ordinary fiscal data - an exempt operation has a zero base, and
    six of oracle's all-zero columns are client-authored ground truth - while a
    column with nothing in it cannot be any model column's counterpart. An
    accepted mapping onto one reaches the customer looking mapped and silently
    blank, which is the one failure that costs more than a missing column.
    """
    series = table.series(column)
    return int(series.notna().sum()) == 0 or not any(
        str(value).strip() for value in series.dropna()
    )


@dataclass
class UnmatchedColumn:
    """A client column whose data does not reach the base model.

    Unmatched means NOT CONSUMED BY AN ACCEPTED MAPPING, which is what decides
    whether the data actually lands: apply_invoice_mapping populates accepted
    and reviewer-confirmed rows only. Defining it as "never referenced by any
    row" instead would hide the majority of real misses - measured against the
    client answer keys, that definition surfaces 10 of 21 unconsumed truths,
    and four of the hidden ones are unreachable from a model-indexed view at
    all because no row ever offers them for their true concept.
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
    """Reasons a leftover column might matter, from universal evidence only.

    Every signal here is a property of Brazilian fiscal data rather than of any
    client's vocabulary: membership of an official code table, a check digit,
    participation in valor = base x aliquota. That is deliberate. A ranking
    built from the sample clients' header words would rank the next client's
    columns by how much they resemble these four, which is exactly backwards.

    Two vocabulary-based cuts were tested and rejected: requiring a fiscal
    token destroys datasul 'Cod. Produto' and oracle 'ORGANIZACAO', and a
    bookkeeping blocklist destroys datasul 'Vlr Contabil', which is a real
    answer. Nothing is filtered on words.
    """
    signals: list[str] = []
    details: list[str] = []

    slot = trio_slots.get(column.index)
    if slot is not None:
        signals.append("arithmetic")
        details.append(
            f"participates in a proven valor = base x aliquota trio as the {slot}"
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
                f"{float(evidence['distinct_coverage']):.1%} of its "
                f"{int(evidence['unique_formatted_count'])} distinct codes are valid "
                f"{evidence['catalog']}"
            )

    for kind in ("nfe_key", "cnpj", "cpf"):
        fraction = float(profile.get(f"valid_{kind}_fraction", 0.0) or 0.0)
        if fraction >= 0.80:
            signals.append(f"{kind}_checkdigit")
            details.append(f"{fraction:.1%} of values pass the {kind} check digit")
            break

    if profile.get("inferred_type") == "datetime":
        signals.append("date")
        details.append("holds dates")

    if not signals:
        quality = profile.get("evidence_quality")
        if quality == "empty":
            details.append("No values in this column.")
        elif quality == "all_zero":
            details.append("Every value in this column is zero.")
        else:
            details.append("Carries data, but no signal matched. Listed for completeness.")
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
    """Every client column whose data does not reach the base model.

    Nothing is omitted. A shortlist is marked, because 294 rows is not a report
    anybody reads, but the rest are still written with a sentence saying why
    they were set aside - two candidate cut rules were each measured to destroy
    a real answer, so demotion is as far as this goes.

    A long list here is NOT a defect signal. The base model is a deliberate
    superset and a client legitimately supplies only a minority of it, so most
    of a client's own columns having no model counterpart is the ordinary case.

    One caveat on reading the shortlist: --offline is a degenerate regime for
    it. With no resolver every unresolved column becomes a review rather than a
    no_match, so "was proposed" carries no information and almost everything is
    shortlisted - 28 of oracle's 30, against 15 of 30 on the same client with
    the resolver enabled. Judge a shortlist from a full run.
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
        # A proposal needs no signal: the mapper already produced evidence for
        # it, there are few of them, and gating them on the detectors was
        # measured to drop recall of known truths from 21/21 to 18/21.
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


def map_columns(
    model_table: SheetTable,
    client_table: SheetTable,
    header_scorer: HeaderScorer,
    data_resolver: DataResolver | None,
    config: MapperConfig = MapperConfig(),
    reference_catalogs: ReferenceCatalogs | None = None,
) -> list[MappingResult]:
    if not client_table.columns:
        raise ValueError("Client spreadsheet has no columns")

    similarities = header_scorer.scores(
        [column.name for column in model_table.columns],
        [column.name for column in client_table.columns],
    )
    expected_shape = (len(model_table.columns), len(client_table.columns))
    if similarities.shape != expected_shape:
        raise ValueError(f"Header scorer returned {similarities.shape}, expected {expected_shape}")

    # Discovered once, from the client's own columns. Independent of the model,
    # of acceptance order, and of any header vocabulary beyond family and role.
    client_tax_trios = discover_client_tax_trios(client_table)

    available = set(range(len(client_table.columns)))
    results: dict[int, MappingResult] = {}
    low_confidence_targets: list[int] = []
    catalog_analysis_cache: dict[tuple[str, int], dict[str, object]] = {}

    # Pass 1: walk model columns from left to right using names only.
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
                reason="No unused client columns remain.",
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
                reason="Accepted from header similarity with a clear lead over the runner-up.",
                arithmetic_proof=_trio_proof_text(
                trio_corroboration(client_tax_trios, model_column.name, client_column.index)
            ),
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
    # Pass 2: revisit only low-confidence model columns using names and row profiles.
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
                reason="No unused client columns remain.",
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
                reason="Header score or runner-up margin was insufficient; data comparison was disabled.",
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
        except Exception as exc:  # Continue the report even if one API decision fails.
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
                reason=f"Data comparison failed: {type(exc).__name__}: {exc}",
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
                        "reason": (
                            "A unique high-coverage fiscal relationship supports this column "
                            "over alternatives (for example, base times rate reproduces value)."
                        ),
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
                        "reason": (
                            "Distinctive non-zero sample values overlap exactly with this "
                            "candidate and not with the competing columns."
                        ),
                    }
                )

        # A fiscal equation can also be persuasive when more than one numeric
        # column happens to fit it. In that case, combine the relationship with
        # the header score and require a clear lead in the combined score. This
        # avoids discarding a strong base/rate/value mapping merely because a
        # broad total column coincidentally produces similar arithmetic.
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
                            "reason": (
                                "The header and a row-wise fiscal relationship jointly "
                                "support this column with a clear lead over alternatives."
                            ),
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
                    "reason": (
                        "The resolver found supporting evidence but did not commit to a column; "
                        "retain the strongest header candidate for review."
                    ),
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
                    "reason": (
                        "The strongest header candidate has weak row evidence; preserve the "
                        "name-based mapping for review instead of replacing it with a merely "
                        "similar value shape."
                    ),
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
        # The gates read distinct coverage - the statistic catalog_confidence
        # is computed from - while the reported catalog_coverage column keeps
        # its original row-weighted meaning so existing reports stay readable.
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
                reason=(
                    "No candidate had enough combined header and distinctive row evidence; "
                    "the client export may not contain this model field."
                ),
                llm_data_confidence=llm_data_confidence,
                llm_final_confidence=llm_final_confidence,
            )
            continue
        if header_conflict is not None:
            reason = (
                f"Explicit {header_conflict}; keep this proposal for review because the "
                "row content provides distinctive evidence that the client header may be bogus."
            )
        elif relationship_contradiction:
            reason = (
                "A high-coverage fiscal equation strongly contradicts this proposal; "
                "keep it for review instead of accepting it automatically."
            )
        elif final_confidence >= config.final_threshold and not accepted:
            reason = (
                "The header match is ambiguous and the row samples are empty or all-zero; "
                "keep this mapping for review."
            )
        accepted_but_for_vacuity = (
            not accepted
            and final_confidence >= config.final_threshold
            and (name_is_decisive or data_is_decisive)
            and header_conflict is None
            and not relationship_contradiction
        )
        if accepted_but_for_vacuity:
            # Say so plainly. Everything else about this proposal may look
            # strong, and a reviewer told only that the header score was
            # insufficient would go looking for the wrong problem.
            reason = (
                f"{chosen.display_name!r} has no populated rows, so it cannot be "
                "this column's source however well the headers match. "
                "Not accepted."
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
            arithmetic_proof=_trio_proof_text(
                trio_corroboration(client_tax_trios, model_column.name, chosen.index)
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
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_results(
    results: Sequence[MappingResult],
    output_path: str | Path,
    metadata: dict[str, object] | None = None,
    unmatched: Sequence[UnmatchedColumn] | None = None,
) -> None:
    output = Path(output_path)
    frame = pd.DataFrame([asdict(result) for result in results])
    for column in REVIEWER_COLUMNS:
        frame[column] = ""
    if output.suffix.lower() == ".csv":
        frame.to_csv(output, index=False)
    elif output.suffix.lower() == ".xlsx":
        with pd.ExcelWriter(output, engine="openpyxl") as writer:
            frame.to_excel(writer, sheet_name="Mappings", index=False)
            if metadata:
                pd.DataFrame(
                    [(key, value) for key, value in metadata.items()],
                    columns=["key", "value"],
                ).to_excel(writer, sheet_name="Run Metadata", index=False)
            # Appended LAST, and on purpose. apply_invoice_mapping reads sheet
            # index 0 with no name, so anything inserted ahead of "Mappings"
            # makes it fail with a misleading missing-columns error.
            #
            # Written on `is not None` rather than on truthiness: an empty list
            # states "every column of yours reached the model", which is a
            # different and stronger claim than an older report that has no
            # such sheet at all.
            if unmatched is not None:
                pd.DataFrame(
                    [asdict(row) for row in unmatched]
                    or [{field: "" for field in UnmatchedColumn.__annotations__}]
                ).to_excel(
                    writer, sheet_name="Unmatched Client Columns", index=False
                )
    else:
        raise ValueError("Output must end in .xlsx or .csv")


def _sheet_argument(value: str) -> str | int:
    return int(value) if value.isdigit() else value


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Map client spreadsheet columns to model.xlsx in two passes."
    )
    parser.add_argument("client", help="Client .xlsx file")
    parser.add_argument("--model", default="model.xlsx", help="Canonical model workbook")
    parser.add_argument("--output", help="Mapping report (.xlsx or .csv)")
    parser.add_argument("--model-sheet", default="0", help="Model sheet index or name")
    parser.add_argument("--client-sheet", default="0", help="Client sheet index or name")
    parser.add_argument(
        "--model-header-row",
        type=int,
        default=1,
        help="1-based header row in the model sheet",
    )
    parser.add_argument(
        "--client-header-row",
        type=int,
        default=1,
        help="1-based header row in the client sheet",
    )
    parser.add_argument("--name-threshold", type=float, default=0.90)
    parser.add_argument(
        "--name-margin",
        type=float,
        default=0.05,
        help="Minimum lead over the second-best header candidate for pass-one acceptance",
    )
    parser.add_argument("--final-threshold", type=float, default=0.90)
    parser.add_argument("--candidates", type=int, default=10)
    parser.add_argument("--sample-size", type=int, default=8)
    parser.add_argument(
        "--embedding-model",
        default=os.getenv("OPENAI_EMBEDDING_MODEL", DEFAULT_EMBEDDING_MODEL),
    )
    parser.add_argument("--llm-model", default=os.getenv("OPENAI_MODEL", DEFAULT_LLM_MODEL))
    parser.add_argument(
        "--reference-data",
        default=str(Path(__file__).with_name("reference_data")),
        help="Directory containing manifest.json and versioned fiscal-code catalogs",
    )
    parser.add_argument(
        "--no-reference-data",
        action="store_true",
        help="Disable official fiscal-code catalog evidence",
    )
    parser.add_argument(
        "--offline",
        action="store_true",
        help="Use lexical name matching and skip OpenAI data comparison.",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if not 0.0 <= args.name_threshold <= 1.0:
        raise SystemExit("--name-threshold must be between 0 and 1")
    if not 0.0 <= args.name_margin <= 1.0:
        raise SystemExit("--name-margin must be between 0 and 1")
    if not 0.0 <= args.final_threshold <= 1.0:
        raise SystemExit("--final-threshold must be between 0 and 1")
    if args.candidates < 1 or args.sample_size < 1:
        raise SystemExit("--candidates and --sample-size must be positive")
    if args.model_header_row < 1 or args.client_header_row < 1:
        raise SystemExit("--model-header-row and --client-header-row must be positive")

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
                f"Could not load reference catalogs from {args.reference_data}: {exc}. "
                "Run update_reference_data.py or use --no-reference-data."
            ) from exc

    if args.offline:
        header_scorer: HeaderScorer = LexicalHeaderScorer()
        data_resolver: DataResolver | None = None
    else:
        if not os.getenv("OPENAI_API_KEY"):
            raise SystemExit(
                "OPENAI_API_KEY is not set. Export a replacement key or use --offline."
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
    # The closest-model-column hint is computed lexically rather than reusing
    # the run's scorer: it is a reviewer convenience, and recomputing sentence
    # embeddings for a hint would spend money to no purpose.
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
    write_results(results, output_path, metadata=metadata, unmatched=unmatched)

    accepted = sum(result.status == "accepted" for result in results)
    review = sum(result.status == "review" for result in results)
    no_match = sum(result.status == "no_match" for result in results)
    print(f"Wrote {output_path}")
    print(f"accepted={accepted} review={review} no_match={no_match}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
