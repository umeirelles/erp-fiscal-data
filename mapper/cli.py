"""Ajuda e mensagens de erro em português para os comandos de ``run.py``.

``PortugueseArgumentParser`` traduz os textos do argparse só durante cada chamada,
sem afetar outros parsers; ``_error_message`` formata erros de leitura e gravação.
"""

from __future__ import annotations

import argparse
import contextlib
import functools
from typing import Iterator

# As flags ficam em inglês. Ao criar um argumento, defina ``help`` e ``metavar``
# em português; sem ``metavar``, o argparse usa a flag em maiúsculas.


#: Descrições dos tipos esperados nas mensagens de valor inválido.
TYPE_LABELS: dict[str, str] = {
    "int": "um número inteiro",
    # Quem digita 0,9 no terminal recebe a dica do separador decimal.
    "float": "um número, com ponto como separador decimal (por exemplo, 0.9)",
}


class _InvalidValueMessage(str):
    """Usa os rótulos de ``TYPE_LABELS`` nas mensagens de valor inválido.

    O argparse fornece o ``__name__`` do tipo ao formatar a mensagem. Tipos
    personalizados sem rótulo aparecem como "um valor do tipo <nome>".
    """

    def __mod__(self, args):
        if isinstance(args, dict) and "type" in args:
            name = str(args["type"])
            args = {**args, "type": TYPE_LABELS.get(name, f"um valor do tipo {name}")}
        return str.__mod__(self, args)


#: Mensagens do argparse (o texto original é a chave) -> português.
#: Cobre as do Python 3.13 e as variantes de versões próximas.
MESSAGES: dict[str, str] = {
    "usage: ": "uso: ",
    "positional arguments": "argumentos posicionais",
    "options": "opções",
    # Título da seção de opções até o Python 3.9.
    "optional arguments": "opções",
    "show this help message and exit": "mostra esta mensagem de ajuda e sai",
    "show program's version number and exit": "mostra a versão do programa e sai",
    " (default: %(default)s)": " (padrão: %(default)s)",
    "%(prog)s: error: %(message)s\n": "%(prog)s: erro: %(message)s\n",
    "%(prog)s: warning: %(message)s\n": "%(prog)s: aviso: %(message)s\n",
    "argument %(argument_name)s: %(message)s": "argumento %(argument_name)s: %(message)s",
    "the following arguments are required: %s": "os seguintes argumentos são obrigatórios: %s",
    "one of the arguments %s is required": "um destes argumentos é obrigatório: %s",
    "unrecognized arguments: %s": "argumentos não reconhecidos: %s",
    "not allowed with argument %s": "não pode ser usado junto com o argumento %s",
    "ambiguous option: %(option)s could match %(matches)s": (
        "opção ambígua: %(option)s pode ser %(matches)s"
    ),
    # Valor dado a uma opção que não aceita valor (--offline=sim).
    "ignored explicit argument %r": "esta opção não aceita valor: %r",
    "expected one argument": "esperava um argumento",
    "expected at most one argument": "esperava no máximo um argumento",
    "expected at least one argument": "esperava pelo menos um argumento",
    "invalid %(type)s value: %(value)r": _InvalidValueMessage(
        "valor inválido %(value)r: esperava %(type)s"
    ),
    "invalid choice: %(value)r (choose from %(choices)s)": (
        "valor não permitido: %(value)r (valores aceitos: %(choices)s)"
    ),
    # Python 3.14 sugere o valor mais parecido.
    "invalid choice: %(value)r, maybe you meant %(closest)r? (choose from %(choices)s)": (
        "valor não permitido: %(value)r; você quis dizer %(closest)r? "
        "(valores aceitos: %(choices)s)"
    ),
    "unexpected option string: %s": "opção inesperada: %s",
    "can't open '%(filename)s': %(error)s": "não foi possível abrir '%(filename)s': %(error)s",
    "subcommands": "subcomandos",
    "unknown parser %(parser_name)r (choices: %(choices)s)": (
        "subcomando desconhecido: %(parser_name)r (valores aceitos: %(choices)s)"
    ),
    "option '%(option)s' is deprecated": "a opção '%(option)s' está obsoleta",
    "argument '%(argument_name)s' is deprecated": "o argumento '%(argument_name)s' está obsoleto",
    "command '%(parser_name)s' is deprecated": "o comando '%(parser_name)s' está obsoleto",
}

#: Mensagens com plural: (singular, plural) originais -> (singular, plural) em português.
PLURAL_MESSAGES: dict[tuple[str, str], tuple[str, str]] = {
    ("expected %s argument", "expected %s arguments"): (
        "esperava %s argumento",
        "esperava %s argumentos",
    ),
}

#: Textos padrão de help e version usados quando não há help personalizado.
_DEFAULT_ACTION_HELPS = frozenset(
    {"show this help message and exit", "show program's version number and exit"}
)


def _translate(message: str) -> str:
    """Retorna a tradução registrada, ou a mensagem original quando não houver tradução."""
    return MESSAGES.get(message, message)


def _translate_plural(singular: str, plural: str, count: int) -> str:
    """Seleciona a forma singular ou plural, usando a tradução quando disponível."""
    singular, plural = PLURAL_MESSAGES.get((singular, plural), (singular, plural))
    return singular if count == 1 else plural


@contextlib.contextmanager
def _portuguese_messages() -> Iterator[None]:
    """Ativa a tradução durante o bloco e restaura as funções anteriores ao sair.

    Restaurar o estado anterior permite chamadas aninhadas, como ``parse_args``
    chamando ``error``, sem desativar a tradução antes da hora.
    """
    previous = (argparse._, argparse.ngettext)
    argparse._ = _translate
    argparse.ngettext = _translate_plural
    try:
        yield
    finally:
        argparse._, argparse.ngettext = previous


def _in_portuguese(method):
    """Executa um método do parser com a tradução temporariamente ativa."""

    @functools.wraps(method)
    def wrapper(*args, **kwargs):
        with _portuguese_messages():
            return method(*args, **kwargs)

    return wrapper


class PortugueseArgumentParser(argparse.ArgumentParser):
    """Adapta ``argparse.ArgumentParser`` para exibir ajuda e erros em português.

    Aceita os mesmos argumentos do parser padrão. Os métodos que geram mensagens
    ativam a tradução apenas durante a chamada. Flags e nomes internos não mudam;
    ``help`` e ``metavar`` são definidos pelo módulo que cria a CLI.

    Erros de argumentos mantêm o comportamento do argparse: mensagem em stderr
    e ``SystemExit`` com exit code 2.
    """

    # Os métodos que montam textos do argparse. O __init__ monta os títulos
    # das seções e a ajuda de -h.
    __init__ = _in_portuguese(argparse.ArgumentParser.__init__)
    parse_args = _in_portuguese(argparse.ArgumentParser.parse_args)
    parse_known_args = _in_portuguese(argparse.ArgumentParser.parse_known_args)
    parse_intermixed_args = _in_portuguese(argparse.ArgumentParser.parse_intermixed_args)
    parse_known_intermixed_args = _in_portuguese(
        argparse.ArgumentParser.parse_known_intermixed_args
    )
    add_subparsers = _in_portuguese(argparse.ArgumentParser.add_subparsers)
    format_usage = _in_portuguese(argparse.ArgumentParser.format_usage)
    error = _in_portuguese(argparse.ArgumentParser.error)

    def format_help(self) -> str:
        # A ação version cria seu help fora dos métodos envolvidos pelo decorator.
        # A tradução aqui também cobre argumentos adicionados a grupos próprios.
        for action in self._actions:
            if action.help in _DEFAULT_ACTION_HELPS:
                action.help = MESSAGES[action.help]
        with _portuguese_messages():
            return super().format_help()


# ---------------------------------------------------------------------------
# Erros de leitura e gravação de arquivos, comuns aos comandos
# ---------------------------------------------------------------------------


def _error_message(exc: ValueError | OSError) -> str:
    """Formata a mensagem de erro de leitura ou gravação exibida pela CLI."""
    if isinstance(exc, FileNotFoundError):
        return f"Arquivo não encontrado: {exc.filename}"
    if isinstance(exc, PermissionError):
        return f"Sem permissão para acessar o arquivo: {exc.filename}"
    if isinstance(exc, OSError):
        target = f" {exc.filename}" if exc.filename else ""
        return f"Não foi possível ler ou gravar o arquivo{target}: {exc.strerror or exc}"
    return f"Não foi possível gerar o relatório de mapeamento: {exc}"
