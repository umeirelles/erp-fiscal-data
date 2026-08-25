"""Load the client-authored de-para answer keys.

Only datasul and oracle are keys. de_para_procosa.xlsx is a
record-granularity spec authored against a different export, and there is no
key for sap_terumo.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import openpyxl

from mapeval.excel import parse_coluna_annotation

# Four rows where the key itself is wrong and the mapper is right. Each was
# verified from the client's own data, and each is re-derivable from the
# row-aligned de-para data channel rather than taken on faith.
#
# Applying these is the difference between 33/56 and 37/56. A harness that
# skips them punishes correct behaviour, so --strict-key-only exists purely
# to prove they are the only thing separating the two figures.
KEY_CORRECTIONS: dict[str, dict[int, tuple[int, str]]] = {
    "oracle": {
        213: (36, "key names c37 ALIQ COFINS; c36 ALIQ PIS is the PIS rate"),
        220: (40, "key names c39 CST-PIS; the pair is transposed in the key"),
        221: (39, "key names c40 CST-COFINS; the pair is transposed in the key"),
    },
    "datasul": {
        184: (188, "key names c99 (block A) but is internally incoherent; "
                   "c188 is trio-consistent with the key's own m52-m55"),
    },
}


@dataclass(frozen=True)
class AnswerKey:
    """The columns a client actually has, per the client's own annotation."""

    client: str
    #: model column number (1-based) -> client column number (1-based)
    present: dict[int, int]
    #: model column number -> why the client's own annotation was overridden
    corrections: dict[int, str] = field(default_factory=dict)

    @property
    def denominator(self) -> int:
        """The only legitimate recall denominator. Never 227."""
        return len(self.present)

    def __len__(self) -> int:
        return len(self.present)


def load_answer_key(
    path: str | Path,
    client: str,
    *,
    apply_corrections: bool = True,
) -> AnswerKey:
    """Read row 1 of a de-para workbook into a model->client column map."""
    workbook = openpyxl.load_workbook(path, read_only=True, data_only=True)
    try:
        sheet = workbook[workbook.sheetnames[0]]
        header_row = next(sheet.iter_rows(min_row=1, max_row=1, values_only=True))
    finally:
        workbook.close()

    present: dict[int, int] = {}
    for offset, cell in enumerate(header_row, start=1):
        client_column = parse_coluna_annotation(cell)
        if client_column is not None:
            present[offset] = client_column

    corrections: dict[int, str] = {}
    if apply_corrections:
        for model_column, (correct_column, why) in KEY_CORRECTIONS.get(client, {}).items():
            if model_column in present:
                present[model_column] = correct_column
                corrections[model_column] = why

    return AnswerKey(client=client, present=present, corrections=corrections)
