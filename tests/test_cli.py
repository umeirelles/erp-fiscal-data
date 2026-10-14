"""Testes de ``mapper/cli.py``: ajuda e mensagens de erro do argparse em português."""

from __future__ import annotations

import argparse

import pytest

from mapper.cli import PortugueseArgumentParser

# ---------------------------------------------------------------------------
# CLI em português
# ---------------------------------------------------------------------------

ENGLISH_CHROME = (
    "usage:",
    "positional arguments",
    "options:",
    "show this help",
    "show program's version",
    "error:",
    "the following arguments",
    "unrecognized arguments",
    "expected one argument",
    "invalid choice",
    "invalid int value",
    "invalid float value",
    "tipo int",
    "tipo float",
    "ignored explicit",
    "argument --",
)


def _sheet(value: str) -> int:
    """Converte um argumento de aba, como as funções passadas em ``type=`` nas CLIs."""
    return int(value)


def _parser() -> PortugueseArgumentParser:
    parser = PortugueseArgumentParser(prog="ferramenta", description="Mapeia as colunas.")
    parser.add_argument("client", help="planilha exportada pelo cliente")
    parser.add_argument("--output", help="relatório de saída")
    parser.add_argument("--candidates", type=int, default=10, help="quantidade de candidatos")
    parser.add_argument("--final-threshold", type=float, default=0.9)
    parser.add_argument("--sheet", type=_sheet)
    parser.add_argument("--mode", choices=["offline", "online"])
    parser.add_argument("--offline", action="store_true")
    parser.add_argument("--version", action="version", version="ferramenta 1.0")
    extra = parser.add_argument_group("outras opções")
    extra.add_argument("--build", action="version", version="build 7")
    return parser


def test_the_help_is_in_portuguese(capsys) -> None:
    with pytest.raises(SystemExit) as stop:
        _parser().parse_args(["--help"])

    assert stop.value.code == 0
    out = capsys.readouterr().out
    assert out.startswith("uso: ferramenta [-h]")
    assert "\nargumentos posicionais:\n" in out
    assert "\nopções:\n" in out
    assert "-h, --help" in out
    assert "mostra esta mensagem de ajuda e sai" in out
    assert "planilha exportada pelo cliente" in out
    # A ajuda padrão de version, no grupo principal e num grupo próprio.
    assert out.count("mostra a versão do programa e sai") == 2
    for english in ENGLISH_CHROME:
        assert english not in out, english


@pytest.mark.parametrize(
    ("argv", "message"),
    [
        ([], "os seguintes argumentos são obrigatórios: client"),
        (["c.xlsx", "--mode", "nuvem"], "argumento --mode: valor não permitido: 'nuvem'"),
        (["c.xlsx", "--bogus"], "argumentos não reconhecidos: --bogus"),
        (["c.xlsx", "--output"], "argumento --output: esperava um argumento"),
        (
            ["c.xlsx", "--candidates", "dez"],
            "argumento --candidates: valor inválido 'dez': esperava um número inteiro",
        ),
        (
            ["c.xlsx", "--final-threshold", "0,9"],
            "argumento --final-threshold: valor inválido '0,9': esperava um número, "
            "com ponto como separador decimal (por exemplo, 0.9)",
        ),
        (["c.xlsx", "--sheet", "aba"], "valor inválido 'aba': esperava um valor do tipo _sheet"),
        (["c.xlsx", "--offline=sim"], "argumento --offline: esta opção não aceita valor: 'sim'"),
    ],
)
def test_common_errors_are_in_portuguese(capsys, argv, message) -> None:
    with pytest.raises(SystemExit) as stop:
        _parser().parse_args(argv)

    assert stop.value.code == 2
    err = capsys.readouterr().err
    assert err.startswith("uso: ferramenta")
    assert "ferramenta: erro: " in err
    assert message in err
    for english in ENGLISH_CHROME:
        assert english not in err, english


def test_other_parsers_stay_in_english(capsys) -> None:
    _parser().format_help()
    with pytest.raises(SystemExit):
        _parser().parse_args([])
    capsys.readouterr()

    plain = argparse.ArgumentParser(prog="plain")
    plain.add_argument("value")
    plain.print_usage()
    assert capsys.readouterr().out.startswith("usage: plain")
    with pytest.raises(SystemExit):
        plain.parse_args([])
    assert "error: the following arguments are required" in capsys.readouterr().err
