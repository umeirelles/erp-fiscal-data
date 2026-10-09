"""Tradução das frases dos relatórios legados em inglês para o português do mapeador atual.

Os relatórios gravados antes da tradução (esquema legado em inglês, versão 2)
trazem em inglês as frases que o próprio mapeador escreve: a justificativa, a
prova aritmética, o detalhe dos sinais, o motivo de retenção e a divergência
da IA. Este módulo reconhece cada frase pelo modelo inglês que a produziu e a
reescreve com a constante em português que o mapeador usa hoje
(``invoice_mapper.REASON_*``, ``PROOF_TEXT``, ``PROOF_NOT_DECISIVE_SUFFIX``,
``SIGNAL_*``, ``WHY_HELD_*``, ``RESOLVER_DISAGREES`` e
``HEADER_CONFLICT_*_PT``). Os parâmetros recebem a mesma preparação que o
mapeador faz: números com vírgula decimal (``report_schema.format_percent_br``
e ``format_decimal_br``), rótulos de ``report_schema.TEXT_LABELS``, sigla do
tributo em maiúsculas e nome de exibição citado em português
(``report_schema.display_name_to_external``). Assim a frase migrada é
exatamente a que o mapeador atual gravaria para os mesmos valores.

Os modelos ingleses foram copiados de ``invoice_mapper.py`` no commit
``2ae4b6d``, o que gravou os relatórios arquivados; cada um cita as linhas de
origem nesse commit. Cada modelo vira uma expressão regular de casamento
completo: a frase inteira precisa casar, senão nada muda.

O que não casa com nenhum modelo passa intacto: a prosa escrita pela IA (o
prompt continua em inglês, então a IA continua respondendo em inglês), as
observações do revisor e o texto que já está em português. Por isso traduzir
duas vezes dá o mesmo resultado.

Usado só por ``migrate_reports.py`` e pelos testes. O ``apply`` não traduz as
frases de um relatório legado: ele grava a planilha em português, copia as
frases como estão e recomenda a migração.
"""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass, field
from typing import Callable, Mapping

import invoice_mapper as im
import report_schema

#: Campos de frase do mapeador em cada tabela, com o nome interno (igual ao
#: cabeçalho legado). Só esses campos são traduzidos; ``reviewer_note`` (texto
#: do revisor), ``model_column`` e os valores de amostra do cliente nunca.
TEXT_FIELDS: dict[str, frozenset[str]] = {
    "mappings": frozenset({"reason", "arithmetic_proof"}),
    "unmatched": frozenset({"signal_detail"}),
    "queue": frozenset({"resolver_disagrees", "arithmetic_proof", "why_held"}),
    # A Cobertura da planilha mapeada copia a justificativa do relatório.
    "coverage": frozenset({"reason"}),
}

#: Tipos de frase, que são os campos de origem. ``signal`` é uma frase só do
#: detalhe dos sinais; ``signal_detail`` é a célula, com as frases unidas por
#: ``"; "``.
KINDS: tuple[str, ...] = (
    "reason",
    "arithmetic_proof",
    "signal_detail",
    "why_held",
    "resolver_disagrees",
)

#: Separador das frases do detalhe dos sinais, nas duas línguas.
SIGNAL_SEPARATOR = "; "


# ---------------------------------------------------------------------------
# Parâmetros: o que cada um casa no inglês e como vira português
# ---------------------------------------------------------------------------

#: ``f"{x:.1%}"``: dígitos, ponto, um dígito e o sinal de porcentagem.
_PERCENT = r"[0-9]+\.[0-9]%"
#: ``int``.
_INTEGER = r"[0-9]+"
#: ``str(float)`` (ou ``str(int)``), inclusive a notação científica.
_DECIMAL = r"-?[0-9]+(?:\.[0-9]+)?(?:e[-+]?[0-9]+)?"
#: Ids internos: catálogo, família de tributo, posição no trio, tipo de DV.
_IDENTIFIER = r"\w+"
#: ``repr`` de um texto do Python, entre aspas simples ou duplas.
_PYTHON_STR_REPR = r"'(?:[^'\\\n]|\\.)*'|\"(?:[^\"\\\n]|\\.)*\""
#: O conflito de cabeçalho em inglês, como ``semantic_header_conflict`` o escreve.
_HEADER_CONFLICT = r"(?:tax-family|tax-qualifier|semantic-role) conflict \([^()]*\)"
#: Nome de classe de exceção (``type(exc).__name__``).
_EXCEPTION_NAME = r"[A-Za-z_][A-Za-z0-9_]*"
#: Mensagem livre de exceção, até o fim do texto.
_ANYTHING = r".*"


def _percent_br(text: str) -> str:
    """``"88.2%"`` -> ``"88,2%"``, pelo mesmo ``format_percent_br`` do mapeador.

    O número é lido e formatado de novo pela função do mapeador; a conferência
    garante que os dígitos são os mesmos da frase inglesa, só com a vírgula.
    """
    rendered = report_schema.format_percent_br(float(text[:-1]) / 100)
    expected = text.replace(".", ",")
    if rendered != expected:
        raise ValueError(
            f"A porcentagem {text!r} não pôde ser reescrita com os mesmos dígitos "
            f"(obtido {rendered!r})."
        )
    return rendered


def _decimal_br(text: str) -> str:
    """``"0.9996"`` -> ``"0,9996"``, pelo mesmo ``format_decimal_br`` do mapeador.

    O inglês trazia ``str`` do número; ``format_decimal_br`` sem casas fixas
    usa a mesma representação, então os dígitos não mudam.
    """
    value: float | int = int(text) if re.fullmatch(r"-?[0-9]+", text) else float(text)
    rendered = report_schema.format_decimal_br(value)
    expected = text.replace(".", ",")
    if rendered != expected:
        raise ValueError(
            f"O número {text!r} não pôde ser reescrito com os mesmos dígitos "
            f"(obtido {rendered!r})."
        )
    return rendered


# ---------------------------------------------------------------------------
# Conflito de cabeçalho
# ---------------------------------------------------------------------------

_CONFLICT_PARTS = re.compile(
    r"(?P<kind>tax-family|tax-qualifier|semantic-role) conflict "
    r"\((?P<left>[^()]*?) vs (?P<right>[^()]*)\)"
)


def translate_header_conflict(text: str) -> str | None:
    """Conflito de cabeçalho em inglês -> o texto de ``describe_header_conflict_pt``.

    O inglês é o de ``semantic_header_conflict`` (que continua em inglês em
    memória, porque vai para a IA): ``tax-family conflict (icms vs pis, cofins)``,
    ``tax-qualifier conflict (st vs regular)`` ou ``semantic-role conflict (base
    vs rate)``. Os lados recebem a mesma preparação de
    ``describe_header_conflict_pt``: famílias em maiúsculas, qualificadores e
    papéis pelos rótulos de ``report_schema.TEXT_LABELS``. Devolve ``None`` se
    o texto não é um conflito.
    """
    match = _CONFLICT_PARTS.fullmatch(text)
    if match is None:
        return None
    kind = match["kind"]
    if kind == "tax-family":
        template = im.HEADER_CONFLICT_FAMILY_PT

        def label(value: str) -> str:
            return value.upper()

    else:
        if kind == "tax-qualifier":
            template = im.HEADER_CONFLICT_QUALIFIER_PT
            labels = report_schema.TEXT_LABELS["tax_qualifier"]
        else:
            template = im.HEADER_CONFLICT_ROLE_PT
            labels = report_schema.TEXT_LABELS["header_role"]

        def label(value: str) -> str:
            return labels.get(value, value)

    def side(values: str) -> str:
        return ", ".join(label(value) for value in values.split(", "))

    return template.format(left=side(match["left"]), right=side(match["right"]))


def _header_conflict_pt(text: str) -> str:
    translated = translate_header_conflict(text)
    if translated is None:  # o padrão do parâmetro garante o casamento
        raise ValueError(f"Conflito de cabeçalho não reconhecido: {text!r}")
    return translated


def _quoted_display_name_pt(text: str) -> str:
    """``repr`` do nome de exibição interno -> o nome de exibição em português.

    O inglês citava ``chosen.display_name`` com ``!r``; o português cita
    ``display_name_to_external`` dele, também com ``!r`` (no próprio modelo).
    """
    name = ast.literal_eval(text)
    if not isinstance(name, str):
        raise ValueError(f"Nome de coluna citado inválido: {text!r}")
    return report_schema.display_name_to_external(name)


# ---------------------------------------------------------------------------
# Modelos de frase
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class LegacyTemplate:
    """Um modelo de frase inglês legado e a frase em português correspondente.

    ``english`` é o texto que o código de 2ae4b6d gravava, com ``{parametro}``
    no lugar de cada valor; ``params`` dá a expressão regular de cada
    parâmetro; ``render`` recebe os parâmetros casados (texto) e devolve a
    frase em português, já com a preparação dos valores. ``source`` cita as
    linhas de ``invoice_mapper.py`` no commit 2ae4b6d.
    """

    id: str
    kind: str
    english: str
    render: Callable[[Mapping[str, str]], str]
    source: str
    params: Mapping[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "_regex", _compile(self.english, self.params))

    @property
    def regex(self) -> re.Pattern[str]:
        """Expressão regular de casamento completo do modelo inglês."""
        return self._regex  # type: ignore[attr-defined]

    def translate(self, text: str) -> str | None:
        """A frase em português, ou ``None`` se o texto não é deste modelo."""
        match = self.regex.fullmatch(text)
        if match is None:
            return None
        return self.render(match.groupdict())


_PLACEHOLDER = re.compile(r"\{(\w+)\}")


def _compile(english: str, params: Mapping[str, str]) -> re.Pattern[str]:
    """Modelo com ``{parametro}`` -> regex: texto literal escapado e grupos nomeados.

    Um parâmetro repetido (``{tax_scope}`` na prova não decisiva) precisa ter
    o mesmo valor nas duas posições.
    """
    parts: list[str] = []
    seen: set[str] = set()
    position = 0
    for match in _PLACEHOLDER.finditer(english):
        parts.append(re.escape(english[position : match.start()]))
        name = match[1]
        if name not in params:
            raise ValueError(f"Parâmetro sem padrão no modelo {english!r}: {name}")
        if name in seen:
            parts.append(f"(?P={name})")
        else:
            parts.append(f"(?P<{name}>{params[name]})")
            seen.add(name)
        position = match.end()
    parts.append(re.escape(english[position:]))
    missing = set(params) - seen
    if missing:
        raise ValueError(f"Padrões sem parâmetro no modelo {english!r}: {sorted(missing)}")
    return re.compile("".join(parts), re.DOTALL)


def _fixed(
    template_id: str, kind: str, english: str, portuguese: str, source: str
) -> LegacyTemplate:
    """Frase fixa, sem parâmetros."""
    return LegacyTemplate(
        id=template_id,
        kind=kind,
        english=english,
        render=lambda _params, text=portuguese: text,
        source=source,
    )


def _render_proof(params: Mapping[str, str]) -> str:
    # Mesma preparação de invoice_mapper._trio_proof_text.
    tax_scope = params["tax_scope"].upper()
    text = im.PROOF_TEXT.format(
        tax_scope=tax_scope,
        rows_tested=int(params["rows_tested"]),
        fit_score=_decimal_br(params["fit_score"]),
    )
    if params.get("alternatives") is not None:
        text += im.PROOF_NOT_DECISIVE_SUFFIX.format(
            alternatives=int(params["alternatives"]), tax_scope=tax_scope
        )
    return text


_PROOF_EN = (
    "valor = base x aliquota holds for {tax_scope} on {rows_tested} rows at fit {fit_score}"
)
_PROOF_PARAMS = {"tax_scope": _IDENTIFIER, "rows_tested": _INTEGER, "fit_score": _DECIMAL}


#: Todos os modelos ingleses legados, por tipo de frase.
TEMPLATES: tuple[LegacyTemplate, ...] = (
    # --- justificativa (reason) ------------------------------------------
    _fixed(
        "reason_no_unused",
        "reason",
        "No unused client columns remain.",
        im.REASON_NO_UNUSED,
        "invoice_mapper.py:2287 e :2427",
    ),
    _fixed(
        "reason_pass_one_accepted",
        "reason",
        "Accepted from header similarity with a clear lead over the runner-up.",
        im.REASON_PASS_ONE_ACCEPTED,
        "invoice_mapper.py:2318",
    ),
    _fixed(
        "reason_offline",
        "reason",
        "Header score or runner-up margin was insufficient; data comparison was disabled.",
        im.REASON_OFFLINE,
        "invoice_mapper.py:2458",
    ),
    LegacyTemplate(
        id="reason_data_error",
        kind="reason",
        english="Data comparison failed: {exc_type}: {exc}",
        params={"exc_type": _EXCEPTION_NAME, "exc": _ANYTHING},
        # A mensagem da exceção passa como está, como o mapeador atual faz.
        render=lambda p: im.REASON_DATA_ERROR.format(exc_type=p["exc_type"], exc=p["exc"]),
        source="invoice_mapper.py:2505",
    ),
    _fixed(
        "reason_unique_relationship",
        "reason",
        "A unique high-coverage fiscal relationship supports this column "
        "over alternatives (for example, base times rate reproduces value).",
        im.REASON_UNIQUE_RELATIONSHIP,
        "invoice_mapper.py:2569-2572",
    ),
    _fixed(
        "reason_overlap",
        "reason",
        "Distinctive non-zero sample values overlap exactly with this "
        "candidate and not with the competing columns.",
        im.REASON_OVERLAP,
        "invoice_mapper.py:2635-2638",
    ),
    _fixed(
        "reason_joint",
        "reason",
        "The header and a row-wise fiscal relationship jointly "
        "support this column with a clear lead over alternatives.",
        im.REASON_JOINT,
        "invoice_mapper.py:2694-2697",
    ),
    _fixed(
        "reason_null_fallback",
        "reason",
        "The resolver found supporting evidence but did not commit to a column; "
        "retain the strongest header candidate for review.",
        im.REASON_NULL_FALLBACK,
        "invoice_mapper.py:2730-2733",
    ),
    _fixed(
        "reason_preserve_best",
        "reason",
        "The strongest header candidate has weak row evidence; preserve the "
        "name-based mapping for review instead of replacing it with a merely "
        "similar value shape.",
        im.REASON_PRESERVE_BEST,
        "invoice_mapper.py:2764-2768",
    ),
    _fixed(
        "reason_unsupported_no_match",
        "reason",
        "No candidate had enough combined header and distinctive row evidence; "
        "the client export may not contain this model field.",
        im.REASON_UNSUPPORTED_NO_MATCH,
        "invoice_mapper.py:2938-2941",
    ),
    LegacyTemplate(
        id="reason_conflict_review",
        kind="reason",
        english=(
            "Explicit {header_conflict}; keep this proposal for review because the "
            "row content provides distinctive evidence that the client header may be bogus."
        ),
        params={"header_conflict": _HEADER_CONFLICT},
        render=lambda p: im.REASON_CONFLICT_REVIEW.format(
            header_conflict=_header_conflict_pt(p["header_conflict"])
        ),
        source="invoice_mapper.py:2947-2950 (conflito: :386-410)",
    ),
    _fixed(
        "reason_contradiction",
        "reason",
        "A high-coverage fiscal equation strongly contradicts this proposal; "
        "keep it for review instead of accepting it automatically.",
        im.REASON_CONTRADICTION,
        "invoice_mapper.py:2952-2955",
    ),
    _fixed(
        "reason_ambiguous_empty",
        "reason",
        "The header match is ambiguous and the row samples are empty or all-zero; "
        "keep this mapping for review.",
        im.REASON_AMBIGUOUS_EMPTY,
        "invoice_mapper.py:2957-2960",
    ),
    LegacyTemplate(
        id="reason_vacuity",
        kind="reason",
        english=(
            "{client_column} has no populated rows, so it cannot be "
            "this column's source however well the headers match. "
            "Not accepted."
        ),
        params={"client_column": _PYTHON_STR_REPR},
        render=lambda p: im.REASON_VACUITY.format(
            client_column=_quoted_display_name_pt(p["client_column"])
        ),
        source="invoice_mapper.py:2972-2976",
    ),
    LegacyTemplate(
        id="reason_catalog_decision",
        kind="reason",
        english=(
            "Versioned official {catalog} codes cover "
            "{distinct_coverage} "
            "of the {unique_formatted_count} distinct codes used "
            "({coverage} of rows) and uniquely "
            "identify this candidate among the available columns."
        ),
        params={
            "catalog": _IDENTIFIER,
            "distinct_coverage": _PERCENT,
            "unique_formatted_count": _INTEGER,
            "coverage": _PERCENT,
        },
        # Mesma preparação de invoice_mapper._unique_distinctive_catalog_decision.
        render=lambda p: im.REASON_CATALOG_DECISION.format(
            catalog=im._catalog_label(p["catalog"]),
            distinct_coverage=_percent_br(p["distinct_coverage"]),
            unique_formatted_count=int(p["unique_formatted_count"]),
            coverage=_percent_br(p["coverage"]),
        ),
        source="invoice_mapper.py:1597-1602",
    ),
    # --- prova aritmética (arithmetic_proof) -----------------------------
    LegacyTemplate(
        id="arithmetic_proof",
        kind="arithmetic_proof",
        english=_PROOF_EN,
        params=_PROOF_PARAMS,
        render=_render_proof,
        source="invoice_mapper.py:1906-1909",
    ),
    LegacyTemplate(
        id="arithmetic_proof_not_decisive",
        kind="arithmetic_proof",
        english=(
            _PROOF_EN + " - but this client has {alternatives} disjoint {tax_scope} "
            "blocks that each satisfy it, so this does not single out this column"
        ),
        params={**_PROOF_PARAMS, "alternatives": _INTEGER},
        render=_render_proof,
        source="invoice_mapper.py:1906-1915",
    ),
    # --- detalhe dos sinais (cada frase de signal_detail) ----------------
    LegacyTemplate(
        id="signal_trio",
        kind="signal",
        english="participates in a proven valor = base x aliquota trio as the {slot}",
        params={"slot": _IDENTIFIER},
        render=lambda p: im.SIGNAL_TRIO.format(
            slot=report_schema.TEXT_LABELS["trio_slot"].get(p["slot"], p["slot"])
        ),
        source="invoice_mapper.py:2107",
    ),
    LegacyTemplate(
        id="signal_catalog",
        kind="signal",
        english=(
            "{distinct_coverage} of its "
            "{unique_formatted_count} distinct codes are valid "
            "{catalog}"
        ),
        params={
            "distinct_coverage": _PERCENT,
            "unique_formatted_count": _INTEGER,
            "catalog": _IDENTIFIER,
        },
        render=lambda p: im.SIGNAL_CATALOG.format(
            distinct_coverage=_percent_br(p["distinct_coverage"]),
            unique_formatted_count=int(p["unique_formatted_count"]),
            catalog=im._catalog_label(p["catalog"]),
        ),
        source="invoice_mapper.py:2122-2124",
    ),
    LegacyTemplate(
        id="signal_checkdigit",
        kind="signal",
        english="{fraction} of values pass the {kind} check digit",
        params={"fraction": _PERCENT, "kind": _IDENTIFIER},
        render=lambda p: im.SIGNAL_CHECKDIGIT.format(
            fraction=_percent_br(p["fraction"]),
            kind=report_schema.TEXT_LABELS["identifier_kind"].get(p["kind"], p["kind"]),
        ),
        source="invoice_mapper.py:2131",
    ),
    _fixed("signal_dates", "signal", "holds dates", im.SIGNAL_DATES, "invoice_mapper.py:2136"),
    _fixed(
        "signal_empty",
        "signal",
        "No values in this column.",
        im.SIGNAL_EMPTY,
        "invoice_mapper.py:2141",
    ),
    _fixed(
        "signal_all_zero",
        "signal",
        "Every value in this column is zero.",
        im.SIGNAL_ALL_ZERO,
        "invoice_mapper.py:2143",
    ),
    _fixed(
        "signal_no_signal",
        "signal",
        "Carries data, but no signal matched. Listed for completeness.",
        im.SIGNAL_NO_SIGNAL,
        "invoice_mapper.py:2145",
    ),
    # --- motivo de retenção (why_held) -----------------------------------
    _fixed(
        "why_held_vacuous",
        "why_held",
        "the proposed column has no populated rows - confirming "
        "this would ship a blank column",
        im.WHY_HELD_VACUOUS,
        "invoice_mapper.py:3136-3137",
    ),
    _fixed(
        "why_held_name_only",
        "why_held",
        "name evidence only - this branch cannot auto-accept "
        "however well the headers match",
        im.WHY_HELD_NAME_ONLY,
        "invoice_mapper.py:3139-3140",
    ),
    _fixed(
        "why_held_below_threshold",
        "why_held",
        "below the accept threshold",
        im.WHY_HELD_BELOW_THRESHOLD,
        "invoice_mapper.py:3142",
    ),
    # --- divergência da IA (resolver_disagrees) --------------------------
    LegacyTemplate(
        id="resolver_disagrees",
        kind="resolver_disagrees",
        english="resolver scored it {llm_final}",
        params={"llm_final": _DECIMAL},
        render=lambda p: im.RESOLVER_DISAGREES.format(llm_final=_decimal_br(p["llm_final"])),
        source="invoice_mapper.py:3111",
    ),
)

_BY_KIND: dict[str, tuple[LegacyTemplate, ...]] = {}
for _template in TEMPLATES:
    _BY_KIND.setdefault(_template.kind, ())
    _BY_KIND[_template.kind] += (_template,)
del _template


# ---------------------------------------------------------------------------
# Tradução
# ---------------------------------------------------------------------------


def match_template(text: str, kind: str) -> LegacyTemplate | None:
    """O modelo inglês de ``kind`` que casa com o texto inteiro, se houver."""
    for template in _BY_KIND.get(kind, ()):
        if template.regex.fullmatch(text):
            return template
    return None


def _translate_signal_detail(text: str) -> str | None:
    """Célula de detalhe dos sinais: toda frase precisa casar com um modelo."""
    pieces = []
    for piece in text.split(SIGNAL_SEPARATOR):
        template = match_template(piece, "signal")
        if template is None:
            return None
        pieces.append(template.translate(piece))
    return SIGNAL_SEPARATOR.join(pieces)


def _translate_kind(text: str, kind: str) -> str | None:
    if kind == "signal_detail":
        return _translate_signal_detail(text)
    template = match_template(text, kind)
    return None if template is None else template.translate(text)


def translate_text(text: object, kind: str | None = None) -> object:
    """Frase legada em inglês -> a frase em português que o mapeador grava hoje.

    ``kind`` é o campo de origem (um de ``KINDS``); só os modelos desse campo
    são tentados. Sem ``kind``, tenta todos, na ordem de ``KINDS``. Valor que
    não é texto, texto vazio e texto que não casa com nenhum modelo voltam
    intactos: prosa da IA, observação do revisor, frase já em português.
    """
    if not isinstance(text, str) or not text:
        return text
    if kind is not None and kind not in KINDS:
        raise ValueError(f"Tipo de frase desconhecido: {kind!r}. Use um de {KINDS}.")
    for candidate in (kind,) if kind is not None else KINDS:
        translated = _translate_kind(text, candidate)
        if translated is not None:
            return translated
    return text


def translate_cell(table: str, field_name: str, value: object) -> object:
    """Célula de um relatório legado -> português, se o campo é frase do mapeador.

    ``field_name`` é o nome interno (igual ao cabeçalho legado). Campos fora de
    ``TEXT_FIELDS`` (inclusive ``reviewer_note``) passam intactos.
    """
    if field_name not in TEXT_FIELDS.get(table, frozenset()):
        return value
    return translate_text(value, kind=field_name)


def is_translatable(table: str, field_name: str, value: object) -> bool:
    """Se a célula é uma frase legada que ``translate_cell`` reescreve."""
    if not isinstance(value, str) or not value:
        return False
    return translate_cell(table, field_name, value) != value
