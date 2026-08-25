"""Load and evaluate versioned Brazilian fiscal-code reference catalogs."""

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


def _normalized_header(value: object) -> str:
    text = "" if value is None else str(value).strip()
    text = unicodedata.normalize("NFKD", text)
    text = text.encode("ascii", "ignore").decode("ascii").lower()
    return re.sub(r"[^a-z0-9]+", " ", text).strip()


def expected_catalogs(header: object) -> tuple[str, ...]:
    """Return official code domains that a canonical header could represent."""
    normalized = _normalized_header(header)
    tokens = set(normalized.split())
    if "cfop" in tokens:
        return ("cfop",)
    if "ncm" in tokens:
        return ("ncm",)
    if "municipio" in tokens or (
        "domicilio" in tokens and "fiscal" in tokens
    ):
        return ("ibge_municipality",)

    is_classification = bool(
        {"class", "classif", "classificacao"} & tokens
        and {"tributaria", "tributario", "tax"} & tokens
    )
    is_tax_status = bool(
        "cst" in tokens
        or ("situacao" in tokens and {"tributaria", "tributario"} & tokens)
        or "taxsituation" in tokens
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
    """Normalize spreadsheet codes without losing leading-zero semantics."""
    if value is None or pd.isna(value):
        return None
    numeric_cell = isinstance(value, (int, float)) and not isinstance(value, bool)
    if numeric_cell:
        number = float(value)
        if not math.isfinite(number) or not number.is_integer():
            return None
        digits = str(int(number))
    else:
        text = str(value).strip()
        if not text:
            return None
        if re.fullmatch(r"[+-]?\d+\.0+", text):
            digits = str(int(float(text)))
        elif normalizer == "cfop":
            match = re.fullmatch(r"(\d{4})(?:\s*/\s*[A-Za-z0-9_-]+)?", text)
            if match is None:
                return None
            digits = match.group(1)
        elif normalizer == "punctuated_digits":
            if re.search(r"[^\d.\-/\s]", text):
                return None
            digits = re.sub(r"\D", "", text)
        elif text.isdigit():
            digits = text
        else:
            return None

    if len(digits) > width:
        return None
    return digits.zfill(width)


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
    """Immutable catalog snapshots plus conservative column-level evidence."""

    def __init__(self, directory: Path, catalogs: dict[str, LoadedCatalog]) -> None:
        self.directory = directory
        self.catalogs = catalogs

    @classmethod
    def from_directory(cls, directory: str | Path) -> "ReferenceCatalogs":
        root = Path(directory)
        manifest_path = root / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        loaded: dict[str, LoadedCatalog] = {}
        for name, raw_spec in manifest["catalogs"].items():
            spec = CatalogSpec(name=name, **raw_spec)
            catalog_path = root / spec.file
            actual_hash = hashlib.sha256(catalog_path.read_bytes()).hexdigest()
            if actual_hash != spec.sha256:
                raise ValueError(
                    f"Reference catalog checksum mismatch for {catalog_path}"
                )
            frame = pd.read_csv(catalog_path, dtype=str, keep_default_na=False)
            if "code" not in frame.columns:
                raise ValueError(f"Reference catalog lacks a code column: {catalog_path}")
            codes = frozenset(code.strip() for code in frame["code"] if code.strip())
            loaded[name] = LoadedCatalog(spec=spec, codes=codes)
        return cls(root, loaded)

    def analyze(self, series: pd.Series, catalog_name: str) -> dict[str, object]:
        catalog = self.catalogs[catalog_name]
        nonempty = [
            value
            for value in series
            if value is not None and not pd.isna(value) and str(value).strip()
        ]
        normalized = [
            normalize_code_value(
                value,
                width=catalog.spec.width,
                normalizer=catalog.spec.normalizer,
            )
            for value in nonempty
        ]
        formatted = [code for code in normalized if code is not None]
        valid = [code for code in formatted if code in catalog.codes]
        nonempty_count = len(nonempty)
        format_fraction = len(formatted) / nonempty_count if nonempty_count else 0.0
        coverage = len(valid) / nonempty_count if nonempty_count else 0.0
        unique_formatted = {code for code in formatted if code is not None}
        unique_valid = set(valid)
        # Coverage weighted by how often each code happens to be used answers
        # "what share of rows did we recognise", which is not the question.
        # A CFOP column is a CFOP column even when two frequent ERP-generic
        # codes are absent from the official table: oracle's CFO uses 17
        # distinct codes, 15 of them official, but the two unofficial ones
        # cover a third of its rows, so row coverage reads 0.667 while the
        # column is unmistakably CFOP. Coverage over DISTINCT codes reads
        # 0.882 and does not care how often a code repeats.
        distinct_coverage = (
            len(unique_valid) / len(unique_formatted) if unique_formatted else 0.0
        )
        all_zero_only = bool(unique_valid) and all(
            not code.strip("0") for code in unique_valid
        )
        sample_factor = min(1.0, math.sqrt(nonempty_count / 5.0))
        # Scored on distinct codes rather than rows: how often a client
        # happens to repeat one code says nothing about whether the column
        # belongs to this code domain.
        score = distinct_coverage * catalog.spec.specificity * sample_factor
        if all_zero_only:
            score = 0.0
        invalid_examples: list[str] = []
        for value, code in zip(nonempty, normalized, strict=True):
            if code is None or code not in catalog.codes:
                text = str(value).strip()
                if text not in invalid_examples:
                    invalid_examples.append(text[:80])
                if len(invalid_examples) == 3:
                    break
        return {
            "catalog": catalog_name,
            "source_version": catalog.spec.source_version,
            "source_url": catalog.spec.source_url,
            "width": catalog.spec.width,
            "specificity": round(catalog.spec.specificity, 4),
            "nonempty_count": nonempty_count,
            "format_fraction": round(format_fraction, 4),
            "valid_count": len(valid),
            "coverage": round(coverage, 4),
            "distinct_coverage": round(distinct_coverage, 4),
            "unique_valid_count": len(unique_valid),
            "unique_formatted_count": len(unique_formatted),
            "all_zero_only": all_zero_only,
            "score": round(score, 4),
            "invalid_examples": invalid_examples,
        }

    def analyze_for_header(
        self, header: object, series: pd.Series
    ) -> list[dict[str, object]]:
        return [
            self.analyze(series, name)
            for name in expected_catalogs(header)
            if name in self.catalogs
        ]


def best_catalog_evidence(
    evidence: Sequence[dict[str, object]],
) -> dict[str, object] | None:
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
