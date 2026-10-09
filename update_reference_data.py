"""Baixa cópias atualizadas das tabelas oficiais de códigos fiscais para reference_data/.

Precisa de acesso à internet e regrava os CSV e o manifest.json do diretório
de saída.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Sequence
from urllib.request import Request, urlopen

import pandas as pd

from cli_ptbr import PortugueseArgumentParser


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
    """Os dígitos do código completados com zeros até ``width``; vazio se não couber."""
    if pd.isna(value):
        return ""
    if isinstance(value, (int, float)) and float(value).is_integer():
        digits = str(int(value))
    else:
        digits = re.sub(r"\D", "", str(value))
    return digits.zfill(width) if digits and len(digits) <= width else ""


def iso_date(value: object) -> str:
    """A data em AAAA-MM-DD; vazio para célula vazia, inválida ou 9999-12-31 (sem fim)."""
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
    """Baixa as tabelas oficiais e as converte; devolve os quadros e os metadados do manifesto."""
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
    """Descarta os códigos que não têm a largura cheia e as repetições."""
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
    # O nome da aba muda a cada publicação (KNOWN_ISSUES 3.14).
    raw = _read_sheet(content, "CST 2026-06-01 Pub")
    frame = pd.DataFrame(
        {
            "code": _codes(raw["CST-IBS/CBS"], 3),
            "description": raw["Descrição CST-IBS/CBS"].fillna(""),
            "valid_from": "2026-01-01",
            "valid_to": "",
        }
    )
    # Sem o filtro de largura das outras tabelas (KNOWN_ISSUES 3.14).
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
    """As entradas do manifesto das tabelas baixadas; só a versão do NCM vem do download."""
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
            # Muitos códigos válidos começam com zeros (000001), então uma
            # coluna de situação com inteiros pode coincidir com eles depois
            # que o Excel corta os zeros: evidência para a IA, nunca decisão
            # só pelo catálogo.
            "specificity": 0.85,
            "source_version": "IT 2025.002 v1.60; published 2026-06-23",
            "source_url": IBS_CBS_URL,
        },
    }


def static_frames() -> tuple[dict[str, pd.DataFrame], dict[str, dict[str, object]]]:
    """As tabelas de CST e CSOSN, escritas aqui a partir dos guias da EFD."""
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
    parser = PortugueseArgumentParser(description=__doc__)
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
    args = build_parser().parse_args(argv)
    write_snapshot(Path(args.output))
    return 0


if __name__ == "__main__":
    sys.exit(main())
