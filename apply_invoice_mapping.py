"""Monta a planilha no layout do modelo a partir da exportação e do relatório de mapeamento.

O relatório está no esquema em português (aba Mapeamentos,
``numero_coluna_modelo``, situação ``aceito`` ...). A leitura passa por
``report_schema``, que confere o cabeçalho da primeira aba e converte nomes e
valores para o vocabulário interno do código; as decisões deste módulo
comparam só valores internos. Um relatório no formato antigo, em inglês, é
recusado com a indicação de como convertê-lo.

O entregável é gravado em português: abas Dados Mapeados e Cobertura,
``__info_mapeamento__`` na célula A1, rótulos do banner, valores enumerados,
etapas e o sufixo ``(ocorrência N)`` dos nomes de coluna repetidos. O texto
livre (justificativa, prova aritmética, observação do revisor) é copiado como
está no relatório; a prosa escrita pela IA e as observações dos revisores
ficam como foram escritas.
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


#: Linhas do banner de Dados Mapeados (linhas 2 a 22 do Excel), na ordem em
#: que são gravadas: (rótulo da coluna A, campo de Mapeamentos de onde sai o
#: valor). O rótulo é o cabeçalho em português do campo; o campo continua com
#: o nome interno, que é o usado em memória e nas comparações.
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

#: Linha do Excel onde começam os dados do cliente: cabeçalho, depois o banner
#: de metadados. Exportada porque esse deslocamento já mudou duas vezes (de 17
#: para 19, quando entraram as pontuações de auditoria da IA, e de novo aqui),
#: e qualquer código a jusante que fixe o número quebra em silêncio.
FIRST_DATA_EXCEL_ROW = len(MAPPING_METADATA) + 2

#: Campos do banner que são confianças: gravados como número e exibidos como
#: porcentagem. Indexado pelo campo, e não pelo rótulo da coluna A, para que
#: o texto do rótulo possa mudar sem afetar a formatação.
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

#: Colunas sem as quais o relatório não pode ser aplicado (nomes internos; as
#: mensagens citam o cabeçalho em português).
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


def _sheet_argument(value: str) -> str | int:
    """Aba dada na linha de comando: só dígitos é o índice; outro texto é o nome."""
    return int(value) if value.isdigit() else value


def _clean_text(value: object) -> str:
    """Texto da célula sem espaços nas pontas; célula vazia vira ``""``."""
    return "" if pd.isna(value) else str(value).strip()


def _column_label(field: str) -> str:
    """Cabeçalho em português de um campo de Mapeamentos, para citar em mensagens.

    Quem lê a mensagem procura a coluna pelo nome que vê na planilha
    (``parecer_revisor``), e não pelo nome interno.
    """
    return report_schema.header_label("mappings", field)


def _status_label(status: str) -> str:
    """Parecer interno na grafia do relatório (``incorrect`` -> ``incorreto``)."""
    return report_schema.ENUMS["reviewer_status"][status]


def _display_name(text: object) -> object:
    """Nome de exibição interno de coluna do cliente -> como aparece para pessoas.

    ``"Valor ICMS (occurrence 2)"`` vira ``"Valor ICMS (ocorrência 2)"``. Em
    memória o nome continua em inglês, porque ``ColumnRef.name`` é entrada do
    embedding e da IA.
    """
    return report_schema.display_name_to_external(text)


def _column_from_number(client: SheetTable, value: object) -> ColumnRef | None:
    """Coluna do cliente pelo número (contado a partir de 1); célula vazia devolve ``None``."""
    if pd.isna(value) or _clean_text(value) == "":
        return None
    number = float(value)
    if not number.is_integer():
        raise ValueError(f"O número da coluna do cliente deve ser inteiro; recebido {value!r}")
    index = int(number) - 1
    if index < 0 or index >= len(client.columns):
        raise ValueError(
            f"O número da coluna do cliente {int(number)} está fora do intervalo "
            f"1..{len(client.columns)}"
        )
    return client.columns[index]


def _corrected_column(client: SheetTable, value: object) -> ColumnRef:
    """Coluna do cliente indicada pelo revisor em ``correct_client_column``.

    Aceita o número da coluna (contado a partir de 1), o cabeçalho da
    exportação ou, para um cabeçalho repetido, o nome com a ocorrência, nas
    formas ``(ocorrência N)`` ou ``(ocorrencia N)``. A comparação ignora
    maiúsculas e minúsculas. Texto que pode ser lido como número é sempre um
    número de coluna.
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
        column = _column_from_number(client, number)
        if column is None:
            raise ValueError("A coluna do cliente corrigida não pode ficar em branco")
        return column

    # O sufixo em português volta aqui à forma interna de ColumnRef.display_name.
    text = report_schema.display_name_to_internal(typed)
    display_matches = [
        column for column in client.columns if column.display_name.casefold() == text.casefold()
    ]
    if len(display_matches) == 1:
        return display_matches[0]

    name_matches = [
        column for column in client.columns if column.name.casefold() == text.casefold()
    ]
    if len(name_matches) == 1:
        return name_matches[0]
    if len(name_matches) > 1:
        raise ValueError(
            f"O cabeçalho corrigido {_display_name(typed)!r} está repetido na exportação do "
            "cliente; use o número da coluna (contado a partir de 1) ou o nome com a "
            "ocorrência, como 'Nome (ocorrência 2)'"
        )
    raise ValueError(
        f"A coluna do cliente corrigida {_display_name(typed)!r} não foi encontrada"
    )


def _read_mapping_report(mapping_path: str | Path) -> pd.DataFrame:
    """Lê a primeira aba do relatório e a converte para nomes e valores internos.

    A aba de mapeamentos é lida pela posição, e não pelo nome, como sempre
    foi: ela precisa continuar sendo a primeira. Um relatório no formato
    antigo, em inglês, é recusado aqui (``report_schema.check_header``).
    """
    mapping = pd.read_excel(mapping_path, dtype=object)
    return report_schema.to_internal_frame(mapping, "mappings")


def _ordered_mapping(mapping: pd.DataFrame, model: SheetTable) -> pd.DataFrame:
    """Confere o relatório com o modelo e devolve uma linha por coluna do modelo, na ordem dele.

    O parecer do revisor sai daqui no vocabulário interno. Um parecer que não
    é ``correto``, ``incorreto``, ``inexistente`` nem vazio é recusado, com o
    que foi digitado e a coluna do modelo.
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
    actual_numbers = sorted(mapping["model_column_number"].tolist())
    if actual_numbers != expected_numbers:
        raise ValueError(
            f"O relatório de mapeamento deve conter cada coluna do modelo "
            f"1..{len(model.columns)} exatamente uma vez"
        )

    mapping = mapping.set_index("model_column_number").loc[expected_numbers].reset_index()
    for row, model_column in zip(mapping.to_dict("records"), model.columns, strict=True):
        if _clean_text(row["model_column"]) != model_column.name:
            raise ValueError(
                f"A coluna do modelo {model_column.index + 1} difere entre o modelo e o "
                f"relatório de mapeamento: {model_column.name!r} != "
                f"{_clean_text(row['model_column'])!r}"
            )

    for optional in [
        # Relatórios gravados antes de essas colunas existirem continuam aplicáveis.
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
    ]:
        if optional not in mapping.columns:
            mapping[optional] = ""

    statuses = []
    for row in mapping.to_dict("records"):
        status = report_schema.normalize_reviewer_status(row["reviewer_status"])
        if status is None:
            raise ValueError(
                f"Valor desconhecido em {_column_label('reviewer_status')}: "
                f"{row['reviewer_status']!r}, na coluna do modelo {row['model_column']!r}. Use "
                f"{_status_label('correct')}, {_status_label('incorrect')} ou "
                f"{_status_label('no_mapping_exists')}, ou deixe em branco."
            )
        statuses.append(status)
    mapping["reviewer_status"] = statuses
    return mapping


def _run_metadata(mapping_path: str | Path) -> dict[str, str]:
    """Metadados da execução do relatório, com chaves internas.

    Procura a aba Metadados da Execução. Uma aba com o nome do formato
    antigo, em inglês (Run Metadata), levanta ``ValueError``: ignorada, ela
    faria a verificação de hash ser pulada em silêncio. Relatórios antigos
    sem a aba continuam aplicáveis, sem verificação.
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
    for key, path in (
        ("client_sha256", client_path),
        ("model_sha256", model_path),
    ):
        expected = metadata.get(key)
        if expected and expected != file_sha256(path):
            raise ValueError(
                f"{Path(path).name} difere do arquivo usado para gerar o relatório de mapeamento"
            )


def _effective_columns(
    mapping: pd.DataFrame,
    client: SheetTable,
    model: SheetTable,
    accepted_only: bool,
) -> list[ColumnRef | None]:
    """Coluna do cliente que cada coluna do modelo recebe, depois dos pareceres do revisor.

    ``mapping`` já está no vocabulário interno, inclusive o parecer do revisor
    (ver ``_ordered_mapping``).
    """
    selected: list[ColumnRef | None] = []
    used: set[int] = set()
    status_column = _column_label("reviewer_status")
    correction_column = _column_label("correct_client_column")

    for row, model_column in zip(
        mapping.to_dict("records"), model.columns, strict=True
    ):
        reviewer_status = row["reviewer_status"]

        # Uma correção só é usada no ramo ``incorrect``. Em todos os outros ela
        # seria descartada em silêncio, e no ``correct`` o descarte é pior do
        # que perder a resposta: a linha continua sendo entregue, com a coluna
        # do *mapeador* e não a do revisor, e um revisor que escolhe o parecer
        # errado põe dado errado do cliente no modelo base em vez de deixá-lo
        # em branco. O erro espelhado (``incorrect`` com a correção em branco)
        # sempre foi recusado. Aqui a regra fica simétrica.
        correction = _clean_text(row["correct_client_column"])
        if correction and reviewer_status != "incorrect":
            situation = (
                "está em branco"
                if reviewer_status == ""
                else f"é {_status_label(reviewer_status)!r}"
            )
            raise ValueError(
                f"{correction_column} {correction!r} está preenchida na coluna do modelo "
                f"{row['model_column']!r}, mas {status_column} {situation}. Só "
                f"{_status_label('incorrect')!r} usa uma correção; com qualquer "
                "outro parecer ela seria descartada em silêncio."
            )

        confirmed = False
        if reviewer_status == "no_mapping_exists":
            column = None
        elif reviewer_status == "incorrect":
            column = _corrected_column(client, row["correct_client_column"])
            confirmed = True
        elif reviewer_status == "correct":
            column = _column_from_number(client, row["client_column_number"])
            # 'correct' afirma que a proposta está certa, então precisa haver
            # proposta. Sem esta trava a linha é confirmada sobre nada e
            # entrega uma coluna em branco assinada pelo revisor, com código
            # de saída 0.
            if column is None:
                raise ValueError(
                    f"{status_column} é {_status_label('correct')!r} na coluna do "
                    f"modelo {row['model_column']!r}, mas "
                    f"{_column_label('client_column_number')} está em branco. Use "
                    f"{_status_label('no_mapping_exists')!r} para confirmar que o "
                    f"cliente não tem este campo, ou {_status_label('incorrect')!r} "
                    f"com {correction_column}."
                )
            confirmed = True
        elif accepted_only and reviewer_status != "correct" and row["status"] != "accepted":
            column = None
        else:
            column = _column_from_number(client, row["client_column_number"])
            confirmed = row["status"] == "accepted"

        if (
            column is not None
            and row["status"] == "accepted"
            and reviewer_status == ""
        ):
            conflict = semantic_header_conflict(model_column.name, column.name)
            if conflict is not None:
                description = (
                    describe_header_conflict_pt(model_column.name, column.name) or conflict
                )
                raise ValueError(
                    f"O mapeamento aceito {model_column.name!r} -> "
                    f"{_display_name(column.display_name)!r} viola as regras atuais de "
                    f"coerência entre cabeçalhos: {description}. Revise-o explicitamente "
                    f"(preencha {status_column}) antes de aplicar."
                )

        proposed = _column_from_number(client, row["client_column_number"])
        proposed_name = _clean_text(row["client_column"])
        if proposed is not None and proposed_name and proposed.display_name != proposed_name:
            raise ValueError(
                f"O relatório de mapeamento não confere com a planilha do cliente na coluna "
                f"{proposed.index + 1}: {_display_name(proposed_name)!r} != "
                f"{_display_name(proposed.display_name)!r}"
            )

        # Propostas em revisão não confirmadas podem apontar de propósito para a
        # mesma coluna de origem. Só os mapeamentos aceitos ou confirmados pelo
        # revisor precisam ser um para um.
        if column is not None and confirmed:
            if column.index in used:
                raise ValueError(
                    f"A coluna do cliente {_display_name(column.display_name)!r} foi mapeada "
                    "mais de uma vez após as correções do revisor"
                )
            used.add(column.index)
        selected.append(column)
    return selected


#: O que uma coluna do modelo vazia significa. Só o estado do meio é trabalho
#: de alguém: "não consta na exportação do cliente" é uma resposta completa e
#: correta, e uma execução que deixa a maior parte do modelo vazia pode estar
#: inteiramente certa.
STATE_DELIVERED = "entregue"
STATE_PENDING = "aguardando decisão"
STATE_ABSENT = "não consta na exportação do cliente"

#: Colunas da aba Cobertura, pelo nome interno. O cabeçalho gravado é o
#: português de ``report_schema.COLUMNS["coverage"]``.
COVERAGE_FIELDS = (
    "state",
    "model_column_number",
    "model_column",
    "excel_column",
    "client_column_number",
    "client_column",
    "final_confidence",
    "reason",
)


def _write_coverage_sheet(
    book,
    *,
    model: SheetTable,
    selected: Sequence[ColumnRef | None],
    statuses: Sequence[str],
    mapping: pd.DataFrame,
) -> None:
    """Índice da situação de cada coluna do modelo, ordenado pelo que falta fazer.

    A aba mapeada responde "o dado entregue está certo?". Ela não responde "o
    que não está aqui e deveria estar", porque toda coluna que ela preenche é
    aceita por construção. Esta aba responde, e é a primeira a ler: cada
    coluna do modelo está entregue, aguardando decisão ou é algo que este
    cliente de fato não envia.

    A justificativa é copiada do relatório como está (a prosa da IA fica em
    inglês).
    """
    sheet = book.create_sheet(report_schema.sheet_name("coverage"))
    headers = [report_schema.header_label("coverage", field) for field in COVERAGE_FIELDS]
    sheet.append(headers)

    order = {STATE_DELIVERED: 0, STATE_PENDING: 1, STATE_ABSENT: 2}
    rows = []
    for index, (model_column, column) in enumerate(zip(model.columns, selected)):
        status = statuses[index] if index < len(statuses) else ""
        if column is not None:
            state = STATE_DELIVERED
        elif status == "review":
            state = STATE_PENDING
        else:
            state = STATE_ABSENT
        proposed = mapping["client_column_number"][index]
        proposed_name = mapping["client_column"][index]
        confidence = mapping["final_confidence"][index]
        rows.append((
            order[state], index + 1,
            [
                state, index + 1, model_column.name,
                get_column_letter(index + 2),
                "" if column is not None else ("" if pd.isna(proposed) else proposed),
                _display_name(
                    column.display_name if column is not None
                    else ("" if pd.isna(proposed_name) else proposed_name)
                ),
                "" if pd.isna(confidence) else confidence,
                "" if pd.isna(mapping["reason"][index]) else mapping["reason"][index],
            ],
        ))
    for _, _, values in sorted(rows, key=lambda item: (item[0], item[1])):
        sheet.append(values)

    sheet.freeze_panes = "A2"
    sheet.auto_filter.ref = f"A1:H{sheet.max_row}"
    for letter, width in (("A", 26), ("B", 10), ("C", 34), ("D", 8),
                          ("E", 10), ("F", 30), ("G", 12), ("H", 78)):
        sheet.column_dimensions[letter].width = width
    header_fill = PatternFill(fill_type="solid", fgColor="E8F0FE")
    for cell in sheet[1]:
        cell.fill = header_fill
        cell.font = Font(bold=True)
    for row in sheet.iter_rows(min_row=2, min_col=7, max_col=7):
        row[0].number_format = "0.00%"


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
    """Aplica os mapeamentos confirmados e grava o banner de metadados seguido dos dados do cliente.

    Lê o relatório no esquema em português e grava o entregável em português
    (abas Dados Mapeados e Cobertura). Por padrão só entram as linhas aceitas
    sem parecer e as confirmadas pelo revisor; com ``accepted_only=False``
    entram também as propostas em revisão não confirmadas, numa planilha que
    não deve ser entregue.
    """
    report = _read_mapping_report(mapping_path)
    metadata = _run_metadata(mapping_path)
    _validate_input_hashes(
        metadata,
        client_path=client_path,
        model_path=model_path,
    )
    model = SheetTable.from_excel(model_path, model_sheet, model_header_row)
    client = SheetTable.from_excel(client_path, client_sheet, client_header_row)
    mapping = _ordered_mapping(report, model)
    selected = _effective_columns(mapping, client, model, accepted_only)

    effective_numbers = [column.index + 1 if column is not None else "" for column in selected]
    effective_names = [column.display_name if column is not None else "" for column in selected]
    metadata_overrides = {
        "client_column_number": effective_numbers,
        "client_column": effective_names,
    }

    metadata_rows: list[list[object]] = []
    for label, mapping_field in MAPPING_METADATA:
        if mapping_field in metadata_overrides:
            values = metadata_overrides[mapping_field]
        else:
            values = ["" if pd.isna(value) else value for value in mapping[mapping_field]]
        # Valores de máquina (situação, método, etapas, trava, catálogo,
        # parecer) e o sufixo de ocorrência saem em português; números e
        # texto livre passam intactos.
        values = [
            report_schema.external_value("banner", mapping_field, value) for value in values
        ]
        metadata_rows.append([label, *values])

    row_count = len(client.data)
    mapped_series = []
    for column in selected:
        if column is None:
            mapped_series.append(pd.Series([pd.NA] * row_count, dtype=object))
        else:
            mapped_series.append(client.series(column).reset_index(drop=True))
    mapped_data = pd.concat(mapped_series, axis=1)
    mapped_data.columns = [column.name for column in model.columns]
    info_header = report_schema.header_label("mapped_data", "__mapping_info__")
    mapped_data.insert(0, info_header, "")

    output_columns = [info_header, *[column.name for column in model.columns]]
    metadata_frame = pd.DataFrame(metadata_rows, columns=output_columns)
    output_frame = pd.concat([metadata_frame, mapped_data], axis=0, ignore_index=True)

    output = Path(output_path)
    if output.suffix.lower() != ".xlsx":
        raise ValueError("O arquivo de saída deve terminar em .xlsx")
    mapped_sheet = report_schema.sheet_name("mapped_data")
    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        output_frame.to_excel(writer, sheet_name=mapped_sheet, index=False)
        worksheet = writer.book[mapped_sheet]
        first_data_excel_row = FIRST_DATA_EXCEL_ROW
        worksheet.freeze_panes = f"B{first_data_excel_row}"

        metadata_fill = PatternFill(fill_type="solid", fgColor="E8F0FE")
        for excel_row in range(2, first_data_excel_row):
            for cell in worksheet[excel_row]:
                cell.fill = metadata_fill

        field_to_excel_row = {
            field: index + 2 for index, (_, field) in enumerate(MAPPING_METADATA)
        }
        for field in CONFIDENCE_ROWS:
            excel_row = field_to_excel_row[field]
            for excel_column in range(2, len(output_columns) + 1):
                worksheet.cell(excel_row, excel_column).number_format = "0.00%"

        # O pandas grava objetos datetime.time como texto. Aqui eles voltam a
        # ser células de hora do Excel, para que os valores mapeados mantenham
        # o tipo e continuem ordenáveis.
        for model_index, column in enumerate(selected):
            if column is None:
                continue
            output_excel_column = model_index + 2
            for row_offset, value in enumerate(client.series(column)):
                if isinstance(value, time):
                    cell = worksheet.cell(
                        first_data_excel_row + row_offset,
                        output_excel_column,
                    )
                    cell.value = value
                    cell.number_format = "hh:mm:ss"

        worksheet.column_dimensions["A"].width = 34
        for excel_column in range(2, min(len(output_columns), 30) + 1):
            worksheet.column_dimensions[get_column_letter(excel_column)].width = 18

        # Um cliente preenche uma minoria do modelo, e isso é o desenho, não uma
        # falha: o modelo é um superconjunto que cobre todo campo que algum
        # cliente possa enviar. Mas isso deixa as colunas entregues espalhadas
        # pelas 227: as 19 de datasul vão de AI a GW, com 101 colunas vazias
        # entre duas delas. Ocultar as colunas sem dado as põe lado a lado.
        # Nada é removido, nenhum valor muda, um clique reexibe, e quem lê o
        # arquivo por programa não é afetado.
        statuses = [
            _clean_text(value).casefold() for value in mapping["status"]
        ]
        delivered_fill = PatternFill(fill_type="solid", fgColor="D7E9DC")
        for model_index, column in enumerate(selected):
            letter = get_column_letter(model_index + 2)
            if column is None:
                worksheet.column_dimensions[letter].hidden = True
            else:
                worksheet.column_dimensions[letter].width = 18
                worksheet.cell(1, model_index + 2).fill = delivered_fill

        _write_coverage_sheet(
            writer.book, model=model, selected=selected, statuses=statuses,
            mapping=mapping,
        )

    print(f"Arquivo gravado: {output}")
    print(
        f"colunas_modelo={len(model.columns)} linhas_cliente={row_count} "
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
        # Arquivo ausente ou ilegível: a mesma mensagem em português do mapeador.
        raise SystemExit(_error_message(exc)) from exc
    except ValueError as exc:
        # As validações do apply já levantam mensagens em português.
        raise SystemExit(str(exc)) from exc
    return 0


if __name__ == "__main__":
    sys.exit(main())
