"""Testes da avaliação (mapeval).

A avaliação mede; ela não pode desviar. Estes testes fixam as invariantes que
tornam os números confiáveis e a linha de base que eles precisam reproduzir,
e conferem que um relatório no esquema em português é pontuado exatamente
como o mesmo relatório no esquema legado em inglês.
"""

from __future__ import annotations

import re
from pathlib import Path

import openpyxl
import pandas as pd
import pytest

import report_schema
from mapeval import cli
from mapeval.excel import (
    column_letter_to_index,
    index_to_column_letter,
    parse_coluna_annotation,
)
from mapeval.outcomes import Outcome, classify
from mapeval.score import Row, read_mappings, score_report
from mapeval.truth import KEY_CORRECTIONS, AnswerKey, load_answer_key

ROOT = Path(__file__).resolve().parent.parent
KEYED = {
    "datasul": (
        "data/clients/datasul_maio.xlsx",
        "data/reports/mapping_datasul_full.xlsx",
        "data/keys/de_para_datasul.xlsx",
    ),
    "oracle": (
        "data/clients/oracle.xlsx",
        "data/reports/mapping_oracle_full.xlsx",
        "data/keys/de_para_oracle.xlsx",
    ),
}
requires_workbooks = pytest.mark.skipif(
    not (ROOT / "data/keys/de_para_oracle.xlsx").exists(),
    reason="planilhas dos clientes ausentes",
)

#: Prova não decisiva como os relatórios legados a gravavam (em inglês).
LEGACY_NON_DECISIVE_PROOF = (
    "valor = base x aliquota holds for pis on 5501 rows at fit 0.9996 - but this "
    "client has 2 disjoint pis blocks that each satisfy it, so this does not "
    "single out this column"
)
#: A mesma prova escrita em português, com o marcador PROOF_NOT_DECISIVE_PT.
PORTUGUESE_NON_DECISIVE_PROOF = (
    "valor = base x alíquota se confirma para PIS em 5501 linhas, com aderência "
    "0,9996 - mas este cliente tem 2 blocos disjuntos de PIS que satisfazem a "
    "equação, portanto ela não distingue esta coluna"
)

_LEGACY_PROOF = re.compile(
    r"valor = base x aliquota holds for (?P<scope>\S+) on (?P<rows>[0-9]+) rows at fit "
    r"(?P<fit>[0-9.]+) - but this client has (?P<alternatives>[0-9]+) disjoint (?P=scope) "
    r"blocks that each satisfy it, so this does not single out this column"
)


def _portuguese_proof(text: object) -> object:
    """Reescreve em português uma prova não decisiva em inglês; o resto passa intacto."""
    if not isinstance(text, str) or report_schema.PROOF_NOT_DECISIVE_EN not in text:
        return text
    match = _LEGACY_PROOF.fullmatch(text)
    assert match is not None, f"prova não decisiva em formato inesperado: {text!r}"
    scope = match["scope"].upper()
    return (
        f"valor = base x alíquota se confirma para {scope} em {match['rows']} linhas, "
        f"com aderência {report_schema.format_decimal_br(float(match['fit']))} - mas este "
        f"cliente tem {match['alternatives']} blocos disjuntos de {scope} que satisfazem "
        f"a equação, portanto ela {report_schema.PROOF_NOT_DECISIVE_PT}"
    )


def _write_both_schemas(report: Path, destination: Path) -> tuple[Path, Path]:
    """Grava a aba de mapeamentos de ``report`` nos dois esquemas.

    Lê a aba em qualquer esquema e volta ao vocabulário interno. A cópia
    legada é o quadro interno com a aba ``Mappings``; a cópia em português
    passa por ``report_schema.to_external_frame``, e as provas não decisivas
    ganham a frase em português com o marcador. Funciona tanto com os
    relatórios arquivados em inglês quanto depois da migração para o
    português.
    """
    with pd.ExcelFile(report) as book:
        sheet = report_schema.find_sheet(book.sheet_names, "mappings", None)
        frame = pd.read_excel(book, sheet_name=sheet)
    internal, _ = report_schema.to_internal_frame(frame, "mappings")

    legacy_path = destination / f"legacy_{report.name}"
    internal.to_excel(legacy_path, sheet_name=report_schema.sheet_name(
        "mappings", report_schema.LEGACY_EN), index=False)

    portuguese = internal.copy()
    if "arithmetic_proof" in portuguese.columns:
        portuguese["arithmetic_proof"] = portuguese["arithmetic_proof"].map(_portuguese_proof)
    portuguese_path = destination / f"pt_{report.name}"
    report_schema.to_external_frame(portuguese, "mappings").to_excel(
        portuguese_path, sheet_name=report_schema.sheet_name("mappings"), index=False
    )
    return legacy_path, portuguese_path


def _summary(score) -> tuple:
    """Tudo o que a CLI imprime de um cliente com gabarito, mais cada linha."""
    rows = [
        (r.model_column, r.client_column, r.status, r.method, r.final_confidence,
         r.outcome, r.header_independent)
        for r in score.rows
    ]
    return (
        rows,
        score.present,
        score.count(Outcome.CORRECT),
        score.delivered_correct,
        len(score.accepted),
        len(score.withheld_correct),
        len(score.undetermined_accepts),
        score.header_independent_share,
        score.accept_precision_lower,
        [(r.model_column, r.client_column) for r in score.vacuous_targets],
        [(r.model_column, r.client_column) for r in score.vacuous_proposals],
    )


def _accepted_row(proof: str, method: str = "data") -> Row:
    return Row(model_column=213, model_name="Aliquota PIS", client_column=96,
               status="accepted", method=method, final_confidence=0.98,
               outcome=Outcome.CORRECT, arithmetic_proof=proof)


@pytest.mark.parametrize(
    ("letters", "index"),
    [("A", 1), ("Z", 26), ("AA", 27), ("AZ", 52), ("BA", 53), ("BV", 74), ("FY", 181), ("GZ", 208)],
)
def test_column_letters_round_trip(letters: str, index: int) -> None:
    assert column_letter_to_index(letters) == index
    assert index_to_column_letter(index) == letters


def test_annotation_tolerates_the_trailing_space_in_the_real_key() -> None:
    # A linha 1 de de_para_datasul.xlsx tem mesmo 'Coluna BV ', com espaço no
    # fim; um leitor ingênuo descarta essa coluna sem avisar.
    assert parse_coluna_annotation("Coluna BV ") == 74


def test_annotation_ignores_cells_that_are_not_annotations() -> None:
    assert parse_coluna_annotation(None) is None
    assert parse_coluna_annotation("Base CBS") is None


def test_a_delivered_vacuous_target_is_fabrication_even_when_the_key_agrees() -> None:
    # Um mapeamento aceito sobre uma coluna sem dado chega à planilha do
    # cliente parecendo mapeado e em branco, sem aviso. Está errado, diga o
    # gabarito o que disser, por isso a coluna vazia é verificada antes de tudo.
    assert classify(
        model_column=1,
        chosen_client_column=5,
        expected_client_column=5,
        chosen_is_vacuous=True,
        in_key=True,
        delivered=True,
    ) is Outcome.FABRICATED


def test_an_undelivered_vacuous_proposal_is_not_a_fabrication() -> None:
    # Uma linha em revisão nunca é preenchida pelo apply_invoice_mapping, então
    # não chega ao cliente. Confundir as duas coisas fazia a execução offline
    # reportar 35 fabricações quando não entrega nenhuma.
    assert classify(
        model_column=1,
        chosen_client_column=5,
        expected_client_column=5,
        chosen_is_vacuous=True,
        in_key=True,
        delivered=False,
    ) is Outcome.CORRECT


def test_absence_is_a_correct_answer_not_a_miss() -> None:
    assert classify(
        model_column=1,
        chosen_client_column=None,
        expected_client_column=None,
        chosen_is_vacuous=False,
        in_key=True,
    ) is Outcome.CORRECTLY_ABSENT


def test_unkeyed_columns_are_never_credited() -> None:
    for chosen in (None, 5):
        assert classify(
            model_column=1,
            chosen_client_column=chosen,
            expected_client_column=None,
            chosen_is_vacuous=False,
            in_key=False,
        ) is Outcome.UNDETERMINED


@requires_workbooks
@pytest.mark.parametrize(("client", "expected"), [("datasul", 30), ("oracle", 26)])
def test_recall_denominator_is_the_present_set_never_227(client: str, expected: int) -> None:
    key = load_answer_key(ROOT / KEYED[client][2], client)
    assert key.denominator == expected != 227


@requires_workbooks
def test_harness_reproduces_the_corrected_baseline() -> None:
    total = 0
    for client, (client_file, report_file, key_file) in KEYED.items():
        key = load_answer_key(ROOT / key_file, client)
        score = score_report(ROOT / report_file, ROOT / client_file, key)
        total += score.count(Outcome.CORRECT)
    assert total == 43


@requires_workbooks
def test_the_four_key_corrections_are_worth_exactly_four_columns() -> None:
    # Se isto mudar, ou os relatórios mudaram ou uma correção está errada.
    # Nada mais pode separar o número estrito do corrigido.
    strict = corrected = 0
    for client, (client_file, report_file, key_file) in KEYED.items():
        for apply_corrections, bucket in ((False, "strict"), (True, "corrected")):
            key = load_answer_key(ROOT / key_file, client, apply_corrections=apply_corrections)
            score = score_report(ROOT / report_file, ROOT / client_file, key)
            if bucket == "strict":
                strict += score.count(Outcome.CORRECT)
            else:
                corrected += score.count(Outcome.CORRECT)
    # Os literais mudam sempre que o mapeador melhora; a DIFERENÇA é a
    # invariante, e é ela que este teste existe para proteger.
    assert corrected - strict == 4
    assert (strict, corrected) == (39, 43)
    assert sum(len(v) for v in KEY_CORRECTIONS.values()) == 4


@requires_workbooks
@pytest.mark.parametrize(("client", "report"), [
    ("datasul", "data/reports/mapping_datasul_full.xlsx"),
    ("oracle", "data/reports/mapping_oracle_full.xlsx"),
])
def test_no_report_delivers_a_column_with_no_data(client: str, report: str) -> None:
    # O datasul entregou duas assim - m32 <- c280 e m188 <- c326, as duas
    # aceitas por semelhança de cabeçalho sobre colunas com zero linhas
    # preenchidas de 6.804. A trava de coluna vazia as removeu; isto garante
    # que não voltem.
    key = load_answer_key(ROOT / KEYED[client][2], client)
    score = score_report(ROOT / report, ROOT / KEYED[client][0], key)
    assert [
        (row.model_column, row.client_column) for row in score.vacuous_targets
    ] == []


@requires_workbooks
def test_delivered_recall_is_never_above_picking_recall() -> None:
    # A invariante que permite imprimir os dois números lado a lado: a entrega
    # é um subconjunto da escolha, então uma execução nunca entrega uma coluna
    # que não encontrou antes.
    for client, (client_file, report_file, key_file) in KEYED.items():
        key = load_answer_key(ROOT / key_file, client)
        score = score_report(ROOT / report_file, ROOT / client_file, key)
        assert score.delivered_correct <= score.count(Outcome.CORRECT)
        assert len(score.withheld_correct) == (
            score.count(Outcome.CORRECT) - score.delivered_correct
        )


@requires_workbooks
def test_the_accept_gate_withholds_eleven_correct_mappings() -> None:
    # O recall da escolha é 42/56, mas o cliente recebe 31. A diferença é a
    # maior perda isolada do pipeline e fica invisível para qualquer métrica
    # que pontue só a escolha, por isso está fixada aqui.
    found = delivered = 0
    for client, (client_file, report_file, key_file) in KEYED.items():
        key = load_answer_key(ROOT / key_file, client)
        score = score_report(ROOT / report_file, ROOT / client_file, key)
        found += score.count(Outcome.CORRECT)
        delivered += score.delivered_correct
    # A diferença é a invariante que dá nome a este teste; os totais mudam
    # conforme o mapeador melhora, e os dois precisam mudar juntos, senão
    # algo regrediu.
    assert found - delivered == 11
    assert (found, delivered) == (43, 32)


@requires_workbooks
def test_every_withheld_correct_mapping_sits_below_the_accept_threshold() -> None:
    # Confirma que a diferença é o limiar de confiança e não outra recusa: se
    # uma linha retida passar de 0,90, outra coisa a está bloqueando e esta
    # análise deixa de explicar a perda.
    for client, (client_file, report_file, key_file) in KEYED.items():
        key = load_answer_key(ROOT / key_file, client)
        score = score_report(ROOT / report_file, ROOT / client_file, key)
        for row in score.withheld_correct:
            assert (row.final_confidence or 0.0) < 0.90, (
                f"{client} m{row.model_column} retida com {row.final_confidence}"
            )


def test_a_non_discriminating_proof_is_not_independent_evidence() -> None:
    # Caso legado, em inglês. Dois blocos disjuntos do mesmo tributo
    # satisfazem valor = base x alíquota, então uma prova tirada de qualquer
    # um deles não distingue nenhum. Contá-la deixaria um cara ou coroa entre
    # blocos passar por evidência aritmética.
    assert _accepted_row("valor = base x aliquota holds for pis on 500 rows at fit 0.99").header_independent
    assert report_schema.PROOF_NOT_DECISIVE_EN in LEGACY_NON_DECISIVE_PROOF
    assert not _accepted_row(LEGACY_NON_DECISIVE_PROOF).header_independent


def test_a_non_discriminating_portuguese_proof_is_not_independent_evidence() -> None:
    # A mesma regra com a prova escrita em português: o marcador é
    # "não distingue esta coluna". Se o mapeval reconhecesse só o marcador
    # inglês, os quatro aceites de datasul com prova assim passariam a contar
    # como independentes de cabeçalho.
    assert _accepted_row(
        "valor = base x alíquota se confirma para PIS em 500 linhas, com aderência 0,99"
    ).header_independent
    assert report_schema.PROOF_NOT_DECISIVE_PT in PORTUGUESE_NON_DECISIVE_PROOF
    assert not _accepted_row(PORTUGUESE_NON_DECISIVE_PROOF).header_independent
    # Um aceite por catálogo continua independente, com ou sem prova decisiva.
    assert _accepted_row(PORTUGUESE_NON_DECISIVE_PROOF, method="catalog").header_independent


def _synthetic_client(path: Path) -> Path:
    """Exportação mínima: c1 e c3 com dados, c2 sem nenhuma linha preenchida."""
    book = openpyxl.Workbook()
    sheet = book.active
    sheet.append(["Base PIS", "Coluna vazia", "CFOP"])
    sheet.append([100.0, None, "5102"])
    sheet.append([200.0, None, "6102"])
    book.save(path)
    return path


#: Linhas de mapeamento no vocabulário interno (nomes e valores do esquema legado).
_SYNTHETIC_ROWS = [
    {"model_column_number": 1, "model_column": "Base PIS", "client_column_number": 1,
     "client_column": "Base PIS", "final_confidence": 0.95, "method": "name",
     "status": "accepted", "arithmetic_proof": None},
    {"model_column_number": 2, "model_column": "CFOP", "client_column_number": 3,
     "client_column": "CFOP", "final_confidence": 0.93, "method": "catalog",
     "status": "accepted", "arithmetic_proof": None},
    {"model_column_number": 3, "model_column": "Isentas PIS", "client_column_number": 2,
     "client_column": "Coluna vazia", "final_confidence": 0.5, "method": "data",
     "status": "review", "arithmetic_proof": None},
    {"model_column_number": 4, "model_column": "Base COFINS", "client_column_number": None,
     "client_column": None, "final_confidence": 0.2, "method": "data",
     "status": "no_match", "arithmetic_proof": None},
]


def _synthetic_report(path: Path, schema: str, proof: str) -> Path:
    """Relatório mínimo no esquema dado; a linha m1 leva a prova recebida."""
    fields = list(_SYNTHETIC_ROWS[0])
    book = openpyxl.Workbook()
    sheet = book.active
    sheet.title = report_schema.sheet_name("mappings", schema)
    sheet.append([report_schema.header_label("mappings", field, schema) for field in fields])
    for number, row in enumerate(_SYNTHETIC_ROWS, start=1):
        values = dict(row, arithmetic_proof=proof if number == 1 else None)
        if schema == report_schema.PT_BR:
            values = {f: report_schema.external_value("mappings", f, v) for f, v in values.items()}
        sheet.append([values[field] for field in fields])
    book.save(path)
    return path


def test_both_schemas_score_identically_without_the_client_workbooks(tmp_path: Path) -> None:
    # Não depende das planilhas confidenciais. O relatório em português tem
    # situacao 'aceito', metodo 'catalogo' e a prova não decisiva em
    # português; o legado tem os mesmos valores em inglês. A pontuação tem de
    # ser a mesma, linha a linha.
    client = _synthetic_client(tmp_path / "cliente.xlsx")
    key = AnswerKey(client="sintetico", present={1: 1, 2: 3})
    legacy = score_report(
        _synthetic_report(tmp_path / "legado.xlsx", report_schema.LEGACY_EN,
                          LEGACY_NON_DECISIVE_PROOF),
        client, key,
    )
    portuguese = score_report(
        _synthetic_report(tmp_path / "pt.xlsx", report_schema.PT_BR,
                          PORTUGUESE_NON_DECISIVE_PROOF),
        client, key,
    )
    assert _summary(portuguese) == _summary(legacy)
    assert [r.status for r in portuguese.rows] == ["accepted", "accepted", "review", "no_match"]
    assert [r.outcome for r in portuguese.rows] == [
        Outcome.CORRECT, Outcome.CORRECT, Outcome.UNDETERMINED, Outcome.UNDETERMINED,
    ]
    assert portuguese.delivered_correct == 2
    # m1 tem só prova não decisiva; m2 é aceite por catálogo.
    assert portuguese.header_independent_share == 0.5
    # m3 propõe a coluna vazia, mas está em revisão: não é fabricação.
    assert [(r.model_column, r.client_column) for r in portuguese.vacuous_proposals] == [(3, 2)]
    assert portuguese.vacuous_targets == []


def test_a_half_translated_report_is_refused(tmp_path: Path) -> None:
    # Cabeçalhos em português numa aba com o nome legado: esquema misto. Lido
    # pela metade, o relatório daria números errados sem aviso.
    path = _synthetic_report(tmp_path / "pt.xlsx", report_schema.PT_BR, "")
    book = openpyxl.load_workbook(path)
    book.active.title = report_schema.sheet_name("mappings", report_schema.LEGACY_EN)
    book.save(path)
    with pytest.raises(ValueError, match="Esquema misto"):
        read_mappings(path)


def test_a_report_without_the_mappings_sheet_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "sem_aba.xlsx"
    book = openpyxl.Workbook()
    book.active.title = "Outra"
    book.save(path)
    with pytest.raises(ValueError, match="não tem a aba 'Mapeamentos'"):
        read_mappings(path)


@requires_workbooks
@pytest.mark.parametrize("client", ["datasul", "oracle"])
def test_a_portuguese_report_scores_exactly_like_the_legacy_one(client: str, tmp_path: Path) -> None:
    # A regressão ponta a ponta da leitura nos dois esquemas, com os
    # relatórios reais: a mesma aba gravada em inglês e em português dá a
    # mesma pontuação em todas as linhas e em todos os números impressos.
    client_file, report_file, key_file = KEYED[client]
    key = load_answer_key(ROOT / key_file, client)
    legacy_path, portuguese_path = _write_both_schemas(ROOT / report_file, tmp_path)
    legacy = score_report(legacy_path, ROOT / client_file, key)
    portuguese = score_report(portuguese_path, ROOT / client_file, key)
    assert _summary(portuguese) == _summary(legacy)
    if client == "datasul":
        # O caso tem de exercitar o marcador em português: há aceites cuja
        # única prova é não decisiva, e eles não contam como independentes.
        marked = [
            r for r in portuguese.accepted
            if r.arithmetic_proof and report_schema.PROOF_NOT_DECISIVE_PT in r.arithmetic_proof
        ]
        assert marked
        assert all(r.method == "catalog" or not r.header_independent for r in marked)


@requires_workbooks
def test_cli_prints_the_same_figures_for_both_schemas(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    client_file, report_file, key_file = KEYED["oracle"]
    legacy_path, portuguese_path = _write_both_schemas(ROOT / report_file, tmp_path)
    outputs = []
    for report in (legacy_path, portuguese_path):
        # Caminhos absolutos: ``root / caminho`` devolve o próprio caminho.
        monkeypatch.setitem(
            cli.CLIENTS, "oracle",
            (str(ROOT / client_file), str(report), str(ROOT / key_file)),
        )
        assert cli.main(["--client", "oracle", "--show-losses"]) == 0
        outputs.append(capsys.readouterr().out)
    assert outputs[0] == outputs[1]
    text = outputs[1]
    assert "TOTAL COM GABARITO  encontrados" in text
    assert "aceites independentes de cabeçalho" in text
    # Porcentagens e confianças com vírgula decimal, nunca com ponto.
    assert re.search(r"\d,\d%", text)
    assert not re.search(r"\d\.\d+%|final=\d\.\d", text)


def test_percentages_use_a_decimal_comma_and_keep_their_width() -> None:
    assert cli._pct(0.8) == " 80,0%"
    assert cli._pct(1.0) == "100,0%"
    assert cli._pct(0.0) == "  0,0%"
    assert cli._pct(None) == "  n/d "
    assert cli._decimal(0.8999) == "0,8999"
    assert cli._decimal(None) == "n/d"


def test_every_outcome_has_a_portuguese_label_that_fits_its_column() -> None:
    assert set(cli.OUTCOME_LABELS) == set(Outcome)
    assert cli.OUTCOME_LABELS[Outcome.WRONG_COLUMN] == "coluna_errada"
    assert cli.OUTCOME_LABELS[Outcome.MISSED] == "perdido"
    # --show-losses só lista perdidas e colunas erradas, numa coluna de 14
    # caracteres alinhada com "retido".
    assert all(
        len(cli.OUTCOME_LABELS[outcome]) <= 14
        for outcome in (Outcome.MISSED, Outcome.WRONG_COLUMN)
    )


def test_help_is_in_portuguese(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exit_info:
        cli.main(["--help"])
    assert exit_info.value.code == 0
    text = capsys.readouterr().out
    assert text.startswith("uso: python -m mapeval")
    assert "opções:" in text
    assert "mostra esta mensagem de ajuda e sai" in text
    assert "--show-losses" in text and "--strict-key-only" in text


@requires_workbooks
def test_accepts_the_key_cannot_check_are_counted_and_visible() -> None:
    # O código de saída lê só as fabricações, e um aceite fora do gabarito
    # sobre uma coluna PREENCHIDA é UNDETERMINED, não FABRICATED - então uma
    # regra que inventa mapeamentos fora do gabarito sai com 0. Isto fixa o
    # número que expõe esse caso.
    for client, (client_file, report_file, key_file) in KEYED.items():
        key = load_answer_key(ROOT / key_file, client)
        score = score_report(ROOT / report_file, ROOT / client_file, key)
        assert len(score.undetermined_accepts) == (
            len(score.accepted)
            - sum(1 for r in score.accepted if r.outcome is not Outcome.UNDETERMINED)
        )
        # e é exatamente o que separa o limite de precisão de 100%
        good = sum(1 for r in score.accepted if r.outcome is Outcome.CORRECT)
        assert good + len(score.undetermined_accepts) <= len(score.accepted)
