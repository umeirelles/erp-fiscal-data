"""Etapa 6: gerar o relatório de mapeamento (comando ``map``).

Lê o modelo e a exportação do cliente, chama ``map_columns`` e grava o XLSX com as
abas Mapeamentos, Metadados da Execução, Colunas do Cliente sem Uso e Fila de Revisão.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import UTC, datetime
import hashlib
import json
import os
from pathlib import Path
from typing import Sequence

import numpy as np
import pandas as pd
from openai import OpenAI

from mapper import schema
from mapper.catalogs import ReferenceCatalogs, best_catalog_evidence
from mapper.cli import PortugueseArgumentParser, _error_message
from mapper.decide import (
    CEILING_GAP,
    FLOAT_TOLERANCE,
    MapperConfig,
    MappingResult,
    _map_run,
    _start_run,
)
from mapper.evidence import (
    IDENTIFIER_MIN_VALID_FRACTION,
    _catalog_label,
    catalog_evidence_is_distinctive,
    client_column_is_vacuous,
    summarize_values,
)
from mapper.headers import (
    DEFAULT_EMBEDDING_MODEL,
    HeaderScorer,
    LexicalHeaderScorer,
    OpenAIEmbeddingHeaderScorer,
)
from mapper.resolver import DEFAULT_LLM_MODEL, PROMPT_VERSION, DataResolver, OpenAIDataResolver
from mapper.spreadsheet import ColumnRef, SheetTable, sheet_argument as _sheet_argument


#: Versão do formato do relatório; a 3 é o schema em português.
MAPPING_SCHEMA_VERSION = 3

#: Colunas que o revisor preenche na aba de mapeamentos (nomes internos).
REVIEWER_COLUMNS = ["reviewer_status", "correct_client_column", "reviewer_note"]

#: Pasta de catálogos padrão: ``reference_data/`` na raiz do repositório.
REFERENCE_DATA_DIR = Path(__file__).resolve().parents[1] / "reference_data"

# Colunas do Cliente sem Uso
UNMATCHED_CATALOG_THRESHOLD = 1.0  # só os domínios de especificidade 1,0 viram sinal

# Fila de Revisão
_CLAMPED_BAND = 0.0002  # confianças tão próximas são o mesmo teto; a margem desempata
RESOLVER_DISAGREEMENT_GAP = 0.10

#: Coluna detalhe_sinais, unidas por "; ". ``slot``, ``catalog`` e ``kind``
#: chegam como rótulos legíveis; ``distinct_coverage`` e ``fraction``, como
#: porcentagens com vírgula decimal.
SIGNAL_TRIO = "participa como {slot} de um trio comprovado de valor = base x alíquota"
SIGNAL_CATALOG = (
    "{distinct_coverage} dos seus {unique_formatted_count} códigos distintos são "
    "válidos na tabela oficial de {catalog}"
)
SIGNAL_CHECKDIGIT = "{fraction} dos valores passam na validação do dígito verificador de {kind}"
SIGNAL_DATES = "contém datas"
SIGNAL_EMPTY = "Esta coluna não tem valores."
SIGNAL_ALL_ZERO = "Todos os valores desta coluna são zero."
SIGNAL_NO_SIGNAL = (
    "A coluna contém dados, mas a análise não identificou sinais para priorizar "
    "sua revisão. Ela permanece na lista de colunas sem uso."
)

#: Coluna motivo_retencao: as três razões possíveis.
WHY_HELD_BELOW_THRESHOLD = "confiança abaixo do threshold de aceitação"
WHY_HELD_NAME_ONLY = (
    "o cabeçalho sugere uma correspondência, mas falta evidência para aceitação automática"
)
WHY_HELD_VACUOUS = (
    "a coluna de origem está vazia; confirmar a proposta não acrescentaria dados ao modelo"
)
#: Coluna divergencia_ia. ``llm_final`` chega com vírgula decimal (``0,75``).
RESOLVER_DISAGREES = "confiança informada pelo resolver de IA: {llm_final}"


# ---------------------------------------------------------------------------
# Abas auxiliares: colunas do cliente sem uso e fila de revisão
# ---------------------------------------------------------------------------


@dataclass
class UnmatchedColumn:
    """Descreve uma coluna do cliente que não foi usada em um mapeamento aceito.

    Inclui colunas citadas apenas em propostas pendentes de revisão. ``signals``
    contém IDs internos separados por vírgula; ``signal_detail`` contém a
    explicação em português exibida na aba Colunas do Cliente sem Uso.
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
    """Identifica sinais nos dados que ajudam a priorizar uma coluna ainda sem uso.

    Considera trios aritméticos, catálogos, dígitos verificadores e datas, sem
    filtrar pelas palavras do cabeçalho. Retorna os IDs dos sinais e suas
    descrições em português.
    """
    signals: list[str] = []
    details: list[str] = []

    slot = trio_slots.get(column.index)
    if slot is not None:
        signals.append("arithmetic")
        details.append(
            SIGNAL_TRIO.format(slot=schema.TEXT_LABELS["trio_slot"].get(slot, slot))
        )

    if reference_catalogs is not None:
        evidence = best_catalog_evidence(
            [
                reference_catalogs.analyze(values, name)
                for name in reference_catalogs.catalogs
            ]
        )
        if evidence is not None and catalog_evidence_is_distinctive(
            evidence, final_threshold=UNMATCHED_CATALOG_THRESHOLD
        ):
            signals.append("catalog")
            details.append(
                SIGNAL_CATALOG.format(
                    distinct_coverage=schema.format_percent_br(
                        float(evidence["distinct_coverage"])
                    ),
                    unique_formatted_count=int(evidence["unique_formatted_count"]),
                    catalog=_catalog_label(evidence["catalog"]),
                )
            )

    for kind in ("nfe_key", "cnpj", "cpf"):
        fraction = float(profile.get(f"valid_{kind}_fraction", 0.0) or 0.0)
        if fraction >= IDENTIFIER_MIN_VALID_FRACTION:
            signals.append(f"{kind}_checkdigit")
            details.append(
                SIGNAL_CHECKDIGIT.format(
                    fraction=schema.format_percent_br(fraction),
                    kind=schema.TEXT_LABELS["identifier_kind"].get(kind, kind),
                )
            )
            break

    if profile.get("inferred_type") == "datetime":
        signals.append("date")
        details.append(SIGNAL_DATES)

    if not signals:
        quality = profile.get("evidence_quality")
        if quality == "empty":
            details.append(SIGNAL_EMPTY)
        elif quality == "all_zero":
            details.append(SIGNAL_ALL_ZERO)
        else:
            details.append(SIGNAL_NO_SIGNAL)
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
    client_profiles: dict[int, dict[str, object]] | None = None,
) -> list[UnmatchedColumn]:
    """Lista todas as colunas sem uso, colocando as prioritárias primeiro.

    Colunas sem sinais relevantes continuam na lista, com prioridade menor.
    No modo offline, as propostas da segunda passagem ficam em revisão, o que
    pode aumentar a quantidade de colunas na lista prioritária.
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
        profile = (client_profiles or {}).get(index)
        if profile is None:
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
        # Uma proposta não precisa de sinal: o mapeador já produziu evidência para ela.
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


@dataclass
class AdjudicationRow:
    """Representa uma proposta pendente na aba Fila de Revisão.

    A fila organiza a consulta por coluna do modelo. O parecer deve ser
    preenchido na aba Mapeamentos, que o apply usa ao gerar a saída.
    ``why_held`` e ``resolver_disagrees`` já contêm texto em português.
    """

    rank: int
    model_column_number: int
    model_column: str
    proposed_client_column_number: int | None
    proposed_client_column: str
    final_confidence: float
    gate_distance: float
    name_margin: float
    rows_with_value: int
    resolver_disagrees: str
    arithmetic_proof: str
    client_sample_values: str
    why_held: str


def summarize_adjudication_queue(
    *,
    client_table: SheetTable,
    results: Sequence[MappingResult],
    final_threshold: float,
    sample_size: int,
    client_profiles: dict[int, dict[str, object]] | None = None,
) -> list[AdjudicationRow]:
    """Ordena as propostas em revisão pela proximidade dos critérios de aceitação."""
    rows: list[AdjudicationRow] = []
    for result in results:
        if result.status == "accepted" or result.client_column_number is None:
            continue
        index = result.client_column_number - 1
        if index < 0 or index >= len(client_table.columns):
            continue
        column = client_table.columns[index]
        profile = (client_profiles or {}).get(index)
        if profile is None:
            profile = summarize_values(client_table.series(column), sample_size)
        final = float(result.final_confidence or 0.0)
        if final <= 0.0:
            continue
        # Confirmar uma proposta sobre uma coluna vazia entregaria uma coluna
        # em branco; ela fica na fila, mas no fim.
        vacuous = client_column_is_vacuous(client_table, column)
        llm_final = result.llm_final_confidence
        disagrees = (
            RESOLVER_DISAGREES.format(llm_final=schema.format_decimal_br(llm_final))
            if llm_final is not None and float(llm_final) < final - RESOLVER_DISAGREEMENT_GAP
            else ""
        )
        # Scores limitados a 0,8999 não permitem comparar a força das propostas entre si.
        clamped = abs(final - (final_threshold - CEILING_GAP)) < FLOAT_TOLERANCE
        rows.append(
            AdjudicationRow(
                rank=0,
                model_column_number=result.model_column_number,
                model_column=result.model_column,
                proposed_client_column_number=result.client_column_number,
                proposed_client_column=result.client_column or "",
                final_confidence=round(final, 4),
                gate_distance=round(final_threshold - final, 4),
                name_margin=round(float(result.name_margin or 0.0), 4),
                rows_with_value=int(profile["non_empty_count"]),
                resolver_disagrees=disagrees,
                arithmetic_proof=result.arithmetic_proof or "",
                client_sample_values=" | ".join(
                    str(value) for value in profile["samples"][:6]
                ),
                why_held=(
                    WHY_HELD_VACUOUS
                    if vacuous
                    else WHY_HELD_NAME_ONLY
                    if clamped
                    else WHY_HELD_BELOW_THRESHOLD
                ),
            )
        )
    # Colunas preenchidas primeiro, depois a confiança; a margem só desempata
    # dentro de uma faixa de confiança (na prática, o teto).
    rows.sort(
        key=lambda row: (
            row.rows_with_value > 0,
            round(row.final_confidence / _CLAMPED_BAND),
            row.name_margin,
            row.rows_with_value,
        ),
        reverse=True,
    )
    for position, row in enumerate(rows, 1):
        row.rank = position
    return rows


# ---------------------------------------------------------------------------
# Gravação do relatório
# ---------------------------------------------------------------------------


def file_sha256(path: str | Path) -> str:
    """Calcula o SHA-256 do arquivo lendo blocos de 1 MiB."""
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _external_rows(
    rows: Sequence[UnmatchedColumn] | Sequence[AdjudicationRow],
    row_type: type,
    table: str,
) -> pd.DataFrame:
    """Converte as linhas auxiliares para o relatório; se a lista estiver vazia, preserva os cabeçalhos."""
    frame = pd.DataFrame(
        [asdict(row) for row in rows]
        or [{field: "" for field in row_type.__annotations__}]
    )
    return schema.to_external_frame(frame, table)


def write_results(
    results: Sequence[MappingResult],
    output_path: str | Path,
    metadata: dict[str, object] | None = None,
    unmatched: Sequence[UnmatchedColumn] | None = None,
    queue: Sequence[AdjudicationRow] | None = None,
) -> None:
    """Grava o relatório em XLSX ou CSV, usando o schema em português.

    ``schema`` converte os nomes e valores estruturados. As mensagens
    já chegam no idioma em que devem ser gravadas. O CSV contém apenas
    a tabela Mapeamentos.
    """
    output = Path(output_path)
    frame = pd.DataFrame([asdict(result) for result in results])
    for column in REVIEWER_COLUMNS:
        frame[column] = ""
    frame = schema.to_external_frame(frame, "mappings")
    if output.suffix.lower() == ".csv":
        frame.to_csv(output, index=False)
    elif output.suffix.lower() == ".xlsx":
        with pd.ExcelWriter(output, engine="openpyxl") as writer:
            frame.to_excel(writer, sheet_name=schema.sheet_name("mappings"), index=False)
            if metadata:
                schema.to_external_metadata(metadata).to_excel(
                    writer, sheet_name=schema.sheet_name("run_metadata"), index=False
                )
            # Mantém Mapeamentos na posição 0, usada pelo apply. As abas auxiliares
            # são gravadas mesmo sem registros, indicando que a análise foi
            # feita e não encontrou itens para listar.
            if unmatched is not None:
                _external_rows(unmatched, UnmatchedColumn, "unmatched").to_excel(
                    writer, sheet_name=schema.sheet_name("unmatched"), index=False
                )
            if queue is not None:
                _external_rows(queue, AdjudicationRow, "queue").to_excel(
                    writer, sheet_name=schema.sheet_name("queue"), index=False
                )
            # Mantém os scores numéricos; o Excel os apresenta como porcentagem.
            for sheet in writer.sheets.values():
                for header in sheet[1]:
                    if str(header.value).startswith("confianca_"):
                        for row in sheet.iter_rows(min_row=2, min_col=header.column,
                                                   max_col=header.column):
                            row[0].number_format = "0.00%"
    else:
        raise ValueError("A saída deve terminar em .xlsx ou .csv")


# ---------------------------------------------------------------------------
# Comando map (python run.py map)
# ---------------------------------------------------------------------------


def build_parser() -> PortugueseArgumentParser:
    """Configura os argumentos e a ajuda de ``run.py map``."""
    parser = PortugueseArgumentParser(
        prog="run.py map",
        description=(
            "Identifica as colunas do cliente correspondentes aos campos de model.xlsx "
            "e gera um relatório para revisão e aplicação do mapeamento."
        )
    )
    parser.add_argument(
        "client", metavar="EXPORTACAO", help="exportação .xlsx do cliente"
    )
    parser.add_argument(
        "--model",
        default="data/model.xlsx",
        metavar="ARQUIVO",
        help="planilha do modelo base (padrão: %(default)s)",
    )
    parser.add_argument(
        "--output",
        metavar="ARQUIVO",
        help=(
            "caminho do relatório de saída, em .xlsx ou .csv "
            "(padrão: mapping_<nome do arquivo do cliente>.xlsx)"
        ),
    )
    parser.add_argument(
        "--model-sheet",
        default="0",
        metavar="ABA",
        help="índice (a partir de 0) ou nome da aba do modelo (padrão: %(default)s)",
    )
    parser.add_argument(
        "--client-sheet",
        default="0",
        metavar="ABA",
        help="índice (a partir de 0) ou nome da aba do cliente (padrão: %(default)s)",
    )
    parser.add_argument(
        "--model-header-row",
        type=int,
        default=1,
        metavar="LINHA",
        help="linha do cabeçalho na aba do modelo, contada a partir de 1 (padrão: %(default)s)",
    )
    parser.add_argument(
        "--client-header-row",
        type=int,
        default=1,
        metavar="LINHA",
        help="linha do cabeçalho na aba do cliente, contada a partir de 1 (padrão: %(default)s)",
    )
    parser.add_argument(
        "--name-threshold",
        type=float,
        default=MapperConfig.name_threshold,
        metavar="VALOR",
        help=(
            "score mínimo do cabeçalho para aceitação na primeira passagem, "
            "de 0 a 1 (padrão: %(default)s)"
        ),
    )
    parser.add_argument(
        "--name-margin",
        type=float,
        default=MapperConfig.name_margin_threshold,
        metavar="VALOR",
        help=(
            "margem mínima entre os scores das duas melhores candidatas pelo "
            "cabeçalho, de 0 a 1 (padrão: %(default)s)"
        ),
    )
    parser.add_argument(
        "--final-threshold",
        type=float,
        default=MapperConfig.final_threshold,
        metavar="VALOR",
        help="threshold da confiança final para aceitação, de 0 a 1 (padrão: %(default)s)",
    )
    parser.add_argument(
        "--candidates",
        type=int,
        default=MapperConfig.candidate_count,
        metavar="N",
        help=(
            "quantidade de candidatos por critério na segunda passagem "
            "(padrão: %(default)s)"
        ),
    )
    parser.add_argument(
        "--sample-size",
        type=int,
        default=MapperConfig.sample_size,
        metavar="N",
        help=(
            "quantidade de valores de amostra por coluna em cada perfil "
            "(padrão: %(default)s)"
        ),
    )
    parser.add_argument(
        "--embedding-model",
        default=os.getenv("OPENAI_EMBEDDING_MODEL", DEFAULT_EMBEDDING_MODEL),
        metavar="MODELO",
        help=(
            "modelo de embeddings da OpenAI usado para comparar os cabeçalhos "
            "(padrão: %(default)s; a variável OPENAI_EMBEDDING_MODEL, se definida, "
            "substitui o padrão)"
        ),
    )
    parser.add_argument(
        "--llm-model",
        default=os.getenv("OPENAI_MODEL", DEFAULT_LLM_MODEL),
        metavar="MODELO",
        help=(
            "modelo de linguagem da OpenAI usado pelo resolver "
            "(padrão: %(default)s; a variável OPENAI_MODEL, se definida, substitui o "
            "padrão)"
        ),
    )
    parser.add_argument(
        "--reference-data",
        default=str(REFERENCE_DATA_DIR),
        metavar="DIRETORIO",
        help=(
            "diretório com o manifest.json e os catálogos versionados de códigos "
            "fiscais (padrão: a pasta reference_data ao lado deste programa)"
        ),
    )
    parser.add_argument(
        "--no-reference-data",
        action="store_true",
        help="desativa a evidência dos catálogos oficiais de códigos fiscais",
    )
    parser.add_argument(
        "--offline",
        action="store_true",
        help=(
            "compara os cabeçalhos pelo texto, sem chamadas à OpenAI; "
            "as propostas da segunda passagem seguem para revisão"
        ),
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Ponto de entrada da CLI; devolve 0 em caso de sucesso.

    Erros de valor e de arquivo saem com uma mensagem em português e código 1,
    sem traceback.
    """
    args = build_parser().parse_args(argv)
    if not 0.0 <= args.name_threshold <= 1.0:
        raise SystemExit("--name-threshold deve estar entre 0 e 1")
    if not 0.0 <= args.name_margin <= 1.0:
        raise SystemExit("--name-margin deve estar entre 0 e 1")
    if not 0.0 <= args.final_threshold <= 1.0:
        raise SystemExit("--final-threshold deve estar entre 0 e 1")
    if args.candidates < 1 or args.sample_size < 1:
        raise SystemExit("--candidates e --sample-size devem ser positivos")
    if args.model_header_row < 1 or args.client_header_row < 1:
        raise SystemExit("--model-header-row e --client-header-row devem ser positivos")
    try:
        return _run(args)
    except (ValueError, OSError) as exc:
        raise SystemExit(_error_message(exc)) from exc


def _run(args) -> int:
    """Executa o mapeamento com os argumentos já validados e grava o relatório."""
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
                f"Não foi possível carregar os catálogos de referência de "
                f"{args.reference_data}: {exc}. Execute python run.py update-catalogs ou "
                "use --no-reference-data."
            ) from exc

    if args.offline:
        header_scorer: HeaderScorer = LexicalHeaderScorer()
        data_resolver: DataResolver | None = None
    else:
        if not os.getenv("OPENAI_API_KEY"):
            raise SystemExit(
                "OPENAI_API_KEY não está definida. Exporte uma chave válida ou use --offline."
            )
        openai_client = OpenAI()
        header_scorer = OpenAIEmbeddingHeaderScorer(
            openai_client,
            model=args.embedding_model,
        )
        data_resolver = OpenAIDataResolver(openai_client, model=args.llm_model)

    run = _start_run(
        model_table=model_table,
        client_table=client_table,
        header_scorer=header_scorer,
        config=MapperConfig(
            name_threshold=args.name_threshold,
            name_margin_threshold=args.name_margin,
            final_threshold=args.final_threshold,
            candidate_count=args.candidates,
            sample_size=args.sample_size,
        ),
        reference_catalogs=reference_catalogs,
        online=data_resolver is not None,
    )
    results = _map_run(run, data_resolver)
    manifest_path = Path(args.reference_data) / "manifest.json"
    # Chaves e sentinelas internos (offline, disabled); write_results os grava em português.
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
    # A sugestão da coluna mais próxima usa comparação textual para evitar
    # novas chamadas à API de embeddings apenas para esse dado auxiliar.
    unmatched = summarize_unmatched_columns(
        model_table=model_table,
        client_table=client_table,
        results={index: result for index, result in enumerate(results)},
        similarities=LexicalHeaderScorer().scores(
            [column.name for column in model_table.columns],
            [column.name for column in client_table.columns],
        ),
        reference_catalogs=reference_catalogs,
        client_tax_trios=run.client_tax_trios,
        sample_size=args.sample_size,
        client_profiles=run.client_profiles,
    )
    queue = summarize_adjudication_queue(
        client_table=client_table,
        results=results,
        final_threshold=args.final_threshold,
        sample_size=args.sample_size,
        client_profiles=run.client_profiles,
    )
    write_results(
        results, output_path, metadata=metadata, unmatched=unmatched, queue=queue
    )

    accepted = sum(result.status == "accepted" for result in results)
    review = sum(result.status == "review" for result in results)
    no_match = sum(result.status == "no_match" for result in results)
    print(f"Arquivo gravado: {output_path}")
    print(f"aceito={accepted} revisao={review} sem_correspondencia={no_match}")
    return 0
