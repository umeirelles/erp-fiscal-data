"""Tabelas fiscais oficiais: CFOP, NCM, municípios do IBGE, CST, CSOSN e cClassTrib.

Carrega os CSVs de ``reference_data/`` conferindo o SHA-256 registrado no manifesto e
compara os códigos de uma coluna com cada tabela. ``main`` é o comando
``update-catalogs``: baixa as tabelas das fontes oficiais (usa a rede) e regrava os CSVs.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import io
import json
import math
from pathlib import Path
import re
from typing import Sequence
from urllib.request import Request, urlopen

import pandas as pd

from mapper.cli import PortugueseArgumentParser
from mapper.schema import target_field
from mapper.spreadsheet import normalize_header

# IDs, chaves do manifesto e nomes dos normalizadores ficam em inglês: fazem parte
# do formato versionado dos catálogos.

#: Descrição exibida por ``run.py update-catalogs --help``.
UPDATE_DESCRIPTION = (
    "Atualiza os catálogos fiscais de ``reference_data/`` a partir das fontes oficiais.\n\n"
    "O download requer acesso à internet. A gravação substitui os CSVs e o\n"
    "``manifest.json`` no diretório de saída.\n"
)


# ---------------------------------------------------------------------------
# Escolha dos catálogos, normalização dos códigos e análise de uma coluna
# ---------------------------------------------------------------------------

#: Abaixo deste número de valores preenchidos o score é atenuado (raiz quadrada).
_FULL_WEIGHT_SAMPLE = 5.0
#: Limites de quantidade e tamanho dos exemplos de códigos fora do catálogo.
_MAX_INVALID_EXAMPLES = 3
_INVALID_EXAMPLE_LENGTH = 80


def expected_catalogs(header: object) -> tuple[str, ...]:
    """Identifica quais catálogos se aplicam ao cabeçalho do modelo base.

    O argumento deve ser o cabeçalho do modelo, não o da exportação do cliente.
    """
    definition = target_field(header)
    if definition is not None:
        return definition.catalogs
    normalized = normalize_header(header)
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
        # Os campos controlados 'Tipo de Imposto …' são resolvidos pelo registro acima.
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
    """Ajusta o código ao tamanho esperado pelo catálogo, completando zeros à esquerda.

    Retorna ``None`` para células vazias ou valores que não podem ser
    interpretados como um código válido para esse formato.
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
    """Mantém cópias imutáveis dos catálogos e avalia as colunas com critérios conservadores."""

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
        """Calcula a cobertura e o score de uma coluna em relação ao catálogo.

        O score usa os códigos distintos para que a repetição de poucos valores
        não domine o resultado. Assim, códigos internos do ERP muito frequentes
        não anulam o apoio dos demais códigos oficiais. Se os únicos códigos
        válidos forem zeros, o score será 0.
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
    """Retorna exemplos de valores fora do catálogo, preservando o texto original até o limite de tamanho."""
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
    """Seleciona a análise por score, cobertura e quantidade de códigos válidos, nessa ordem."""
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


# ---------------------------------------------------------------------------
# Comando update-catalogs (python run.py update-catalogs)
#
#
# Baixa as tabelas das fontes oficiais (requer internet) e regrava os CSVs e
# o manifest.json no diretório de saída.
# ---------------------------------------------------------------------------

NFE_BASE = "https://www.nfe.fazenda.gov.br/portal/exibirArquivo.aspx?conteudo="
NCM_URL = (
    "https://portalunico.siscomex.gov.br/classif/api/publico/"
    "nomenclatura/download/json"
)
CFOP_URL = f"{NFE_BASE}UGAhInpwpPw%3D"
IBS_CBS_URL = f"{NFE_BASE}D5b4Ov84WDg%3D"
MUNICIPALITY_URL = f"{NFE_BASE}eGl9ISPltUE%3D"
EFD_ICMS_IPI_SOURCE = (
    "https://www.gov.br/sped/pt-br/assuntos/escrituracoes-digitais/efd-icms-ipi/"
    "manuais-e-documentos-tecnicos/guia-pratico-da-efd-icms-ipi-3-2.2"
)
EFD_CONTRIBUTIONS_SOURCE = "https://sped.rfb.gov.br/item/show/268"

_USER_AGENT = "cbs-base-fiscal reference updater/1.0"
_TIMEOUT_SECONDS = 90


def download(url: str, *, nfe: bool = False) -> bytes:
    headers = {"User-Agent": _USER_AGENT}
    if nfe:
        headers["Cookie"] = "AspxAutoDetectCookieSupport=1"
    request = Request(url, headers=headers)
    with urlopen(request, timeout=_TIMEOUT_SECONDS) as response:
        content = response.read()
    if not content:
        raise RuntimeError(f"A fonte oficial retornou uma resposta vazia: {url}")
    return content


def clean_code(value: object, width: int) -> str:
    """Completa o código com zeros até ``width``; retorna vazio se exceder o tamanho."""
    if pd.isna(value):
        return ""
    if isinstance(value, (int, float)) and float(value).is_integer():
        digits = str(int(value))
    else:
        digits = re.sub(r"\D", "", str(value))
    return digits.zfill(width) if digits and len(digits) <= width else ""


def iso_date(value: object) -> str:
    """Converte a data para YYYY-MM-DD.

    Retorna vazio para células ausentes, datas inválidas e ``9999-12-31``,
    que representa ausência de data final.
    """
    if pd.isna(value) or str(value).strip() in {"", "9999-12-31"}:
        return ""
    parsed = pd.to_datetime(value, errors="coerce")
    return "" if pd.isna(parsed) else parsed.date().isoformat()


def standard_frame(
    codes: Sequence[str],
    descriptions: Sequence[str] | None = None,
    *,
    valid_from: str = "",
    valid_to: str = "",
) -> pd.DataFrame:
    descriptions = descriptions or [""] * len(codes)
    return pd.DataFrame(
        {
            "code": list(codes),
            "description": list(descriptions),
            "valid_from": [valid_from] * len(codes),
            "valid_to": [valid_to] * len(codes),
        }
    )


def official_frames() -> tuple[dict[str, pd.DataFrame], dict[str, dict[str, object]]]:
    """Baixa as tabelas oficiais e retorna os DataFrames convertidos e os metadados do manifesto."""
    cfop_bytes = download(CFOP_URL, nfe=True)
    ibs_bytes = download(IBS_CBS_URL, nfe=True)
    municipality_bytes = download(MUNICIPALITY_URL, nfe=True)
    ncm_bytes = download(NCM_URL)

    cfop = _cfop_frame(cfop_bytes)
    municipality = _municipality_frame(municipality_bytes)
    ibs_cst = _ibs_cbs_cst_frame(ibs_bytes)
    cclass = _ibs_cbs_classification_frame(ibs_bytes)
    ncm_payload = json.loads(ncm_bytes.decode("utf-8"))
    ncm = _ncm_frame(ncm_payload)

    frames = {
        "cfop": cfop,
        "ncm": ncm,
        "ibge_municipality": municipality,
        "ibs_cbs_cst": ibs_cst,
        "ibs_cbs_classification": cclass,
    }
    return frames, _official_metadata(ncm_version=ncm_payload["Data_Ultima_Atualizacao_NCM"])


def _read_sheet(content: bytes, sheet: str) -> pd.DataFrame:
    return pd.read_excel(io.BytesIO(content), sheet_name=sheet, dtype=object)


def _codes(column: pd.Series, width: int) -> pd.Series:
    return column.map(lambda value: clean_code(value, width))


def _full_width_unique(frame: pd.DataFrame, width: int) -> pd.DataFrame:
    """Remove códigos com tamanho diferente do esperado e elimina duplicatas."""
    return frame[frame["code"].str.len().eq(width)].drop_duplicates("code")


def _cfop_frame(content: bytes) -> pd.DataFrame:
    raw = _read_sheet(content, "CFOP")
    frame = pd.DataFrame(
        {
            "code": _codes(raw["CFOP"], 4),
            "description": "",
            "valid_from": raw["Início de vigência"].map(iso_date),
            "valid_to": raw["Fim de vigência"].map(iso_date),
        }
    )
    return _full_width_unique(frame, 4)


def _municipality_frame(content: bytes) -> pd.DataFrame:
    raw = _read_sheet(content, "Mun")
    frame = pd.DataFrame(
        {
            "code": _codes(raw["cMun"], 7),
            "description": raw["Nome_Município"].fillna(""),
            "valid_from": "",
            "valid_to": "",
        }
    )
    return _full_width_unique(frame, 7)


def _ibs_cbs_cst_frame(content: bytes) -> pd.DataFrame:
    # Confira o nome da aba ao atualizar a publicação.
    raw = _read_sheet(content, "CST 2026-06-01 Pub")
    frame = pd.DataFrame(
        {
            "code": _codes(raw["CST-IBS/CBS"], 3),
            "description": raw["Descrição CST-IBS/CBS"].fillna(""),
            "valid_from": "2026-01-01",
            "valid_to": "",
        }
    )
    # Esta extração ainda não aplica o filtro de largura das outras tabelas.
    return frame.drop_duplicates("code")


def _ibs_cbs_classification_frame(content: bytes) -> pd.DataFrame:
    raw = _read_sheet(content, "cClass 2026-06-01 Pub")
    frame = pd.DataFrame(
        {
            "code": _codes(raw["cClassTrib"], 6),
            "description": raw["Nome cClassTrib"].fillna(""),
            "valid_from": raw["dIniVig"].map(iso_date),
            "valid_to": raw["dFimVig"].map(iso_date),
        }
    )
    return _full_width_unique(frame, 6)


def _ncm_frame(payload: dict) -> pd.DataFrame:
    rows = []
    for item in payload["Nomenclaturas"]:
        code = re.sub(r"\D", "", item["Codigo"])
        if len(code) != 8:  # capítulos, posições e subposições ficam de fora
            continue
        rows.append(
            {
                "code": code,
                "description": item.get("Descricao", ""),
                "valid_from": iso_date(item.get("Data_Inicio")),
                "valid_to": iso_date(item.get("Data_Fim")),
            }
        )
    return pd.DataFrame(rows).drop_duplicates("code")


def _official_metadata(*, ncm_version: str) -> dict[str, dict[str, object]]:
    """Monta os metadados das tabelas baixadas; apenas a versão do NCM é obtida no download."""
    return {
        "cfop": {
            "width": 4,
            "normalizer": "cfop",
            "specificity": 1.0,
            "source_version": "IT 2023.002 v1.00; effective 2023-04-24",
            "source_url": CFOP_URL,
        },
        "ncm": {
            "width": 8,
            "normalizer": "punctuated_digits",
            "specificity": 1.0,
            "source_version": ncm_version,
            "source_url": NCM_URL,
        },
        "ibge_municipality": {
            "width": 7,
            "normalizer": "digits",
            "specificity": 1.0,
            "source_version": "NF-e municipality table published 2025-02-18",
            "source_url": MUNICIPALITY_URL,
        },
        "ibs_cbs_cst": {
            "width": 3,
            "normalizer": "digits",
            "specificity": 0.75,
            "source_version": "IT 2025.002 v1.60; published 2026-06-23",
            "source_url": IBS_CBS_URL,
        },
        "ibs_cbs_classification": {
            "width": 6,
            "normalizer": "digits",
            # Códigos como 000001 podem coincidir com inteiros comuns quando
            # o Excel remove os zeros. Por isso, o catálogo apoia a análise
            # da IA, mas não basta para decidir o mapeamento.
            "specificity": 0.85,
            "source_version": "IT 2025.002 v1.60; published 2026-06-23",
            "source_url": IBS_CBS_URL,
        },
    }


def static_frames() -> tuple[dict[str, pd.DataFrame], dict[str, dict[str, object]]]:
    """Monta as tabelas de CST e CSOSN mantidas no código com base nos guias da EFD."""
    # CST do ICMS = origem (0 a 8) + tratamento tributário.
    icms_treatments = {
        "00": "Tributada integralmente",
        "10": "Tributada com cobrança do ICMS por substituição tributária",
        "20": "Com redução de base de cálculo",
        "30": "Isenta ou não tributada com cobrança do ICMS por substituição tributária",
        "40": "Isenta",
        "41": "Não tributada",
        "50": "Suspensão",
        "51": "Diferimento",
        "60": "ICMS cobrado anteriormente por substituição tributária",
        "70": "Com redução de base e cobrança do ICMS por substituição tributária",
        "90": "Outras",
    }
    cst_icms_codes = [
        f"{origin}{treatment}"
        for origin in range(9)
        for treatment in icms_treatments
    ]
    cst_icms_descriptions = [
        f"Origem {code[0]}; {icms_treatments[code[1:]]}" for code in cst_icms_codes
    ]
    ipi_codes = ["00", "01", "02", "03", "04", "05", "49", "50", "51", "52", "53", "54", "55", "99"]
    pis_cofins_codes = (
        [f"{value:02d}" for value in range(1, 10)]
        + ["49"]
        + [f"{value:02d}" for value in range(50, 57)]
        + [f"{value:02d}" for value in range(60, 68)]
        + [f"{value:02d}" for value in range(70, 76)]
        + ["98", "99"]
    )
    csosn_codes = ["101", "102", "103", "201", "202", "203", "300", "400", "500", "900"]
    frames = {
        "cst_icms": standard_frame(cst_icms_codes, cst_icms_descriptions),
        "cst_icms_taxation": standard_frame(
            list(icms_treatments), list(icms_treatments.values())
        ),
        "csosn": standard_frame(csosn_codes),
        "cst_ipi": standard_frame(ipi_codes),
        "cst_pis_cofins": standard_frame(pis_cofins_codes),
    }
    return frames, _static_metadata()


def _static_metadata() -> dict[str, dict[str, object]]:
    return {
        "cst_icms": {
            "width": 3,
            "normalizer": "digits",
            "specificity": 0.75,
            "source_version": "EFD ICMS/IPI Guia Prático 3.2.2, table 4.3.1",
            "source_url": EFD_ICMS_IPI_SOURCE,
        },
        "cst_icms_taxation": {
            "width": 2,
            "normalizer": "digits",
            "specificity": 0.50,
            "source_version": "EFD ICMS/IPI Guia Prático 3.2.2, table 4.3.1",
            "source_url": EFD_ICMS_IPI_SOURCE,
        },
        "csosn": {
            "width": 3,
            "normalizer": "digits",
            "specificity": 0.75,
            "source_version": "EFD ICMS/IPI Guia Prático 3.2.2, table 4.3.1",
            "source_url": EFD_ICMS_IPI_SOURCE,
        },
        "cst_ipi": {
            "width": 2,
            "normalizer": "digits",
            "specificity": 0.55,
            "source_version": "EFD ICMS/IPI Guia Prático 3.2.2, table 4.3.2",
            "source_url": EFD_ICMS_IPI_SOURCE,
        },
        "cst_pis_cofins": {
            "width": 2,
            "normalizer": "digits",
            "specificity": 0.55,
            "source_version": "EFD-Contribuições CST tables II and III",
            "source_url": EFD_CONTRIBUTIONS_SOURCE,
        },
    }


def write_snapshot(output: Path) -> None:
    """Grava um CSV por catálogo, ordenado pelo código, e o manifest.json com os SHA-256."""
    output.mkdir(parents=True, exist_ok=True)
    official, official_metadata = official_frames()
    static, static_metadata = static_frames()
    frames = {**official, **static}
    metadata = {**official_metadata, **static_metadata}
    manifest_catalogs: dict[str, dict[str, object]] = {}
    for name in sorted(frames):
        filename = f"{name}.csv"
        path = output / filename
        frame = frames[name].copy()
        frame = frame[["code", "description", "valid_from", "valid_to"]]
        frame = frame.sort_values("code").reset_index(drop=True)
        frame.to_csv(path, index=False, lineterminator="\n")
        manifest_catalogs[name] = {
            "file": filename,
            **metadata[name],
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        }
        print(f"{name}: {len(frame)} códigos")

    manifest = {
        "schema_version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "catalogs": manifest_catalogs,
    }
    (output / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"Arquivo gravado: {output / 'manifest.json'}")


def build_parser() -> argparse.ArgumentParser:
    """Configura os argumentos e a ajuda de ``run.py update-catalogs``."""
    parser = PortugueseArgumentParser(prog="run.py update-catalogs", description=UPDATE_DESCRIPTION)
    parser.add_argument(
        "--output",
        default="reference_data",
        metavar="DIRETORIO",
        help=(
            "diretório de saída dos CSV e do manifest.json "
            "(padrão: reference_data, relativo ao diretório atual)"
        ),
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Baixa as tabelas oficiais e regrava os CSVs e o manifesto; usa a rede."""
    args = build_parser().parse_args(argv)
    write_snapshot(Path(args.output))
    return 0
