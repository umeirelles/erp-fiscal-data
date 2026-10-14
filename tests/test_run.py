"""Testes de ``run.py``: o entry point único e o encaminhamento dos comandos."""

from __future__ import annotations

import pytest

from mapper import apply, catalogs, evaluate, report
import run


def test_each_command_calls_one_function_in_one_module() -> None:
    assert {name: function for name, (function, _) in run.COMMANDS.items()} == {
        "map": report.main,
        "apply": apply.main,
        "evaluate": evaluate.main,
        "update-catalogs": catalogs.main,
    }


def test_the_help_lists_the_four_commands_in_portuguese(capsys) -> None:
    with pytest.raises(SystemExit) as stop:
        run.main(["--help"])
    assert stop.value.code == 0
    out = capsys.readouterr().out
    assert out.startswith("uso: run.py")
    for name in ("map", "apply", "evaluate", "update-catalogs"):
        assert f"\n  {name} " in out


def test_the_remaining_arguments_go_to_the_command(capsys) -> None:
    with pytest.raises(SystemExit) as stop:
        run.main(["evaluate", "--help"])
    assert stop.value.code == 0
    assert capsys.readouterr().out.startswith("uso: run.py evaluate")


def test_an_unknown_command_is_refused_in_portuguese(capsys) -> None:
    with pytest.raises(SystemExit) as stop:
        run.main(["mapear"])
    assert stop.value.code == 2
    assert "argumento COMANDO: valor não permitido: 'mapear'" in capsys.readouterr().err
