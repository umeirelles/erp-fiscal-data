"""Linha de comando em português: ``ArgumentParser`` com ajuda e erros traduzidos.

O ``argparse`` monta os textos fixos dele (``usage:``, os títulos das seções,
a ajuda de ``-h`` e as mensagens de erro) chamando ``argparse._``, que é o
``gettext`` da biblioteca padrão. ``PortugueseArgumentParser`` troca essa
função por uma tradução própria só enquanto um método do próprio parser está
rodando, e devolve a original ao sair, mesmo quando o argparse encerra o
programa com ``SystemExit``. Assim nenhum outro parser do processo (o do
pytest, por exemplo) muda de língua.

Os nomes das flags (``--offline``, ``--output`` ...) continuam em inglês; a
ajuda de cada argumento é escrita em português por quem cria o parser.
"""

from __future__ import annotations

import argparse
import contextlib
from typing import Iterator

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
    "ignored explicit argument %r": "valor explícito ignorado: %r",
    "expected one argument": "esperava um argumento",
    "expected at most one argument": "esperava no máximo um argumento",
    "expected at least one argument": "esperava pelo menos um argumento",
    "invalid %(type)s value: %(value)r": "valor inválido para o tipo %(type)s: %(value)r",
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


def _translate(message: str) -> str:
    """Substituto de ``gettext``: devolve a tradução ou, sem tradução, o original."""
    return MESSAGES.get(message, message)


def _translate_plural(singular: str, plural: str, count: int) -> str:
    """Substituto de ``ngettext``, com a mesma regra de escolha do plural."""
    translated = PLURAL_MESSAGES.get((singular, plural))
    if translated is not None:
        singular, plural = translated
    return singular if count == 1 else plural


@contextlib.contextmanager
def _portuguese_messages() -> Iterator[None]:
    """Põe o argparse em português enquanto o bloco roda e restaura ao sair.

    Guarda e restaura o que estava instalado, então chamadas aninhadas (por
    exemplo ``parse_args`` chamando ``error``) funcionam.
    """
    previous = (argparse._, argparse.ngettext)
    argparse._ = _translate
    argparse.ngettext = _translate_plural
    try:
        yield
    finally:
        argparse._, argparse.ngettext = previous


class PortugueseArgumentParser(argparse.ArgumentParser):
    """``ArgumentParser`` cuja ajuda e cujas mensagens de erro saem em português.

    Uso igual ao do ``argparse.ArgumentParser``. A saída fica assim::

        uso: invoice_mapper.py [-h] [--offline] client

        argumentos posicionais:
          client      planilha .xlsx exportada pelo cliente

        opções:
          -h, --help  mostra esta mensagem de ajuda e sai
          --offline   usa só a comparação léxica de cabeçalhos, sem a OpenAI

    e um erro sai como ``invoice_mapper.py: erro: os seguintes argumentos
    são obrigatórios: client``, com código de saída 2.
    """

    def __init__(self, *args, **kwargs) -> None:
        # Os títulos das seções e a ajuda de -h são montados aqui.
        with _portuguese_messages():
            super().__init__(*args, **kwargs)

    def parse_args(self, *args, **kwargs):
        with _portuguese_messages():
            return super().parse_args(*args, **kwargs)

    def parse_known_args(self, *args, **kwargs):
        with _portuguese_messages():
            return super().parse_known_args(*args, **kwargs)

    def parse_intermixed_args(self, *args, **kwargs):
        with _portuguese_messages():
            return super().parse_intermixed_args(*args, **kwargs)

    def parse_known_intermixed_args(self, *args, **kwargs):
        with _portuguese_messages():
            return super().parse_known_intermixed_args(*args, **kwargs)

    def add_subparsers(self, **kwargs):
        with _portuguese_messages():
            return super().add_subparsers(**kwargs)

    def format_usage(self) -> str:
        with _portuguese_messages():
            return super().format_usage()

    def format_help(self) -> str:
        with _portuguese_messages():
            return super().format_help()

    def error(self, message: str):
        with _portuguese_messages():
            super().error(message)
