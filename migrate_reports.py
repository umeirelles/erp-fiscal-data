"""Migra um relatório de mapeamento do esquema legado em inglês para o esquema em português.

Uso::

    python migrate_reports.py RELATORIO EXPORTACAO [--model data/model.xlsx]
        [--reference-data reference_data] [--dry-run]

Os relatórios gravados antes da tradução (esquema legado, versão 2) têm abas,
cabeçalhos, chaves de metadados, valores enumerados e frases do mapeador em
inglês. Esta ferramenta os reescreve no esquema que o mapeador grava hoje
(versão 3), **sem reexecutar nada**: nenhuma decisão, pontuação, ordem ou
valor muda. O que muda:

* nomes das abas, cabeçalhos e chaves da aba de metadados;
* valores enumerados (situação, método, veto, catálogo, parecer do revisor,
  disposição, prioridade, sinais), etapas de ``supported_by`` e
  ``decided_by`` (inclusive ``unrecorded`` e ``(agrees)``, que só existem no
  legado) e o sufixo de ocorrência dos nomes de exibição;
* as frases escritas pelo mapeador (justificativa, prova aritmética, detalhe
  dos sinais, motivo de retenção e divergência da IA), traduzidas por
  ``legacy_reports`` para exatamente o texto que o mapeador atual gravaria;
* ``versao_esquema_mapeamento`` passa de 2 para 3 e entra a chave
  ``migrado_da_versao_esquema`` com a versão de origem. ``gerado_em_utc`` não
  muda.

O que passa intacto: a prosa escrita pela IA (o prompt continua em inglês),
as observações do revisor, ``model_column``, os valores de amostra do cliente
e todo número (os inteiros digitados em ``correct_client_column`` continuam
inteiros). O relatório é editado célula a célula com o openpyxl, então a
ordem das colunas, os tipos e a formatação do revisor ficam como estão, e
nenhuma aba ou coluna é acrescentada (sap_procosa mantém o layout antigo).

Antes de traduzir, quando os hashes gravados nos metadados conferem com a
exportação, o modelo e o manifesto de referência atuais, a ferramenta
regenera as partes derivadas (provas pelos trios, abas de colunas sem uso e
fila de revisão pelas funções ``summarize_*``) e as compara com o arquivado.
É uma prova cruzada: se algo diverge, ela avisa e traduz só pelos modelos de
frase.

A gravação é segura: o resultado vai para um arquivo temporário, passa pela
verificação embutida (quadros internos iguais nas colunas que não são texto,
frases iguais à tradução das originais, observações idênticas, contagem por
situação igual, pareceres linha a linha) e só então substitui o original.
Num relatório que já está em português, a ferramenta avisa e sai sem gravar.
"""

from __future__ import annotations

import copy
import errno
import math
import os
import shutil
import sys
import tempfile
from collections import Counter
from dataclasses import MISSING, dataclass, field, fields, replace
from pathlib import Path
from typing import Callable, Sequence

import openpyxl
import pandas as pd

import invoice_mapper as im
import legacy_reports
import report_schema as rs
from cli_ptbr import PortugueseArgumentParser
from reference_catalog import ReferenceCatalogs

#: Abas com tabela (cabeçalho na linha 1), na ordem em que aparecem.
TABLES: tuple[str, ...] = ("mappings", "unmatched", "queue")

#: Diretório padrão dos catálogos de referência, como no invoice_mapper.
DEFAULT_REFERENCE_DATA = str(Path(__file__).with_name("reference_data"))

ALREADY_PORTUGUESE = "{report} já está no esquema em português; nada foi alterado."

#: Métodos cujas linhas nunca recebiam prova aritmética no código legado
#: (2ae4b6d): a revisão só por nome do modo offline e a falha da comparação de
#: dados. A regeneração das provas não as considera.
_METHODS_WITHOUT_PROOF = frozenset({"name_only", "data_error"})

#: Quantas divergências citar numa mensagem.
_SHOWN = 5


class MigrationError(ValueError):
    """A verificação da migração falhou; o relatório original não foi alterado."""


# ---------------------------------------------------------------------------
# Resultado
# ---------------------------------------------------------------------------


@dataclass
class RegenerationCheck:
    """Resultado da prova cruzada por regeneração (antes de traduzir)."""

    ran: bool
    skipped_reason: str = ""
    #: Rótulo -> quantidade de linhas comparadas.
    compared: dict[str, int] = field(default_factory=dict)
    mismatches: list[str] = field(default_factory=list)


@dataclass
class MigrationSummary:
    """O que a migração fez com um relatório."""

    report: Path
    already_portuguese: bool = False
    sheets_renamed: int = 0
    headers_renamed: int = 0
    #: Valores enumerados, etapas, sinais, nomes de exibição e metadados.
    values_translated: int = 0
    #: Frases do mapeador reescritas em português.
    sentences_translated: int = 0
    #: Campo -> frases que passaram intactas (prosa da IA, na justificativa).
    sentences_kept: Counter = field(default_factory=Counter)
    #: Frases intactas que não deveriam existir: fora de uma linha da IA.
    unexpected_english: list[str] = field(default_factory=list)
    regeneration: RegenerationCheck | None = None
    status_counts: dict[str, int] = field(default_factory=dict)
    verdicts: int = 0
    notes: int = 0
    written: bool = False


# ---------------------------------------------------------------------------
# Regra de cada célula
# ---------------------------------------------------------------------------


def migrate_value(table: str, field_name: str, value: object) -> object:
    """Valor de uma célula legada -> o valor no esquema em português.

    ``field_name`` é o nome interno, igual ao cabeçalho legado. Frases do
    mapeador passam por ``legacy_reports``; enums, etapas, sinais e nomes de
    exibição, por ``report_schema.external_value``. A observação do revisor e
    todo campo sem tratamento (números, ``model_column``, amostras) voltam
    intactos.
    """
    if field_name == "reviewer_note":
        return value
    if field_name in legacy_reports.TEXT_FIELDS.get(table, frozenset()):
        return legacy_reports.translate_cell(table, field_name, value)
    return rs.external_value(table, field_name, value)


def _missing(value: object) -> bool:
    return value is None or (isinstance(value, float) and math.isnan(value))


def _same_cell(left: object, right: object) -> bool:
    """Igualdade estrita de célula: mesmo tipo e mesmo valor (vazio = vazio)."""
    if _missing(left) or _missing(right):
        return _missing(left) and _missing(right)
    return type(left) is type(right) and left == right


# ---------------------------------------------------------------------------
# Leitura
# ---------------------------------------------------------------------------


def _legacy_sheet_kinds(workbook) -> dict[str, str]:
    """Tipo de aba -> nome da aba no relatório legado.

    A tabela de mapeamentos é sempre a primeira aba, como o ``apply`` a lê.
    Levanta ``ValueError`` para esquema misto (uma aba com o nome em
    português) e se a aba ``Mappings`` não for a primeira.
    """
    names = workbook.sheetnames
    kinds: dict[str, str] = {}
    for kind in ("mappings", "run_metadata", "unmatched", "queue"):
        name = rs.find_sheet(names, kind, rs.LEGACY_EN)
        if name is not None:
            kinds[kind] = name
    first = names[0]
    if kinds.get("mappings", first) != first:
        raise ValueError(
            f"A aba {kinds['mappings']!r} precisa ser a primeira do relatório, como o "
            "apply_invoice_mapping a lê."
        )
    kinds["mappings"] = first
    return kinds


def _read_report(path: Path, schema: str) -> tuple[dict[str, pd.DataFrame], dict[str, str] | None]:
    """Quadros internos das tabelas (pelo ``report_schema``) e os metadados, se houver."""
    with pd.ExcelFile(path) as book:
        names = book.sheet_names
        frames: dict[str, pd.DataFrame] = {}
        for table in TABLES:
            name = names[0] if table == "mappings" else rs.find_sheet(names, table, schema)
            if name is None:
                continue
            frame, found = rs.to_internal_frame(pd.read_excel(book, sheet_name=name), table)
            if found != schema:
                raise ValueError(
                    f"Esquema misto: a aba {name!r} não está no mesmo esquema da aba de "
                    "mapeamentos."
                )
            frames[table] = frame
        metadata_name = rs.find_sheet(names, "run_metadata", schema)
        metadata = (
            None
            if metadata_name is None
            else rs.internal_metadata(pd.read_excel(book, sheet_name=metadata_name), schema)
        )
    return frames, metadata


# ---------------------------------------------------------------------------
# Prova cruzada por regeneração (design C.2)
# ---------------------------------------------------------------------------


def _plain(value: object) -> object:
    """Valor lido pelo pandas -> forma comparável.

    Vazio vira ``""`` e número do numpy vira número do Python.
    """
    if _missing(value):
        return ""
    if hasattr(value, "item") and not isinstance(value, str):
        return value.item()
    return value


def _same_regenerated(archived: object, regenerated: object) -> bool:
    archived, regenerated = _plain(archived), _plain(regenerated)
    numbers = (int, float)
    if (
        isinstance(archived, numbers)
        and isinstance(regenerated, numbers)
        and not isinstance(archived, bool)
        and not isinstance(regenerated, bool)
    ):
        return math.isclose(archived, regenerated, rel_tol=0.0, abs_tol=1e-9)
    return str(archived) == str(regenerated)


def _rebuild_results(frame: pd.DataFrame) -> list[im.MappingResult]:
    """Linhas da aba de mapeamentos (quadro interno) -> ``MappingResult``.

    Os campos ausentes no layout do relatório ficam com o padrão da classe.
    Só alimenta a regeneração; nada aqui é gravado.
    """
    required = [
        item.name
        for item in fields(im.MappingResult)
        if item.default is MISSING and item.default_factory is MISSING
    ]
    absent = [name for name in required if name not in frame.columns]
    if absent:
        raise ValueError(f"A aba de mapeamentos não tem as colunas {absent}.")
    results = []
    for record in frame.to_dict("records"):
        values: dict[str, object] = {}
        for item in fields(im.MappingResult):
            if item.name not in record:
                continue
            value = _plain(record[item.name])
            values[item.name] = None if value == "" else value
        values["model_column_number"] = int(values["model_column_number"])
        if values["client_column_number"] is not None:
            values["client_column_number"] = int(values["client_column_number"])
        for name in ("method", "status", "reason", "supported_by", "decided_by", "vetoed_by"):
            if name in values and values[name] is None:
                values[name] = ""
        results.append(im.MappingResult(**values))
    return results


def _compare_rows(
    label: str,
    archived: pd.DataFrame,
    regenerated: Sequence[object],
    table: str,
    check: RegenerationCheck,
) -> None:
    """Compara, linha a linha, uma aba arquivada com as linhas regeneradas.

    Nas frases, a regenerada (já em português) precisa ser a tradução da
    arquivada; nos demais campos, o valor interno precisa ser igual.
    """
    check.compared[label] = len(archived)
    if len(archived) != len(regenerated):
        check.mismatches.append(
            f"{label}: {len(archived)} linhas arquivadas, {len(regenerated)} regeneradas"
        )
        return
    for position, (record, row) in enumerate(zip(archived.to_dict("records"), regenerated)):
        for name, value in vars(row).items():
            if name not in record:
                continue
            expected = record[name]
            if name in legacy_reports.TEXT_FIELDS[table]:
                expected = legacy_reports.translate_cell(table, name, expected)
            if not _same_regenerated(expected, value):
                check.mismatches.append(
                    f"{label}, linha {position + 2}, {name}: arquivado {_plain(expected)!r}, "
                    f"regenerado {_plain(value)!r}"
                )


def cross_check_by_regeneration(
    frames: dict[str, pd.DataFrame],
    metadata: dict[str, str] | None,
    client_path: Path,
    model_path: Path,
    reference_data: Path,
) -> RegenerationCheck:
    """Regenera as partes derivadas a partir das entradas e compara com o arquivado.

    Só roda quando há o que comparar (prova aritmética ou as abas derivadas) e
    quando os hashes dos metadados conferem com os arquivos atuais. Não grava
    nada e não decide nada: a tradução é sempre pelos modelos de frase; isto
    só confirma, por um caminho independente, que o português resultante é o
    que o mapeador atual gravaria.
    """
    mappings = frames["mappings"]
    has_proofs = "arithmetic_proof" in mappings.columns
    if not has_proofs and "unmatched" not in frames and "queue" not in frames:
        return RegenerationCheck(
            ran=False,
            skipped_reason=(
                "o relatório não tem prova aritmética nem as abas derivadas; não há o que "
                "regenerar"
            ),
        )
    if metadata is None:
        return RegenerationCheck(
            ran=False, skipped_reason="o relatório não tem a aba de metadados com os hashes"
        )
    manifest_path = reference_data / "manifest.json"
    recorded_manifest = metadata.get("reference_manifest_sha256", "")
    expected_hashes = [
        ("sha256_cliente", metadata.get("client_sha256", ""), client_path),
        ("sha256_modelo", metadata.get("model_sha256", ""), model_path),
    ]
    if recorded_manifest != "disabled":
        expected_hashes.append(
            ("sha256_manifesto_referencia", recorded_manifest, manifest_path)
        )
    for label, recorded, path in expected_hashes:
        if not path.exists():
            return RegenerationCheck(ran=False, skipped_reason=f"{path} não existe")
        if recorded != im.file_sha256(path):
            return RegenerationCheck(
                ran=False,
                skipped_reason=f"{label} do relatório não confere com {path}",
            )

    try:
        return _regenerate(
            frames, metadata, client_path, model_path, reference_data,
            catalogs_disabled=recorded_manifest == "disabled",
        )
    except (OSError, ValueError, KeyError) as exc:
        # A conferência é opcional: se ela própria falha, a migração segue só
        # pelos modelos de frase, com o motivo à vista.
        return RegenerationCheck(ran=False, skipped_reason=f"a regeneração falhou ({exc})")


def _regenerate(
    frames: dict[str, pd.DataFrame],
    metadata: dict[str, str],
    client_path: Path,
    model_path: Path,
    reference_data: Path,
    *,
    catalogs_disabled: bool,
) -> RegenerationCheck:
    """Recalcula provas, colunas sem uso e fila de revisão e compara com o arquivado."""
    mappings = frames["mappings"]
    has_proofs = "arithmetic_proof" in mappings.columns
    client = im.SheetTable.from_excel(
        client_path,
        im._sheet_argument(metadata.get("client_sheet") or "0"),
        int(float(metadata.get("client_header_row") or 1)) - 1,
    )
    model = im.SheetTable.from_excel(
        model_path,
        im._sheet_argument(metadata.get("model_sheet") or "0"),
        int(float(metadata.get("model_header_row") or 1)) - 1,
    )
    catalogs = None if catalogs_disabled else ReferenceCatalogs.from_directory(reference_data)
    sample_size = int(float(metadata.get("sample_size") or 8))
    final_threshold = float(metadata.get("final_threshold") or 0.9)

    check = RegenerationCheck(ran=True)
    trios = im.discover_client_tax_trios(client)
    results = _rebuild_results(mappings)
    archived_proofs = mappings["arithmetic_proof"].tolist() if has_proofs else []
    for position, result in enumerate(results):
        proof = None
        if result.client_column_number is not None and result.method not in _METHODS_WITHOUT_PROOF:
            index = result.model_column_number - 1
            if (
                not 0 <= index < len(model.columns)
                or model.columns[index].name != result.model_column
            ):
                check.mismatches.append(
                    f"Mapeamentos, linha {position + 2}: a coluna do modelo "
                    f"{result.model_column!r} não está na posição {result.model_column_number} "
                    "do modelo atual"
                )
                continue
            proof = im._trio_proof_text(
                im.trio_corroboration(
                    trios, model.columns[index].name, result.client_column_number - 1
                )
            )
        results[position] = replace(result, arithmetic_proof=proof if has_proofs else None)
        if has_proofs:
            expected = legacy_reports.translate_cell(
                "mappings", "arithmetic_proof", archived_proofs[position]
            )
            if not _same_regenerated(expected, proof):
                check.mismatches.append(
                    f"Mapeamentos, linha {position + 2}, arithmetic_proof: arquivado "
                    f"{_plain(expected)!r}, regenerado {_plain(proof)!r}"
                )
    if has_proofs:
        check.compared["provas aritméticas"] = len(results)

    if "unmatched" in frames:
        unmatched = im.summarize_unmatched_columns(
            model_table=model,
            client_table=client,
            results=dict(enumerate(results)),
            similarities=im.LexicalHeaderScorer().scores(
                [column.name for column in model.columns],
                [column.name for column in client.columns],
            ),
            reference_catalogs=catalogs,
            client_tax_trios=trios,
            sample_size=sample_size,
        )
        _compare_rows(
            rs.sheet_name("unmatched"), frames["unmatched"], unmatched, "unmatched", check
        )
    if "queue" in frames:
        queue = im.summarize_adjudication_queue(
            client_table=client,
            results=results,
            final_threshold=final_threshold,
            sample_size=sample_size,
        )
        _compare_rows(rs.sheet_name("queue"), frames["queue"], queue, "queue", check)
    return check


# ---------------------------------------------------------------------------
# Tradução no lugar (openpyxl)
# ---------------------------------------------------------------------------


def _header_fields(worksheet) -> dict[int, str]:
    """Coluna (1, 2 ...) -> nome legado do cabeçalho, para as células de texto da linha 1."""
    return {
        cell.column: cell.value
        for cell in worksheet[1]
        if isinstance(cell.value, str) and cell.value
    }


def _set_value(cell, value: object) -> None:
    """Grava o valor mantendo o tipo de texto (um texto com ``=`` não vira fórmula)."""
    was_text = cell.data_type == "s"
    cell.value = value
    if was_text and isinstance(value, str) and cell.data_type == "f":
        cell.data_type = "s"


def _translate_table(worksheet, table: str, summary: MigrationSummary) -> None:
    columns = _header_fields(worksheet)
    names = rs.COLUMNS[table]
    for column, name in columns.items():
        translated = names.get(name)
        if translated is not None and translated != name:
            worksheet.cell(1, column).value = translated
            summary.headers_renamed += 1
    text_fields = legacy_reports.TEXT_FIELDS[table]
    llm_column = next(
        (column for column, name in columns.items() if name == "llm_final_confidence"), None
    )
    for row in worksheet.iter_rows(min_row=2):
        for cell in row:
            name = columns.get(cell.column)
            if name is None:
                continue
            value = cell.value
            new = migrate_value(table, name, value)
            if name in text_fields and isinstance(value, str) and value:
                if new == value:
                    summary.sentences_kept[name] += 1
                    llm_written = (
                        table == "mappings"
                        and name == "reason"
                        and llm_column is not None
                        and not _missing(worksheet.cell(cell.row, llm_column).value)
                    )
                    if not llm_written:
                        summary.unexpected_english.append(
                            f"{worksheet.title}!{cell.coordinate} ({name}): {value[:80]!r}"
                        )
                    continue
            if _same_cell(new, value):
                continue
            _set_value(cell, new)
            if name in text_fields:
                summary.sentences_translated += 1
            else:
                summary.values_translated += 1


def _translate_metadata(worksheet, summary: MigrationSummary) -> None:
    """Aba de metadados: cabeçalho, chaves, sentinelas e a versão do esquema.

    ``versao_esquema_mapeamento`` passa a 3 e uma linha nova no fim registra
    ``migrado_da_versao_esquema`` com o valor de origem, com o mesmo tipo.
    """
    header = [cell.value for cell in worksheet[1]]
    if rs.detect_schema(header, "run_metadata") != rs.LEGACY_EN:
        raise ValueError(
            f"Esquema misto: a aba {worksheet.title!r} tem os cabeçalhos do esquema em "
            "português, mas o relatório está no esquema legado em inglês."
        )
    key_column = header.index("key") + 1
    if "value" not in header:
        raise ValueError(f"A aba {worksheet.title!r} precisa ter as colunas 'key' e 'value'.")
    value_column = header.index("value") + 1
    for cell in worksheet[1]:
        if not isinstance(cell.value, str):
            continue
        translated = rs.COLUMNS["run_metadata"].get(cell.value)
        if translated is not None:
            cell.value = translated
            summary.headers_renamed += 1
    previous_version: object = None
    has_version = False
    already_marked = False
    last_row = worksheet.max_row
    for row in range(2, last_row + 1):
        key_cell = worksheet.cell(row, key_column)
        value_cell = worksheet.cell(row, value_column)
        key = key_cell.value.strip() if isinstance(key_cell.value, str) else key_cell.value
        if not isinstance(key, str) or not key:
            continue
        if key == "mapping_schema_version":
            previous_version, has_version = value_cell.value, True
            value_cell.value = rs.SCHEMA_VERSION_PT
            summary.values_translated += 1
        elif key in rs.METADATA_SENTINEL_KEYS and isinstance(value_cell.value, str):
            sentinel = rs.METADATA_SENTINELS.get(value_cell.value, value_cell.value)
            if sentinel != value_cell.value:
                value_cell.value = sentinel
                summary.values_translated += 1
        already_marked = already_marked or key == "migrated_from_schema_version"
        translated = rs.METADATA_KEYS.get(key)
        if translated is not None and translated != key_cell.value:
            key_cell.value = translated
            summary.values_translated += 1
    if has_version and not already_marked:
        worksheet.cell(last_row + 1, key_column).value = rs.METADATA_KEYS[
            "migrated_from_schema_version"
        ]
        worksheet.cell(last_row + 1, value_column).value = previous_version


def _translate_workbook(workbook, kinds: dict[str, str], summary: MigrationSummary) -> None:
    for table in TABLES:
        if table in kinds:
            _translate_table(workbook[kinds[table]], table, summary)
    if "run_metadata" in kinds:
        _translate_metadata(workbook[kinds["run_metadata"]], summary)
    for kind, name in kinds.items():
        if rs.find_sheet([name], kind, rs.LEGACY_EN) == name:
            workbook[name].title = rs.sheet_name(kind)
            summary.sheets_renamed += 1


# ---------------------------------------------------------------------------
# Verificação embutida (design C.4)
# ---------------------------------------------------------------------------


def _expected_title(kinds: dict[str, str], title: str) -> str:
    for kind, name in kinds.items():
        if name == title and rs.find_sheet([name], kind, rs.LEGACY_EN) == name:
            return rs.sheet_name(kind)
    return title


def _expected_metadata_cells(sheet) -> dict[str, object]:
    """Coordenada -> valor esperado das células da aba de metadados legada, depois da migração.

    Cabeçalho ``chave``/``valor``, chaves em português, versão 3 e sentinelas
    traduzidos; os demais valores, idênticos. A linha acrescentada no fim é
    conferida à parte.
    """
    header = [cell.value for cell in sheet[1]]
    expected: dict[str, object] = {}
    for cell in sheet[1]:
        if isinstance(cell.value, str) and cell.value in rs.COLUMNS["run_metadata"]:
            expected[cell.coordinate] = rs.COLUMNS["run_metadata"][cell.value]
    if "key" not in header or "value" not in header:
        return expected
    key_column, value_column = header.index("key") + 1, header.index("value") + 1
    for row in range(2, sheet.max_row + 1):
        key_cell = sheet.cell(row, key_column)
        value_cell = sheet.cell(row, value_column)
        key = key_cell.value.strip() if isinstance(key_cell.value, str) else key_cell.value
        if not isinstance(key, str) or not key:
            continue
        if key in rs.METADATA_KEYS:
            expected[key_cell.coordinate] = rs.METADATA_KEYS[key]
        if key == "mapping_schema_version":
            expected[value_cell.coordinate] = rs.SCHEMA_VERSION_PT
        elif key in rs.METADATA_SENTINEL_KEYS and isinstance(value_cell.value, str):
            expected[value_cell.coordinate] = rs.METADATA_SENTINELS.get(
                value_cell.value, value_cell.value
            )
    return expected


def _cell_format(cell) -> tuple[object, ...]:
    """Formatação de uma célula, comparável entre duas pastas de trabalho.

    O openpyxl devolve fonte, preenchimento etc. como ``StyleProxy``, que não
    se compara com outro proxy; ``copy`` devolve o objeto de estilo em si.
    """
    return (cell.number_format,) + tuple(
        copy.copy(getattr(cell, name))
        for name in ("font", "fill", "alignment", "border", "protection")
    )


def _check_cells(original, migrated, kinds: dict[str, str], problems: list[str]) -> None:
    """Toda célula do migrado é exatamente a regra aplicada à original, inclusive o tipo.

    Também confere a ordem e os nomes das abas, as dimensões e a formatação
    de cada célula, para provar que nada além do previsto mudou.
    """
    expected_titles = [_expected_title(kinds, title) for title in original.sheetnames]
    if migrated.sheetnames != expected_titles:
        problems.append(f"abas {migrated.sheetnames}, esperadas {expected_titles}")
        return
    table_of = {name: kind for kind, name in kinds.items()}
    for old, new in zip(original.worksheets, migrated.worksheets):
        kind = table_of.get(old.title)
        extra_rows = 1 if kind == "run_metadata" else 0
        if new.max_column != old.max_column or new.max_row not in {
            old.max_row,
            old.max_row + extra_rows,
        }:
            problems.append(
                f"aba {new.title!r}: dimensões {new.max_row}x{new.max_column}, "
                f"original {old.max_row}x{old.max_column}"
            )
            continue
        fields_by_column = _header_fields(old) if kind in TABLES else {}
        metadata_expected = _expected_metadata_cells(old) if kind == "run_metadata" else {}
        mismatched = 0
        for row in old.iter_rows():
            for old_cell in row:
                new_cell = new.cell(old_cell.row, old_cell.column)
                value = old_cell.value
                if kind in TABLES:
                    name = fields_by_column.get(old_cell.column)
                    if old_cell.row == 1:
                        expected = (
                            rs.COLUMNS[kind].get(value, value) if isinstance(value, str) else value
                        )
                    elif name is not None:
                        expected = migrate_value(kind, name, value)
                    else:
                        expected = value
                elif kind == "run_metadata":
                    expected = metadata_expected.get(old_cell.coordinate, value)
                else:
                    expected = value
                same_format = _cell_format(new_cell) == _cell_format(old_cell)
                if not _same_cell(expected, new_cell.value) or not same_format:
                    mismatched += 1
                    if mismatched <= _SHOWN:
                        problems.append(
                            f"{new.title}!{new_cell.coordinate}: {new_cell.value!r}, "
                            f"esperado {expected!r}"
                            + ("" if same_format else " (formatação diferente)")
                        )
        if kind == "run_metadata" and new.max_row == old.max_row + 1:
            appended = [
                new.cell(new.max_row, column).value for column in range(1, new.max_column + 1)
            ]
            if rs.METADATA_KEYS["migrated_from_schema_version"] not in appended:
                problems.append(f"aba {new.title!r}: linha acrescentada inesperada {appended}")
        if new.freeze_panes != old.freeze_panes or new.auto_filter.ref != old.auto_filter.ref:
            problems.append(f"aba {new.title!r}: painéis congelados ou filtro diferentes")
        for letter, dimension in old.column_dimensions.items():
            other = new.column_dimensions[letter]
            if (other.width, other.hidden) != (dimension.width, dimension.hidden):
                problems.append(f"aba {new.title!r}: largura ou ocultação da coluna {letter}")


def _check_frames(
    original: dict[str, pd.DataFrame], migrated: dict[str, pd.DataFrame], problems: list[str]
) -> None:
    """Quadros internos iguais fora das frases; cada frase migrada é a tradução da original."""
    if set(original) != set(migrated):
        problems.append(f"tabelas {sorted(migrated)}, originais {sorted(original)}")
        return
    for table, old in original.items():
        new = migrated[table]
        title = rs.sheet_name(table)
        if list(new.columns) != list(old.columns) or len(new) != len(old):
            problems.append(
                f"{title}: colunas ou linhas diferentes ({list(new.columns)} x {list(old.columns)})"
            )
            continue
        for name in old.columns:
            if old[name].dtype != new[name].dtype:
                problems.append(
                    f"{title}.{name}: tipo {new[name].dtype}, original {old[name].dtype}"
                )
                continue
            is_text = name in legacy_reports.TEXT_FIELDS.get(table, frozenset())
            mismatched = 0
            for position, (before, after) in enumerate(zip(old[name].tolist(), new[name].tolist())):
                expected = legacy_reports.translate_cell(table, name, before) if is_text else before
                if not _same_cell(expected, after):
                    mismatched += 1
                    if mismatched <= _SHOWN:
                        problems.append(
                            f"{title}, linha {position + 2}, {name}: {after!r}, "
                            f"esperado {expected!r}"
                        )
                again = legacy_reports.translate_cell(table, name, after) if is_text else after
                if not _same_cell(again, after):
                    problems.append(
                        f"{title}, linha {position + 2}, {name}: tradução não idempotente"
                    )


def _check_metadata(
    original: dict[str, str] | None, migrated: dict[str, str] | None, problems: list[str]
) -> None:
    if original is None or migrated is None:
        if original is not migrated:
            problems.append("a aba de metadados apareceu ou sumiu")
        return
    expected = dict(original)
    if "mapping_schema_version" in original:
        expected["mapping_schema_version"] = str(rs.SCHEMA_VERSION_PT)
        expected.setdefault("migrated_from_schema_version", original["mapping_schema_version"])
    if list(migrated.items()) != list(expected.items()):
        differences = {
            key: (expected.get(key), migrated.get(key))
            for key in set(expected) | set(migrated)
            if expected.get(key) != migrated.get(key)
        }
        problems.append(f"metadados diferentes do esperado: {differences or 'ordem das chaves'}")


def _reviewer_cells(worksheet, schema: str) -> list[tuple[object, object, object]]:
    """(parecer, coluna correta, observação) cru de cada linha da aba de mapeamentos."""
    header = [cell.value for cell in worksheet[1]]
    positions = []
    for name in im.REVIEWER_COLUMNS:
        label = rs.header_label("mappings", name, schema)
        positions.append(header.index(label) if label in header else None)
    rows = []
    for row in worksheet.iter_rows(min_row=2, values_only=True):
        rows.append(tuple(None if position is None else row[position] for position in positions))
    return rows


def _check_reviewer(original, migrated, problems: list[str], summary: MigrationSummary) -> None:
    """Pareceres linha a linha: parecer equivalente, coluna correta e observação idênticas."""
    before = _reviewer_cells(original.worksheets[0], rs.LEGACY_EN)
    after = _reviewer_cells(migrated.worksheets[0], rs.PT_BR)
    if len(before) != len(after):
        problems.append("a aba de mapeamentos mudou de tamanho")
        return
    verdicts = notes = 0
    for row, ((status, column, note), (new_status, new_column, new_note)) in enumerate(
        zip(before, after), start=2
    ):
        if rs.normalize_reviewer_status(status) != rs.normalize_reviewer_status(new_status):
            problems.append(f"parecer da linha {row}: {new_status!r}, original {status!r}")
        if not _same_cell(rs.display_name_to_external(column), new_column):
            problems.append(f"coluna correta da linha {row}: {new_column!r}, original {column!r}")
        if not _same_cell(note, new_note):
            problems.append(f"observação da linha {row} mudou")
        verdicts += bool(rs.normalize_reviewer_status(status))
        notes += isinstance(note, str) and bool(note)
    summary.verdicts, summary.notes = verdicts, notes


def verify_migration(
    original_path: Path, migrated_path: Path, kinds: dict[str, str], summary: MigrationSummary
) -> None:
    """Verificação embutida: levanta ``MigrationError`` com tudo o que divergir.

    * cada célula do migrado é a regra aplicada à original, com o mesmo tipo e
      a mesma formatação, e as abas estão na mesma ordem;
    * lidos pelo ``report_schema``, os quadros internos do original (legado) e
      do migrado (português) são iguais nas colunas que não são frase, e cada
      frase migrada é a tradução da original (e traduzi-la de novo não muda);
    * metadados iguais, exceto a versão e a chave nova da migração;
    * contagem por situação igual;
    * pareceres, colunas corretas e observações do revisor linha a linha.
    """
    problems: list[str] = []
    original_book = openpyxl.load_workbook(original_path)
    migrated_book = openpyxl.load_workbook(migrated_path)
    _check_cells(original_book, migrated_book, kinds, problems)
    _check_reviewer(original_book, migrated_book, problems, summary)
    original_frames, original_metadata = _read_report(original_path, rs.LEGACY_EN)
    migrated_frames, migrated_metadata = _read_report(migrated_path, rs.PT_BR)
    _check_frames(original_frames, migrated_frames, problems)
    _check_metadata(original_metadata, migrated_metadata, problems)
    before = Counter(original_frames["mappings"]["status"].tolist())
    after = Counter(migrated_frames["mappings"]["status"].tolist())
    if before != after:
        problems.append(f"contagem por situação {dict(after)}, original {dict(before)}")
    summary.status_counts = dict(after)
    if problems:
        raise MigrationError(
            "A verificação da migração falhou; o relatório original não foi alterado:\n  "
            + "\n  ".join(problems[: 4 * _SHOWN])
        )


# ---------------------------------------------------------------------------
# Migração
# ---------------------------------------------------------------------------


def _require_file(path: Path) -> None:
    if not path.is_file():
        raise FileNotFoundError(errno.ENOENT, os.strerror(errno.ENOENT), str(path))


def _status_line(counts: dict[str, int]) -> str:
    labels = rs.ENUMS["status"]
    return " ".join(f"{labels[status]}={counts.get(status, 0)}" for status in labels)


def migrate_report(
    report_path: str | Path,
    client_path: str | Path,
    model_path: str | Path = "data/model.xlsx",
    reference_data: str | Path = DEFAULT_REFERENCE_DATA,
    dry_run: bool = False,
    log: Callable[[str], None] = print,
) -> MigrationSummary:
    """Migra um relatório legado para o esquema em português, no próprio arquivo.

    ``client_path`` é a exportação do cliente que gerou o relatório, usada só
    na prova cruzada por regeneração. Com ``dry_run`` tudo é feito e
    verificado num arquivo temporário, e o relatório não é alterado. Levanta
    ``ValueError`` (esquema misto, verificação reprovada) ou ``OSError``.
    """
    report = Path(report_path)
    client = Path(client_path)
    model = Path(model_path)
    references = Path(reference_data)
    for path in (report, client, model):
        _require_file(path)

    workbook = openpyxl.load_workbook(report)
    header = [cell.value for cell in workbook.worksheets[0][1]]
    summary = MigrationSummary(report=report)
    if rs.detect_schema(header, "mappings") == rs.PT_BR:
        summary.already_portuguese = True
        log(ALREADY_PORTUGUESE.format(report=report))
        return summary
    kinds = _legacy_sheet_kinds(workbook)

    frames, metadata = _read_report(report, rs.LEGACY_EN)
    version = (metadata or {}).get("mapping_schema_version", "?")
    log(f"Relatório: {report} (esquema legado em inglês, versão {version})")

    regeneration = cross_check_by_regeneration(frames, metadata, client, model, references)
    summary.regeneration = regeneration
    if not regeneration.ran:
        log(f"Conferência por regeneração não feita: {regeneration.skipped_reason}.")
        log("A tradução das frases será feita só pelos modelos de frase.")
    elif regeneration.mismatches:
        log(
            f"Aviso: a regeneração diverge do arquivado em {len(regeneration.mismatches)} "
            "célula(s); as frases serão traduzidas só pelos modelos de frase."
        )
        for mismatch in regeneration.mismatches[:_SHOWN]:
            log(f"  {mismatch}")
    else:
        compared = ", ".join(f"{label}: {count}" for label, count in regeneration.compared.items())
        log(f"Conferência por regeneração: nenhuma divergência ({compared} linhas).")

    _translate_workbook(workbook, kinds, summary)
    if summary.unexpected_english:
        log(
            f"Aviso: {len(summary.unexpected_english)} frase(s) do mapeador não casaram com "
            "nenhum modelo e ficaram como estão:"
        )
        for item in summary.unexpected_english[:_SHOWN]:
            log(f"  {item}")

    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{report.stem}.", suffix=".migrando.xlsx", dir=report.parent
    )
    os.close(descriptor)
    temporary = Path(temporary_name)
    try:
        workbook.save(temporary)
        verify_migration(report, temporary, kinds, summary)
        if not dry_run:
            shutil.copymode(report, temporary)
            os.replace(temporary, report)
            summary.written = True
    finally:
        if temporary.exists():
            temporary.unlink()

    llm_prose = sum(summary.sentences_kept.values()) - len(summary.unexpected_english)
    log(
        f"Abas renomeadas: {summary.sheets_renamed}. Cabeçalhos: {summary.headers_renamed}. "
        "Valores traduzidos (situação, método, etapas, nomes de exibição, metadados etc.): "
        f"{summary.values_translated}. Frases do mapeador traduzidas: "
        f"{summary.sentences_translated}. Justificativas escritas pela IA, mantidas em "
        f"inglês: {llm_prose}."
    )
    log(
        f"Verificação: ok. {_status_line(summary.status_counts)}; "
        f"{summary.verdicts} parecer(es) e {summary.notes} observação(ões) do revisor "
        "preservados linha a linha."
    )
    if summary.written:
        log(f"Arquivo gravado: {report}")
    else:
        log("Simulação (--dry-run): o relatório não foi alterado.")
    return summary


def build_parser() -> PortugueseArgumentParser:
    """Linha de comando em português; as flags continuam em inglês."""
    parser = PortugueseArgumentParser(
        description=(
            "Migra um relatório de mapeamento do esquema legado em inglês para o esquema "
            "em português, sem reexecutar o mapeador: nenhuma decisão ou número muda, e "
            "os pareceres e observações do revisor são preservados."
        )
    )
    parser.add_argument(
        "report",
        metavar="RELATORIO",
        help="relatório de mapeamento legado (.xlsx), migrado no próprio arquivo",
    )
    parser.add_argument(
        "client",
        metavar="EXPORTACAO",
        help=(
            "exportação .xlsx do cliente que gerou o relatório, usada para conferir as "
            "partes derivadas por regeneração"
        ),
    )
    parser.add_argument(
        "--model",
        default="data/model.xlsx",
        metavar="ARQUIVO",
        help="planilha do modelo base (padrão: %(default)s)",
    )
    parser.add_argument(
        "--reference-data",
        default=DEFAULT_REFERENCE_DATA,
        metavar="DIRETORIO",
        help=(
            "diretório com o manifest.json e os catálogos de referência (padrão: a pasta "
            "reference_data ao lado deste programa)"
        ),
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="faz a migração e a verificação num arquivo temporário, sem alterar o relatório",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Ponto de entrada da CLI; erro de entrada, de arquivo ou de verificação sai com 1."""
    args = build_parser().parse_args(argv)
    try:
        migrate_report(
            args.report,
            args.client,
            model_path=args.model,
            reference_data=args.reference_data,
            dry_run=args.dry_run,
        )
    except FileNotFoundError as exc:
        raise SystemExit(f"Arquivo não encontrado: {exc.filename}") from exc
    except (ValueError, OSError) as exc:
        raise SystemExit(str(exc)) from exc
    return 0


if __name__ == "__main__":
    sys.exit(main())
