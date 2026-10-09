"""Carrega os catálogos oficiais de códigos fiscais e pontua uma coluna contra eles.

Os catálogos (CFOP, NCM, municípios do IBGE, CST e cClassTrib do IBS/CBS, CST
do ICMS, do IPI e do PIS/COFINS, e CSOSN) ficam em ``reference_data/``, cada um
com o SHA-256 registrado no ``manifest.json``. Ids de catálogo, chaves do
manifesto e nomes de normalizador ficam em inglês: mudá-los mudaria o
manifesto e, com ele, o hash que cada relatório registra.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

import pandas as pd

#: Abaixo deste número de valores preenchidos a pontuação é atenuada (raiz quadrada).
_FULL_WEIGHT_SAMPLE = 5.0
#: Quantos valores fora do catálogo a análise mostra, e com quantos caracteres.
_MAX_INVALID_EXAMPLES = 3
_INVALID_EXAMPLE_LENGTH = 80


def _normalized_header(value: object) -> str:
    text = "" if value is None else str(value).strip()
    text = unicodedata.normalize("NFKD", text)
    text = text.encode("ascii", "ignore").decode("ascii").lower()
    return re.sub(r"[^a-z0-9]+", " ", text).strip()


def expected_catalogs(header: object) -> tuple[str, ...]:
    """Devolve os catálogos de códigos que um cabeçalho do modelo base pode representar.

    Recebe o cabeçalho do modelo, nunca o do cliente.
    """
    normalized = _normalized_header(header)
    tokens = set(normalized.split())
    if "cfop" in tokens:
        return ("cfop",)
    if "ncm" in tokens:
        return ("ncm",)
    if "municipio" in tokens or ("domicilio" in tokens and "fiscal" in tokens):
        return ("ibge_municipality",)

    is_classification = bool(
        {"class", "classif", "classificacao"} & tokens
        and {"tributaria", "tributario", "tax"} & tokens
    )
    is_tax_status = bool(
        "cst" in tokens
        or ("situacao" in tokens and {"tributaria", "tributario"} & tokens)
        or "taxsituation" in tokens
        # Não casa 'Tipo de Imposto …' do modelo (KNOWN_ISSUES 3.11).
        or "tipo imposto" in normalized
        or "codigo de sit tributaria" in normalized
    )
    has_ibs_cbs = bool({"ibs", "cbs", "ibd"} & tokens)
    if is_classification and has_ibs_cbs:
        return ("ibs_cbs_classification",)
    if is_tax_status and has_ibs_cbs:
        return ("ibs_cbs_cst",)
    if is_tax_status and "ipi" in tokens:
        return ("cst_ipi",)
    if is_tax_status and {"pis", "cofins"} & tokens:
        return ("cst_pis_cofins",)
    if is_tax_status and "icms" in tokens:
        return ("cst_icms", "cst_icms_taxation", "csosn")
    return ()


def normalize_code_value(
    value: object,
    *,
    width: int,
    normalizer: str,
) -> str | None:
    """Normaliza o código de uma célula para a largura do catálogo, sem perder os zeros à esquerda.

    Devolve None quando a célula está vazia ou não tem a forma de um código.
    """
    if value is None or pd.isna(value):
        return None
    digits = _code_digits(value, normalizer)
    if digits is None or len(digits) > width:
        return None
    return digits.zfill(width)


def _code_digits(value: object, normalizer: str) -> str | None:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        number = float(value)
        if not math.isfinite(number) or not number.is_integer():
            return None
        return str(int(number))
    text = str(value).strip()
    if not text:
        return None
    if re.fullmatch(r"[+-]?\d+\.0+", text):  # número gravado como texto: "5102.0"
        return str(int(float(text)))
    if normalizer == "cfop":
        # Aceita o sufixo que alguns ERPs põem no CFOP: "5102/AA".
        match = re.fullmatch(r"(\d{4})(?:\s*/\s*[A-Za-z0-9_-]+)?", text)
        return None if match is None else match.group(1)
    if normalizer == "punctuated_digits":
        # NCM com pontuação: "8516.79.10".
        return None if re.search(r"[^\d.\-/\s]", text) else re.sub(r"\D", "", text)
    return text if text.isdigit() else None


@dataclass(frozen=True)
class CatalogSpec:
    name: str
    file: str
    width: int
    normalizer: str
    specificity: float
    source_version: str
    source_url: str
    sha256: str


@dataclass(frozen=True)
class LoadedCatalog:
    spec: CatalogSpec
    codes: frozenset[str]


class ReferenceCatalogs:
    """Cópias imutáveis dos catálogos e a evidência conservadora que dão sobre uma coluna."""

    def __init__(self, directory: Path, catalogs: dict[str, LoadedCatalog]) -> None:
        self.directory = directory
        self.catalogs = catalogs

    @classmethod
    def from_directory(cls, directory: str | Path) -> "ReferenceCatalogs":
        """Carrega todos os catálogos do manifesto, conferindo o SHA-256 de cada CSV."""
        root = Path(directory)
        manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
        loaded = {
            name: _load_catalog(root, CatalogSpec(name=name, **raw_spec))
            for name, raw_spec in manifest["catalogs"].items()
        }
        return cls(root, loaded)

    def analyze(self, series: pd.Series, catalog_name: str) -> dict[str, object]:
        """Mede quanto os valores de uma coluna pertencem a um catálogo.

        A pontuação usa a cobertura sobre os códigos distintos, não sobre as
        linhas: quantas vezes o cliente repete um código não diz se a coluna é
        deste domínio, e dois códigos internos do ERP muito usados não devem
        esconder uma coluna que é claramente de CFOP. Uma coluna cujos códigos
        válidos são todos zero pontua 0.
        """
        catalog = self.catalogs[catalog_name]
        spec = catalog.spec
        nonempty = [value for value in series if _is_filled(value)]
        normalized = [
            normalize_code_value(value, width=spec.width, normalizer=spec.normalizer)
            for value in nonempty
        ]
        formatted = [code for code in normalized if code is not None]
        valid = [code for code in formatted if code in catalog.codes]
        unique_formatted = set(formatted)
        unique_valid = set(valid)

        distinct_coverage = _fraction(len(unique_valid), len(unique_formatted))
        all_zero_only = bool(unique_valid) and all(not code.strip("0") for code in unique_valid)
        sample_factor = min(1.0, math.sqrt(len(nonempty) / _FULL_WEIGHT_SAMPLE))
        score = 0.0 if all_zero_only else distinct_coverage * spec.specificity * sample_factor
        return {
            "catalog": catalog_name,
            "source_version": spec.source_version,
            "source_url": spec.source_url,
            "width": spec.width,
            "specificity": round(spec.specificity, 4),
            "nonempty_count": len(nonempty),
            "format_fraction": round(_fraction(len(formatted), len(nonempty)), 4),
            "valid_count": len(valid),
            "coverage": round(_fraction(len(valid), len(nonempty)), 4),
            "distinct_coverage": round(distinct_coverage, 4),
            "unique_valid_count": len(unique_valid),
            "unique_formatted_count": len(unique_formatted),
            "all_zero_only": all_zero_only,
            "score": round(score, 4),
            "invalid_examples": _invalid_examples(nonempty, normalized, catalog.codes),
        }


def _load_catalog(root: Path, spec: CatalogSpec) -> LoadedCatalog:
    path = root / spec.file
    if hashlib.sha256(path.read_bytes()).hexdigest() != spec.sha256:
        raise ValueError(
            "O checksum do catálogo de referência não confere com o "
            f"manifest.json: {path}"
        )
    frame = pd.read_csv(path, dtype=str, keep_default_na=False)
    if "code" not in frame.columns:
        raise ValueError(f"O catálogo de referência não tem a coluna 'code': {path}")
    codes = frozenset(code.strip() for code in frame["code"] if code.strip())
    return LoadedCatalog(spec=spec, codes=codes)


def _is_filled(value: object) -> bool:
    return value is not None and not pd.isna(value) and bool(str(value).strip())


def _fraction(part: int, whole: int) -> float:
    return part / whole if whole else 0.0


def _invalid_examples(
    values: list[object], codes: list[str | None], catalog_codes: frozenset[str]
) -> list[str]:
    """Os primeiros valores fora do catálogo, como o cliente os escreveu (cortados)."""
    examples: list[str] = []
    for value, code in zip(values, codes, strict=True):
        if code is None or code not in catalog_codes:
            text = str(value).strip()
            if text not in examples:
                examples.append(text[:_INVALID_EXAMPLE_LENGTH])
            if len(examples) == _MAX_INVALID_EXAMPLES:
                break
    return examples


def best_catalog_evidence(
    evidence: Sequence[dict[str, object]],
) -> dict[str, object] | None:
    """A análise mais forte: maior pontuação, depois cobertura, depois mais códigos válidos."""
    if not evidence:
        return None
    return max(
        evidence,
        key=lambda item: (
            float(item["score"]),
            float(item["coverage"]),
            int(item["valid_count"]),
        ),
    )
