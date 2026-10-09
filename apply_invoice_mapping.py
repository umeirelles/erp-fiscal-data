"""Monta a planilha no layout do modelo a partir da exportação e do relatório de mapeamento.

O relatório é lido no esquema em português e convertido por ``report_schema``
para o vocabulário interno, o único que este módulo compara. Um relatório no
formato antigo, em inglês, é recusado com a indicação de como convertê-lo.
O entregável (abas Dados Mapeados e Cobertura) sai em português; o texto livre
do relatório (justificativa, prova aritmética, observação do revisor) é
copiado como está.
"""

from __future__ import annotations

import argparse
import math
import sys
from datetime import time
from pathlib import Path
from typing import Sequence

import pandas as pd
from openpyxl.styles import Font, PatternFill
from openpyxl.utils import get_column_letter

import report_schema
from cli_ptbr import PortugueseArgumentParser
from invoice_mapper import (
    ColumnRef,
    SheetTable,
    _error_message,
    describe_header_conflict_pt,
    file_sha256,
    semantic_header_conflict,
)


#: Linhas do banner de Dados Mapeados (linhas 2 a 22 do Excel), na ordem
#: gravada: (rótulo da coluna A, campo de Mapeamentos de onde sai o valor).
MAPPING_METADATA = [
    ("numero_coluna_cliente", "client_column_number"),
    ("coluna_cliente", "client_column"),
    ("situacao", "status"),
    ("metodo", "method"),
    ("confianca_nome", "name_confidence"),
    ("confianca_nome_segunda_melhor", "name_runner_up_confidence"),
    ("margem_nome", "name_margin"),
    ("confianca_dados", "data_confidence"),
    ("confianca_dados_ia", "llm_data_confidence"),
    ("confianca_final_ia", "llm_final_confidence"),
    ("nome_catalogo", "catalog_name"),
    ("cobertura_catalogo", "catalog_coverage"),
    ("confianca_catalogo", "catalog_confidence"),
    ("confianca_final", "final_confidence"),
    ("decidido_por", "decided_by"),
    ("apoiado_por", "supported_by"),
    ("vetado_por", "vetoed_by"),
    ("prova_aritmetica", "arithmetic_proof"),
    ("justificativa", "reason"),
    ("parecer_revisor", "reviewer_status"),
    ("observacao_revisor", "reviewer_note"),
]

#: Primeira linha do Excel com dados do cliente, abaixo do cabeçalho e do
#: banner. Já mudou mais de uma vez: importe-a em vez de fixar o número.
FIRST_DATA_EXCEL_ROW = len(MAPPING_METADATA) + 2

#: Campos do banner gravados como número e exibidos como porcentagem.
CONFIDENCE_ROWS = {
    "name_confidence",
    "name_runner_up_confidence",
    "name_margin",
    "data_confidence",
    "llm_data_confidence",
    "llm_final_confidence",
    "catalog_coverage",
    "catalog_confidence",
    "final_confidence",
}

#: Colunas sem as quais o relatório não pode ser aplicado.
REQUIRED_MAPPING_COLUMNS = {
    "model_column_number",
    "model_column",
    "client_column_number",
    "client_column",
    "name_confidence",
    "name_runner_up_confidence",
    "name_margin",
    "data_confidence",
    "final_confidence",
    "method",
    "status",
    "reason",
}

#: Colunas que os relatórios mais antigos não têm; entram em branco, para que
#: esses relatórios continuem aplicáveis.
OPTIONAL_MAPPING_COLUMNS = (
    "decided_by",
    "supported_by",
    "vetoed_by",
    "arithmetic_proof",
    "catalog_name",
    "catalog_coverage",
    "catalog_confidence",
    "llm_data_confidence",
    "llm_final_confidence",
    "reviewer_status",
    "correct_client_column",
    "reviewer_note",
)

#: Situação de cada coluna do modelo na aba Cobertura, na ordem em que a aba
#: as lista. Só a do meio é trabalho de alguém: uma coluna que o cliente não
#: envia é uma resposta completa.
STATE_DELIVERED = "entregue"
STATE_PENDING = "aguardando decisão"
STATE_ABSENT = "não consta na exportação do cliente"
COVERAGE_STATE_ORDER = (STATE_DELIVERED, STATE_PENDING, STATE_ABSENT)

#: Colunas da aba Cobertura (nome interno -> largura). O cabeçalho gravado é o
#: português de ``report_schema.COLUMNS["coverage"]``.
COVERAGE_FIELDS = {
    "state": 26,
    "model_column_number": 10,
    "model_column": 34,
    "excel_column": 8,
    "client_column_number": 10,
    "client_column": 30,
    "final_confidence": 12,
    "reason": 78,
}

PERCENT_FORMAT = "0.00%"
TIME_FORMAT = "hh:mm:ss"
#: Fundo azul do banner (e do cabeçalho da Cobertura).
BANNER_COLOR = "E8F0FE"
#: Fundo verde do cabeçalho das colunas entregues.
DELIVERED_COLOR = "D7E9DC"
INFO_COLUMN_WIDTH = 34
MODEL_COLUMN_WIDTH = 18
#: Até esta coluna do Excel (AD) a largura é fixada mesmo nas colunas ocultas.
LAST_FIXED_WIDTH_EXCEL_COLUMN = 30


def _sheet_argument(value: str) -> str | int:
    """Aba dada na linha de comando: só dígitos é o índice; outro texto é o nome."""
    return int(value) if value.isdigit() else value


def _clean_text(value: object) -> str:
    """Texto da célula sem espaços nas pontas; célula vazia vira ``""``."""
    return "" if pd.isna(value) else str(value).strip()


def _blank_if_missing(value: object) -> object:
    """Célula vazia vira ``""``; qualquer outro valor passa intacto."""
    return "" if pd.isna(value) else value


def _column_label(field: str) -> str:
    """Cabeçalho de Mapeamentos de um campo, como a pessoa o vê na planilha."""
    return report_schema.header_label("mappings", field)


def _status_label(status: str) -> str:
    """Parecer interno na grafia do relatório (``incorrect`` -> ``incorreto``)."""
    return report_schema.ENUMS["reviewer_status"][status]


def _display_name(text: object) -> object:
    """Nome de exibição interno -> português (``(occurrence 2)`` -> ``(ocorrência 2)``)."""
    return report_schema.display_name_to_external(text)


# ---------------------------------------------------------------------------
# Leitura e conferência do relatório
# ---------------------------------------------------------------------------


def _read_mapping_report(mapping_path: str | Path) -> pd.DataFrame:
    """Lê a primeira aba do relatório e a converte para nomes e valores internos.

    A aba é lida pela posição, não pelo nome, então Mapeamentos precisa
    continuar sendo a primeira. ``to_internal_frame`` recusa o formato antigo.
    """
    mapping = pd.read_excel(mapping_path, dtype=object)
    return report_schema.to_internal_frame(mapping, "mappings")


def _run_metadata(mapping_path: str | Path) -> dict[str, str]:
    """Metadados da execução que gerou o relatório, com chaves internas.

    Relatório sem a aba devolve ``{}`` e é aplicado sem verificação de hash.
    Uma aba com o nome do formato antigo é recusada, para que a verificação
    não seja pulada em silêncio.
    """
    workbook = pd.ExcelFile(mapping_path)
    name = report_schema.find_sheet(workbook.sheet_names, "run_metadata")
    if name is None:
        return {}
    frame = pd.read_excel(workbook, sheet_name=name, dtype=object)
    return report_schema.internal_metadata(frame)


def _validate_input_hashes(
    metadata: dict[str, str],
    *,
    client_path: str | Path,
    model_path: str | Path,
) -> None:
    """Recusa o relatório se a exportação ou o modelo mudaram desde a execução que o gerou."""
    for key, path in (("client_sha256", client_path), ("model_sha256", model_path)):
        expected = metadata.get(key)
        if expected and expected != file_sha256(path):
            raise ValueError(
                f"{Path(path).name} difere do arquivo usado para gerar o relatório de mapeamento"
            )


def _ordered_mapping(mapping: pd.DataFrame, model: SheetTable) -> pd.DataFrame:
    """Confere o relatório com o modelo e devolve uma linha por coluna do modelo, na ordem dele.

    Também completa as colunas opcionais ausentes e traz o parecer do revisor
    para o vocabulário interno.
    """
    missing = REQUIRED_MAPPING_COLUMNS - set(mapping.columns)
    if missing:
        labels = sorted(_column_label(field) for field in missing)
        raise ValueError(f"Faltam colunas no relatório de mapeamento: {labels}")

    mapping = mapping.copy()
    mapping["model_column_number"] = pd.to_numeric(
        mapping["model_column_number"], errors="raise"
    ).astype(int)
    if mapping["model_column_number"].duplicated().any():
        raise ValueError(
            "O relatório de mapeamento tem valores repetidos em "
            f"{_column_label('model_column_number')}"
        )
    expected_numbers = list(range(1, len(model.columns) + 1))
    if sorted(mapping["model_column_number"].tolist()) != expected_numbers:
        raise ValueError(
            f"O relatório de mapeamento deve conter cada coluna do modelo "
            f"1..{len(model.columns)} exatamente uma vez"
        )

    mapping = mapping.set_index("model_column_number").loc[expected_numbers].reset_index()
    for row, model_column in zip(mapping.to_dict("records"), model.columns, strict=True):
        written = _clean_text(row["model_column"])
        if written != model_column.name:
            raise ValueError(
                f"A coluna do modelo {model_column.index + 1} difere entre o modelo e o "
                f"relatório de mapeamento: {model_column.name!r} != {written!r}"
            )

    for field in OPTIONAL_MAPPING_COLUMNS:
        if field not in mapping.columns:
            mapping[field] = ""
    mapping["reviewer_status"] = [_reviewer_status(row) for row in mapping.to_dict("records")]
    return mapping


def _reviewer_status(row: dict) -> str:
    """Parecer do revisor no vocabulário interno; um valor desconhecido é recusado."""
    status = report_schema.normalize_reviewer_status(row["reviewer_status"])
    if status is None:
        raise ValueError(
            f"Valor desconhecido em {_column_label('reviewer_status')}: "
            f"{row['reviewer_status']!r}, na coluna do modelo {row['model_column']!r}. Use "
            f"{_status_label('correct')}, {_status_label('incorrect')} ou "
            f"{_status_label('no_mapping_exists')}, ou deixe em branco."
        )
    return status


# ---------------------------------------------------------------------------
# Escolha da coluna do cliente de cada coluna do modelo
# ---------------------------------------------------------------------------


def _column_from_number(client: SheetTable, value: object) -> ColumnRef | None:
    """Coluna do cliente pelo número (a partir de 1); célula vazia devolve ``None``."""
    if pd.isna(value) or _clean_text(value) == "":
        return None
    number = float(value)
    if not number.is_integer():
        raise ValueError(f"O número da coluna do cliente deve ser inteiro; recebido {value!r}")
    if not 1 <= number <= len(client.columns):
        raise ValueError(
            f"O número da coluna do cliente {int(number)} está fora do intervalo "
            f"1..{len(client.columns)}"
        )
    return client.columns[int(number) - 1]


def _corrected_column(client: SheetTable, value: object) -> ColumnRef:
    """Coluna do cliente que o revisor indicou em ``coluna_cliente_correta``.

    Aceita o número da coluna (a partir de 1), o cabeçalho ou, para um
    cabeçalho repetido, o nome com ``(ocorrência N)`` ou ``(ocorrencia N)``,
    sem diferenciar maiúsculas. Texto que se lê como número é sempre um número
    de coluna.
    """
    typed = _clean_text(value)
    if not typed:
        raise ValueError(
            f"{_column_label('reviewer_status')} é {_status_label('incorrect')!r}, mas "
            f"{_column_label('correct_client_column')} está em branco"
        )

    try:
        number = float(typed)
    except ValueError:
        number = math.nan
    if not math.isnan(number):
        return _column_from_number(client, number)

    wanted = report_schema.display_name_to_internal(typed).casefold()
    by_display_name = [c for c in client.columns if c.display_name.casefold() == wanted]
    if len(by_display_name) == 1:
        return by_display_name[0]
    by_name = [c for c in client.columns if c.name.casefold() == wanted]
    if len(by_name) == 1:
        return by_name[0]
    if len(by_name) > 1:
        raise ValueError(
            f"O cabeçalho corrigido {_display_name(typed)!r} está repetido na exportação do "
            "cliente; use o número da coluna (contado a partir de 1) ou o nome com a "
            "ocorrência, como 'Nome (ocorrência 2)'"
        )
    raise ValueError(
        f"A coluna do cliente corrigida {_display_name(typed)!r} não foi encontrada"
    )


def _refuse_unused_correction(row: dict) -> None:
    """Recusa uma correção ao lado de um parecer que não é ``incorreto``.

    Só ``incorreto`` usa a correção. Com ``correto`` ela seria descartada e a
    linha entregue com a coluna do mapeador, não com a do revisor.
    """
    correction = _clean_text(row["correct_client_column"])
    reviewer_status = row["reviewer_status"]
    if not correction or reviewer_status == "incorrect":
        return
    situation = (
        "está em branco" if reviewer_status == "" else f"é {_status_label(reviewer_status)!r}"
    )
    raise ValueError(
        f"{_column_label('correct_client_column')} {correction!r} está preenchida na coluna "
        f"do modelo {row['model_column']!r}, mas {_column_label('reviewer_status')} "
        f"{situation}. Só {_status_label('incorrect')!r} usa uma correção; com qualquer "
        "outro parecer ela seria descartada em silêncio."
    )


def _reviewed_column(
    row: dict, client: SheetTable, accepted_only: bool
) -> tuple[ColumnRef | None, bool]:
    """(coluna do cliente que a linha entrega, se ela é aceita ou confirmada pelo revisor)."""
    reviewer_status = row["reviewer_status"]
    if reviewer_status == "no_mapping_exists":
        return None, False
    if reviewer_status == "incorrect":
        return _corrected_column(client, row["correct_client_column"]), True

    proposal = _column_from_number(client, row["client_column_number"])
    if reviewer_status == "correct":
        # Sem proposta, confirmar entregaria uma coluna em branco assinada pelo revisor.
        if proposal is None:
            raise ValueError(
                f"{_column_label('reviewer_status')} é {_status_label('correct')!r} na coluna "
                f"do modelo {row['model_column']!r}, mas "
                f"{_column_label('client_column_number')} está em branco. Use "
                f"{_status_label('no_mapping_exists')!r} para confirmar que o cliente não "
                f"tem este campo, ou {_status_label('incorrect')!r} com "
                f"{_column_label('correct_client_column')}."
            )
        return proposal, True

    accepted = row["status"] == "accepted"
    if accepted_only and not accepted:
        return None, False
    return proposal, accepted


def _refuse_header_conflict(model_column: ColumnRef, column: ColumnRef) -> None:
    """Recusa um par que as regras atuais de coerência entre cabeçalhos proíbem."""
    conflict = semantic_header_conflict(model_column.name, column.name)
    if conflict is None:
        return
    description = describe_header_conflict_pt(model_column.name, column.name) or conflict
    raise ValueError(
        f"O mapeamento aceito {model_column.name!r} -> "
        f"{_display_name(column.display_name)!r} viola as regras atuais de "
        f"coerência entre cabeçalhos: {description}. Revise-o explicitamente "
        f"(preencha {_column_label('reviewer_status')}) antes de aplicar."
    )


def _refuse_proposal_from_another_export(row: dict, client: SheetTable) -> None:
    """Recusa a linha se a coluna proposta não tem na exportação o nome gravado no relatório."""
    proposed = _column_from_number(client, row["client_column_number"])
    proposed_name = _clean_text(row["client_column"])
    if proposed is None or not proposed_name or proposed.display_name == proposed_name:
        return
    raise ValueError(
        f"O relatório de mapeamento não confere com a planilha do cliente na coluna "
        f"{proposed.index + 1}: {_display_name(proposed_name)!r} != "
        f"{_display_name(proposed.display_name)!r}"
    )


def _effective_columns(
    mapping: pd.DataFrame,
    client: SheetTable,
    model: SheetTable,
    accepted_only: bool,
) -> list[ColumnRef | None]:
    """Coluna do cliente que cada coluna do modelo recebe, depois dos pareceres do revisor.

    Só as colunas aceitas ou confirmadas precisam ser um para um: propostas em
    revisão não confirmadas podem apontar para a mesma coluna de origem.
    """
    selected: list[ColumnRef | None] = []
    confirmed_indexes: set[int] = set()
    for row, model_column in zip(mapping.to_dict("records"), model.columns, strict=True):
        _refuse_unused_correction(row)
        column, confirmed = _reviewed_column(row, client, accepted_only)
        if column is not None and row["status"] == "accepted" and row["reviewer_status"] == "":
            _refuse_header_conflict(model_column, column)
        _refuse_proposal_from_another_export(row, client)

        if column is not None and confirmed:
            if column.index in confirmed_indexes:
                raise ValueError(
                    f"A coluna do cliente {_display_name(column.display_name)!r} foi mapeada "
                    "mais de uma vez após as correções do revisor"
                )
            confirmed_indexes.add(column.index)
        selected.append(column)
    return selected


# ---------------------------------------------------------------------------
# Gravação da planilha
# ---------------------------------------------------------------------------


def _banner_rows(
    mapping: pd.DataFrame, selected: Sequence[ColumnRef | None]
) -> list[list[object]]:
    """Linhas do banner: o rótulo e um valor por coluna do modelo, em português.

    ``numero_coluna_cliente`` e ``coluna_cliente`` mostram a coluna de fato
    entregue, depois dos pareceres do revisor.
    """
    delivered = {
        "client_column_number": ["" if column is None else column.index + 1 for column in selected],
        "client_column": ["" if column is None else column.display_name for column in selected],
    }
    rows: list[list[object]] = []
    for label, field in MAPPING_METADATA:
        if field in delivered:
            values = delivered[field]
        else:
            values = [_blank_if_missing(value) for value in mapping[field]]
        rows.append(
            [label, *(report_schema.external_value("banner", field, value) for value in values)]
        )
    return rows


def _client_data(
    client: SheetTable, model: SheetTable, selected: Sequence[ColumnRef | None], info_header: str
) -> pd.DataFrame:
    """Valores do cliente sem alteração, uma coluna por coluna do modelo; sem origem, vazia."""
    row_count = len(client.data)
    data = pd.concat(
        [
            pd.Series([pd.NA] * row_count, dtype=object)
            if column is None
            else client.series(column).reset_index(drop=True)
            for column in selected
        ],
        axis=1,
    )
    data.columns = [column.name for column in model.columns]
    data.insert(0, info_header, "")
    return data


def _format_banner(worksheet, column_count: int) -> None:
    """Fundo azul no banner e confianças exibidas como porcentagem."""
    fill = PatternFill(fill_type="solid", fgColor=BANNER_COLOR)
    for excel_row in range(2, FIRST_DATA_EXCEL_ROW):
        for cell in worksheet[excel_row]:
            cell.fill = fill
    for excel_row, (_, field) in enumerate(MAPPING_METADATA, start=2):
        if field in CONFIDENCE_ROWS:
            for excel_column in range(2, column_count + 1):
                worksheet.cell(excel_row, excel_column).number_format = PERCENT_FORMAT


def _restore_time_cells(
    worksheet, client: SheetTable, selected: Sequence[ColumnRef | None]
) -> None:
    """Regrava como hora do Excel os ``datetime.time``, que o pandas grava como texto."""
    for excel_column, column in enumerate(selected, start=2):
        if column is None:
            continue
        for excel_row, value in enumerate(client.series(column), start=FIRST_DATA_EXCEL_ROW):
            if isinstance(value, time):
                cell = worksheet.cell(excel_row, excel_column)
                cell.value = value
                cell.number_format = TIME_FORMAT


def _arrange_columns(worksheet, selected: Sequence[ColumnRef | None], column_count: int) -> None:
    """Larguras, cabeçalho verde nas colunas entregues e colunas sem dado ocultas.

    Um cliente preenche uma minoria das colunas do modelo, e as entregues
    ficam espalhadas. Ocultar as vazias põe as entregues lado a lado sem
    remover nada: um clique as reexibe, e quem lê por programa vê todas.
    """
    worksheet.column_dimensions["A"].width = INFO_COLUMN_WIDTH
    for excel_column in range(2, min(column_count, LAST_FIXED_WIDTH_EXCEL_COLUMN) + 1):
        worksheet.column_dimensions[get_column_letter(excel_column)].width = MODEL_COLUMN_WIDTH

    delivered_fill = PatternFill(fill_type="solid", fgColor=DELIVERED_COLOR)
    for excel_column, column in enumerate(selected, start=2):
        letter = get_column_letter(excel_column)
        if column is None:
            worksheet.column_dimensions[letter].hidden = True
        else:
            worksheet.column_dimensions[letter].width = MODEL_COLUMN_WIDTH
            worksheet.cell(1, excel_column).fill = delivered_fill


def _coverage_row(
    model_column: ColumnRef, column: ColumnRef | None, row: dict
) -> list[object]:
    """Linha da Cobertura de uma coluna do modelo.

    A situação segue a ``situacao`` do mapeador, não o parecer do revisor, e
    uma linha entregue fica sem ``numero_coluna_cliente`` (KNOWN_ISSUES 1.2 e
    1.3).
    """
    if column is not None:
        state = STATE_DELIVERED
        client_number, client_name = "", column.display_name
    else:
        mapper_status = _clean_text(row["status"]).casefold()
        state = STATE_PENDING if mapper_status == "review" else STATE_ABSENT
        client_number = _blank_if_missing(row["client_column_number"])
        client_name = _blank_if_missing(row["client_column"])
    return [
        state,
        model_column.index + 1,
        model_column.name,
        get_column_letter(model_column.index + 2),
        client_number,
        _display_name(client_name),
        _blank_if_missing(row["final_confidence"]),
        _blank_if_missing(row["reason"]),
    ]


def _write_coverage_sheet(
    book,
    *,
    model: SheetTable,
    selected: Sequence[ColumnRef | None],
    mapping: pd.DataFrame,
) -> None:
    """Aba Cobertura: a situação de cada coluna do modelo, entregues primeiro.

    Dados Mapeados só mostra o que foi entregue; esta aba diz o que falta e se
    é decisão pendente ou algo que o cliente de fato não envia.
    """
    sheet = book.create_sheet(report_schema.sheet_name("coverage"))
    sheet.append([report_schema.header_label("coverage", field) for field in COVERAGE_FIELDS])
    rows = [
        _coverage_row(model_column, column, row)
        for model_column, column, row in zip(model.columns, selected, mapping.to_dict("records"))
    ]
    for values in sorted(rows, key=lambda values: COVERAGE_STATE_ORDER.index(values[0])):
        sheet.append(values)

    last_letter = get_column_letter(len(COVERAGE_FIELDS))
    sheet.freeze_panes = "A2"
    sheet.auto_filter.ref = f"A1:{last_letter}{sheet.max_row}"
    for position, width in enumerate(COVERAGE_FIELDS.values(), start=1):
        sheet.column_dimensions[get_column_letter(position)].width = width
    header_fill = PatternFill(fill_type="solid", fgColor=BANNER_COLOR)
    for cell in sheet[1]:
        cell.fill = header_fill
        cell.font = Font(bold=True)
    confidence_column = list(COVERAGE_FIELDS).index("final_confidence") + 1
    for (cell,) in sheet.iter_rows(
        min_row=2, min_col=confidence_column, max_col=confidence_column
    ):
        cell.number_format = PERCENT_FORMAT


def build_mapped_workbook(
    client_path: str | Path,
    mapping_path: str | Path,
    output_path: str | Path,
    model_path: str | Path = "data/model.xlsx",
    model_sheet: str | int = 0,
    client_sheet: str | int = 0,
    model_header_row: int = 0,
    client_header_row: int = 0,
    accepted_only: bool = True,
) -> None:
    """Aplica os mapeamentos e grava Dados Mapeados (banner e dados do cliente) e Cobertura.

    Por padrão só entram as linhas aceitas sem parecer e as confirmadas pelo
    revisor; com ``accepted_only=False`` entram também as propostas em revisão
    não confirmadas, numa planilha que não deve ser entregue.
    """
    report = _read_mapping_report(mapping_path)
    _validate_input_hashes(
        _run_metadata(mapping_path), client_path=client_path, model_path=model_path
    )
    model = SheetTable.from_excel(model_path, model_sheet, model_header_row)
    client = SheetTable.from_excel(client_path, client_sheet, client_header_row)
    mapping = _ordered_mapping(report, model)
    selected = _effective_columns(mapping, client, model, accepted_only)

    info_header = report_schema.header_label("mapped_data", "__mapping_info__")
    output_columns = [info_header, *[column.name for column in model.columns]]
    banner = pd.DataFrame(_banner_rows(mapping, selected), columns=output_columns)
    data = _client_data(client, model, selected, info_header)
    output_frame = pd.concat([banner, data], axis=0, ignore_index=True)

    output = Path(output_path)
    if output.suffix.lower() != ".xlsx":
        raise ValueError("O arquivo de saída deve terminar em .xlsx")
    mapped_sheet = report_schema.sheet_name("mapped_data")
    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        output_frame.to_excel(writer, sheet_name=mapped_sheet, index=False)
        worksheet = writer.book[mapped_sheet]
        worksheet.freeze_panes = f"B{FIRST_DATA_EXCEL_ROW}"
        _format_banner(worksheet, len(output_columns))
        _restore_time_cells(worksheet, client, selected)
        _arrange_columns(worksheet, selected, len(output_columns))
        _write_coverage_sheet(writer.book, model=model, selected=selected, mapping=mapping)

    print(f"Arquivo gravado: {output}")
    print(
        f"colunas_modelo={len(model.columns)} linhas_cliente={len(client.data)} "
        f"colunas_mapeadas={sum(column is not None for column in selected)}"
    )


def build_parser() -> argparse.ArgumentParser:
    """Linha de comando em português; as flags continuam em inglês."""
    parser = PortugueseArgumentParser(
        description=(
            "Aplica um relatório de mapeamento e gera a planilha do cliente no layout do "
            "modelo."
        )
    )
    parser.add_argument("client", metavar="EXPORTACAO", help="exportação .xlsx do cliente")
    parser.add_argument(
        "mapping",
        metavar="RELATORIO",
        help="relatório de mapeamento gerado pelo invoice_mapper.py",
    )
    parser.add_argument(
        "--model",
        default="data/model.xlsx",
        metavar="ARQUIVO",
        help="planilha do modelo base (padrão: %(default)s)",
    )
    parser.add_argument(
        "--output", required=True, metavar="ARQUIVO", help="planilha .xlsx de saída"
    )
    parser.add_argument(
        "--model-sheet",
        default="0",
        metavar="ABA",
        help="índice (a partir de 0) ou nome da aba do modelo",
    )
    parser.add_argument(
        "--client-sheet",
        default="0",
        metavar="ABA",
        help="índice (a partir de 0) ou nome da aba do cliente",
    )
    parser.add_argument(
        "--model-header-row",
        type=int,
        default=1,
        metavar="LINHA",
        help="linha do cabeçalho do modelo (contada a partir de 1)",
    )
    parser.add_argument(
        "--client-header-row",
        type=int,
        default=1,
        metavar="LINHA",
        help="linha do cabeçalho do cliente (contada a partir de 1)",
    )
    parser.add_argument(
        "--accepted-only",
        action="store_true",
        help="obsoleta: mapeamentos aceitos e confirmados pelo revisor já são o padrão",
    )
    parser.add_argument(
        "--include-review",
        action="store_true",
        help=(
            "preenche também as propostas em revisão não confirmadas, numa planilha de "
            "revisão explicitamente insegura, que não deve ser entregue"
        ),
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Ponto de entrada da CLI: erro de entrada ou de arquivo sai com a mensagem e código 1."""
    args = build_parser().parse_args(argv)
    if args.accepted_only and args.include_review:
        raise SystemExit("--accepted-only e --include-review não podem ser usados juntos")
    if args.model_header_row < 1 or args.client_header_row < 1:
        raise SystemExit(
            "--model-header-row e --client-header-row devem ser positivos "
            "(a contagem começa em 1)"
        )
    try:
        build_mapped_workbook(
            client_path=args.client,
            mapping_path=args.mapping,
            output_path=args.output,
            model_path=args.model,
            model_sheet=_sheet_argument(args.model_sheet),
            client_sheet=_sheet_argument(args.client_sheet),
            model_header_row=args.model_header_row - 1,
            client_header_row=args.client_header_row - 1,
            accepted_only=not args.include_review,
        )
    except OSError as exc:
        raise SystemExit(_error_message(exc)) from exc
    except ValueError as exc:
        # As validações deste módulo já falam português; as do pandas passam
        # como vieram (KNOWN_ISSUES 6.2).
        raise SystemExit(str(exc)) from exc
    return 0


if __name__ == "__main__":
    sys.exit(main())
