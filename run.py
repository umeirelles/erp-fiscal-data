"""Entry point único do mapeador de planilhas fiscais.

Cada comando chama uma função de um módulo de ``mapper/``; as opções de cada um
aparecem em ``python run.py COMANDO --help``.
"""

from __future__ import annotations

import argparse
import sys
from typing import Sequence

from mapper import apply, catalogs, evaluate, report
from mapper.cli import PortugueseArgumentParser

#: Comando -> (função que o executa, descrição). Na ordem em que costumam ser usados.
COMMANDS = {
    "map": (report.main, "gera o relatório de mapeamento de uma exportação do cliente"),
    "apply": (apply.main, "aplica o relatório revisado e gera a planilha no layout do modelo"),
    "evaluate": (evaluate.main, "compara os relatórios gravados com os gabaritos de-para"),
    "update-catalogs": (catalogs.main, "baixa as tabelas fiscais oficiais e regrava reference_data/"),
}


def main(argv: Sequence[str] | None = None) -> int:
    parser = PortugueseArgumentParser(
        prog="run.py",
        description="Mapeia as colunas de uma exportação de ERP para o modelo fiscal da consultoria.",
        epilog="comandos:\n" + "\n".join(
            f"  {name:<17} {description}" for name, (_, description) in COMMANDS.items()
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("command", metavar="COMANDO", choices=COMMANDS,
                        help="um dos comandos listados abaixo")
    parser.add_argument("arguments", metavar="ARGUMENTOS", nargs=argparse.REMAINDER,
                        help="opções do comando; veja python run.py COMANDO --help")
    args = parser.parse_args(argv)
    run_command, _ = COMMANDS[args.command]
    return run_command(args.arguments)


if __name__ == "__main__":
    sys.exit(main())
