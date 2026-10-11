"""Combina a proposta do mapeador e o parecer humano em uma decisão efetiva.

As funções recebem os nomes internos do relatório. O parecer deve ser
normalizado com ``normalize_review`` na leitura, antes de resolver a decisão.
"""

from __future__ import annotations

from dataclasses import dataclass
import math

import pandas as pd

import report_schema
from spreadsheet import ColumnRef, SheetTable


def _clean_text(value: object) -> str:
    """Remove espaços nas extremidades do texto; retorna ``""`` para células vazias."""
    return "" if pd.isna(value) else str(value).strip()


def _column_label(field: str) -> str:
    """Retorna o cabeçalho usado para o campo na aba Mapeamentos."""
    return report_schema.header_label("mappings", field)


def _status_label(status: str) -> str:
    """Converte o parecer interno para o relatório: ``incorrect`` vira ``incorreto``."""
    return report_schema.ENUMS["reviewer_status"][status]


def _display_name(text: object) -> object:
    """Converte o nome de exibição para o relatório, incluindo o sufixo ``(ocorrência N)``."""
    return report_schema.display_name_to_external(text)


def normalize_review(row: dict) -> str:
    """Converte o parecer para o valor interno e rejeita valores desconhecidos."""
    status = report_schema.normalize_reviewer_status(row["reviewer_status"])
    if status is None:
        raise ValueError(
            f"Valor desconhecido em {_column_label('reviewer_status')}: "
            f"{row['reviewer_status']!r}, na coluna do modelo {row['model_column']!r}. Use "
            f"{_status_label('correct')}, {_status_label('incorrect')} ou "
            f"{_status_label('no_mapping_exists')}, ou deixe em branco."
        )
    return status


def _column_from_number(client: SheetTable, value: object) -> ColumnRef | None:
    """Busca a coluna do cliente pelo número, a partir de 1; retorna ``None`` para células vazias."""
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
    """Localiza a origem indicada pelo revisor em ``coluna_cliente_correta``.

    Aceita o número da coluna a partir de 1 ou seu nome de exibição, sem
    distinguir maiúsculas. Cabeçalhos repetidos podem ser identificados pelos
    sufixos ``(ocorrência N)`` ou ``(ocorrencia N)``. Um texto numérico é
    interpretado como número de coluna, mesmo que exista um cabeçalho com esse texto.
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
    """Exige o parecer ``incorreto`` quando há uma coluna corrigida.

    Com outro parecer, a correção seria ignorada. No caso de ``correto``, isso
    entregaria a coluna proposta pelo mapeador em vez da indicada pelo revisor.
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


@dataclass(frozen=True)
class EffectiveMapping:
    """Guarda a origem proposta ou confirmada e a situação após a revisão."""

    column: ColumnRef | None
    status: str

    @property
    def delivered(self) -> bool:
        return self.status == "accepted" and self.column is not None


def effective_status(row: dict) -> str:
    """Aplica a precedência do parecer humano sobre a situação do mapeador."""
    reviewer_status = row["reviewer_status"]
    if reviewer_status == "no_mapping_exists":
        return "no_match"
    if reviewer_status in {"correct", "incorrect"}:
        return "accepted"
    return row["status"]


def resolve_mapping(row: dict, client: SheetTable) -> EffectiveMapping:
    """Valida a correção e resolve a origem; mantém propostas pendentes para avaliação."""
    _refuse_unused_correction(row)
    status = effective_status(row)
    reviewer_status = row["reviewer_status"]
    if reviewer_status == "no_mapping_exists":
        return EffectiveMapping(None, status)
    if reviewer_status == "incorrect":
        return EffectiveMapping(_corrected_column(client, row["correct_client_column"]), status)
    proposal = _column_from_number(client, row["client_column_number"])
    if reviewer_status == "correct" and proposal is None:
        raise ValueError(
            f"{_column_label('reviewer_status')} é {_status_label('correct')!r} na coluna "
            f"do modelo {row['model_column']!r}, mas "
            f"{_column_label('client_column_number')} está em branco. Use "
            f"{_status_label('no_mapping_exists')!r} para confirmar que o cliente não "
            f"tem este campo, ou {_status_label('incorrect')!r} com "
            f"{_column_label('correct_client_column')}."
        )
    return EffectiveMapping(proposal, status)
