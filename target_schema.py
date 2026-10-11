"""Define os campos do modelo controlado, sem deduzi-los dos valores de amostra.

O registro mantém a semântica já usada pelo mapeador e explicita os catálogos
dos campos Tipo de Imposto e os identificadores aceitos. Campos sem catálogo
ou identificador declarado não recebem essas evidências por coincidência de
amostras. Cabeçalhos de modelos alternativos continuam usando o fallback do
mapeador quando não constam neste registro.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path

from spreadsheet import normalize_header


@dataclass(frozen=True)
class TargetField:
    number: int
    header: str
    role: str | None
    tax_families: tuple[str, ...]
    tax_qualifiers: tuple[str, ...]
    identifier_kinds: tuple[str, ...]
    catalogs: tuple[str, ...]

    @property
    def tax_scope(self) -> tuple[str, tuple[str, ...]] | None:
        return (self.tax_families[0], self.tax_qualifiers) if len(self.tax_families) == 1 else None


def _load_fields() -> dict[int, TargetField]:
    raw = json.loads(Path(__file__).with_name("target_fields.json").read_text())
    fields = {}
    for number, header, role, families, qualifiers, identifiers, catalogs in raw["fields"]:
        if number in fields:
            raise ValueError(f"Número de campo repetido no modelo: {number}")
        fields[number] = TargetField(number, header, role, tuple(families), tuple(qualifiers),
                                     tuple(identifiers), tuple(catalogs))
    return fields


TARGET_FIELDS = _load_fields()
_FIELDS_BY_HEADER = {field.header: field for field in TARGET_FIELDS.values()}
_FIELDS_BY_NORMALIZED_HEADER: dict[str, list[TargetField]] = {}
for _field in TARGET_FIELDS.values():
    _FIELDS_BY_NORMALIZED_HEADER.setdefault(normalize_header(_field.header), []).append(_field)


def target_field(header: object, *, number: int | None = None) -> TargetField | None:
    """Busca por identidade explícita ou por nome; não escolhe entre nomes ambíguos."""
    if number is not None:
        field = TARGET_FIELDS.get(number)
        return field if field is not None and field.header == header else None
    if isinstance(header, str) and header in _FIELDS_BY_HEADER:
        return _FIELDS_BY_HEADER[header]
    matches = _FIELDS_BY_NORMALIZED_HEADER.get(normalize_header(header), [])
    return matches[0] if len(matches) == 1 else None
