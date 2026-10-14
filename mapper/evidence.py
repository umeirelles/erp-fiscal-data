"""Etapa 3: evidências nos valores das colunas.

Recebe séries de valores e devolve perfis, frações de dígitos verificadores (CPF,
CNPJ, chave NF-e), provas aritméticas (valor = base x alíquota e quantidade x preço)
e a leitura das análises de catálogo usada pelas regras de decisão.
"""

from __future__ import annotations

import re
from typing import Mapping, Sequence

import numpy as np
import pandas as pd

from mapper import schema
from mapper.catalogs import best_catalog_evidence
from mapper.headers import (
    _TRIO_ROLES,
    _target_role,
    _target_tax_scope,
    header_semantic_role,
    tax_calculation_scope,
)
from mapper.spreadsheet import ColumnRef, SheetTable, normalize_header


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
OVERLAP_SCORE_BASE = 0.90  # score da sobreposição: min(0,99; 0,90 + 0,09 x sobreposição)
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
FULL_CONFIDENCE_ROWS = 20.0  # com menos linhas, o score cai por √(linhas / 20)
MIN_DISTINCT_VALUES = 3  # base ou valor com menos valores distintos: identidade degenerada...
DEGENERATE_DISCOUNT = 0.75  # ...e score descontado
CONTRADICTION_MIN_ROWS = 20  # contradição forte: >= 20 linhas, cobertura >= 0,80, aderência <= 0,20
CONTRADICTION_MIN_COVERAGE = 0.80
CONTRADICTION_MAX_FIT = 0.20
TRIO_MIN_FIT = 0.97  # aderência mínima à equação para registrar um trio
TRIO_MIN_ROWS = 30  # quantidade mínima de linhas para sustentar a relação

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

#: Coluna prova_aritmetica. ``tax_scope`` é a sigla do tributo em maiúsculas;
#: ``fit_score`` chega com vírgula decimal (``0,998``).
PROOF_TEXT = (
    "valor = base x alíquota se confirma para {tax_scope} em {rows_tested} linhas, "
    "com aderência {fit_score}"
)
#: Complemento da prova quando o cliente tem mais de um bloco do mesmo tributo.
PROOF_NOT_DECISIVE_SUFFIX = (
    " - mas este cliente tem {alternatives} blocos disjuntos de {tax_scope} que "
    "satisfazem a equação, portanto ela não distingue esta coluna"
)


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
        fit_score=schema.format_decimal_br(trio["fit_score"]),
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
# Catálogos oficiais de códigos
# ---------------------------------------------------------------------------


def _catalog_score(evidence: Sequence[dict[str, object]]) -> float:
    best = best_catalog_evidence(evidence)
    return float(best["score"]) if best is not None else 0.0


def _catalog_label(catalog: object) -> str:
    """Retorna o nome do catálogo usado nas mensagens, como ``CFOP`` para ``cfop``."""
    return schema.CATALOG_LABELS.get(str(catalog), str(catalog))


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
