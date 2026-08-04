"""Download authoritative fiscal-code snapshots into reference_data/."""

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


def download(url: str, *, nfe: bool = False) -> bytes:
    headers = {"User-Agent": "cbs-base-fiscal reference updater/1.0"}
    if nfe:
        headers["Cookie"] = "AspxAutoDetectCookieSupport=1"
    request = Request(url, headers=headers)
    with urlopen(request, timeout=90) as response:
        content = response.read()
    if not content:
        raise RuntimeError(f"Official source returned an empty response: {url}")
    return content


def clean_code(value: object, width: int) -> str:
    if pd.isna(value):
        return ""
    if isinstance(value, (int, float)) and float(value).is_integer():
        digits = str(int(value))
    else:
        digits = re.sub(r"\D", "", str(value))
    return digits.zfill(width) if digits and len(digits) <= width else ""


def iso_date(value: object) -> str:
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
    cfop_bytes = download(CFOP_URL, nfe=True)
    ibs_bytes = download(IBS_CBS_URL, nfe=True)
    municipality_bytes = download(MUNICIPALITY_URL, nfe=True)
    ncm_bytes = download(NCM_URL)

    cfop_raw = pd.read_excel(io.BytesIO(cfop_bytes), sheet_name="CFOP", dtype=object)
    cfop = pd.DataFrame(
        {
            "code": cfop_raw["CFOP"].map(lambda value: clean_code(value, 4)),
            "description": "",
            "valid_from": cfop_raw["Início de vigência"].map(iso_date),
            "valid_to": cfop_raw["Fim de vigência"].map(iso_date),
        }
    )
    cfop = cfop[cfop["code"].str.len().eq(4)].drop_duplicates("code")

    municipality_raw = pd.read_excel(
        io.BytesIO(municipality_bytes), sheet_name="Mun", dtype=object
    )
    municipality = pd.DataFrame(
        {
            "code": municipality_raw["cMun"].map(
                lambda value: clean_code(value, 7)
            ),
            "description": municipality_raw["Nome_Município"].fillna(""),
            "valid_from": "",
            "valid_to": "",
        }
    )
    municipality = municipality[
        municipality["code"].str.len().eq(7)
    ].drop_duplicates("code")

    ibs_cst_raw = pd.read_excel(
        io.BytesIO(ibs_bytes), sheet_name="CST 2026-06-01 Pub", dtype=object
    )
    ibs_cst = pd.DataFrame(
        {
            "code": ibs_cst_raw["CST-IBS/CBS"].map(
                lambda value: clean_code(value, 3)
            ),
            "description": ibs_cst_raw["Descrição CST-IBS/CBS"].fillna(""),
            "valid_from": "2026-01-01",
            "valid_to": "",
        }
    ).drop_duplicates("code")

    cclass_raw = pd.read_excel(
        io.BytesIO(ibs_bytes), sheet_name="cClass 2026-06-01 Pub", dtype=object
    )
    cclass = pd.DataFrame(
        {
            "code": cclass_raw["cClassTrib"].map(
                lambda value: clean_code(value, 6)
            ),
            "description": cclass_raw["Nome cClassTrib"].fillna(""),
            "valid_from": cclass_raw["dIniVig"].map(iso_date),
            "valid_to": cclass_raw["dFimVig"].map(iso_date),
        }
    )
    cclass = cclass[cclass["code"].str.len().eq(6)].drop_duplicates("code")

    ncm_payload = json.loads(ncm_bytes.decode("utf-8"))
    ncm_rows = []
    for item in ncm_payload["Nomenclaturas"]:
        code = re.sub(r"\D", "", item["Codigo"])
        if len(code) != 8:
            continue
        ncm_rows.append(
            {
                "code": code,
                "description": item.get("Descricao", ""),
                "valid_from": iso_date(item.get("Data_Inicio")),
                "valid_to": iso_date(item.get("Data_Fim")),
            }
        )
    ncm = pd.DataFrame(ncm_rows).drop_duplicates("code")

    frames = {
        "cfop": cfop,
        "ncm": ncm,
        "ibge_municipality": municipality,
        "ibs_cbs_cst": ibs_cst,
        "ibs_cbs_classification": cclass,
    }
    metadata = {
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
            "source_version": ncm_payload["Data_Ultima_Atualizacao_NCM"],
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
            # Many valid codes begin with zeros (for example 000001), so an
            # ordinary integer status column can collide after Excel removes
            # leading zeros. Keep this as strong LLM evidence, not a catalog-only
            # automatic decision.
            "specificity": 0.85,
            "source_version": "IT 2025.002 v1.60; published 2026-06-23",
            "source_url": IBS_CBS_URL,
        },
    }
    return frames, metadata


def static_frames() -> tuple[dict[str, pd.DataFrame], dict[str, dict[str, object]]]:
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
    metadata = {
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
    return frames, metadata


def write_snapshot(output: Path) -> None:
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
        print(f"{name}: {len(frame)} codes")

    manifest = {
        "schema_version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "catalogs": manifest_catalogs,
    }
    (output / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"Wrote {output / 'manifest.json'}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", default="reference_data")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    write_snapshot(Path(args.output))
    return 0


if __name__ == "__main__":
    sys.exit(main())
