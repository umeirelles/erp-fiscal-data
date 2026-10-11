"""Compara os relatórios arquivados com os gabaritos e apresenta as métricas de mapeamento."""

from __future__ import annotations

import sys
from pathlib import Path

import report_schema
from cli_ptbr import PortugueseArgumentParser
from mapeval.outcomes import Outcome
from mapeval.score import (
    Score,
    method_census,
    score_report,
)
from mapeval.truth import AnswerKey, load_answer_key

#: cliente -> (exportação do cliente, relatório de mapeamento, gabarito de-para ou None)
CLIENTS: dict[str, tuple[str, str, str | None]] = {
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
    # de_para_procosa.xlsx é uma especificação no nível de registro, não um
    # gabarito de colunas.
    "sap_procosa": (
        "data/clients/sap_procosa.XLSX",
        "data/reports/mapping_sap_procosa_full.xlsx",
        None,
    ),
    "sap_terumo": (
        "data/clients/sap_terumo.XLSX",
        "data/reports/mapping_sap_terumo_full.xlsx",
        None,
    ),
}

#: Rótulo impresso de cada resultado. ``Outcome.value`` continua em inglês.
OUTCOME_LABELS: dict[Outcome, str] = {
    outcome: report_schema.ENUMS["outcome"][outcome.value] for outcome in Outcome
}

#: Início da linha de totais; a linha seguinte é recuada até depois dele.
_TOTAL_LABEL = "TOTAL COM GABARITO  "


def _pct(value: float | None) -> str:
    """Formata uma porcentagem com vírgula decimal e largura mínima de 6; usa ``n/d`` se ausente."""
    return "  n/d " if value is None else report_schema.format_percent_br(value).rjust(6)


def _decimal(value: float | None) -> str:
    """Formata um número com vírgula decimal, preservando os dígitos; usa ``n/d`` se ausente."""
    return "n/d" if value is None else report_schema.format_decimal_br(value)


def _build_parser() -> PortugueseArgumentParser:
    parser = PortugueseArgumentParser(prog="python -m mapeval", description=__doc__)
    parser.add_argument("--root", default=".", metavar="PASTA",
                        help="raiz do repositório com as planilhas")
    parser.add_argument("--client", action="append", metavar="CLIENTE",
                        help="limita a avaliação a este cliente; repita a opção para mais de um")
    parser.add_argument("--strict-key-only", action="store_true",
                        help="não aplica as quatro correções verificadas do gabarito")
    parser.add_argument("--show-losses", action="store_true",
                        help="lista cada acerto retido e cada linha perdida ou com coluna errada")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    root = Path(args.root)

    found = delivered = present = fabrications = 0
    for name in args.client or list(CLIENTS):
        # Um nome fora de CLIENTS ainda termina em KeyError; veja docs/KNOWN_ISSUES.md.
        client_file, report_file, key_file = CLIENTS[name]
        if key_file is None:
            fabrications += _report_unkeyed_client(name, root / client_file, root / report_file)
            continue

        key = load_answer_key(root / key_file, name,
                              apply_corrections=not args.strict_key_only)
        score = score_report(root / report_file, root / client_file, key)
        _report_keyed_client(name, score, key, show_losses=args.show_losses)
        found += score.count(Outcome.CORRECT)
        delivered += score.delivered_correct
        present += score.present
        fabrications += score.count(Outcome.FABRICATED)

    if present:
        _report_totals(found, delivered, present, fabrications)
    # Somente fabricações alteram o exit code. Escolhas fora das 56 colunas
    # cobertas pelo gabarito podem aumentar "sem_veredito" sem causar falha.
    # Essa contagem também precisa ser considerada ao avaliar uma mudança.
    return 1 if fabrications else 0


def _report_unkeyed_client(name: str, client_path: Path, report_path: Path) -> int:
    """Exibe os totais de um cliente sem gabarito e retorna a contagem de aceites em colunas vazias.

    Se o relatório não existir, retorna 0 sem exibir aviso.
    """
    if not report_path.exists():
        return 0
    print(f"\n{name}  SEM GABARITO — não é possível calcular precisão ou recall.")
    score = score_report(report_path, client_path, AnswerKey(name, {}))
    pending = sum(row.status == "review" for row in score.rows)
    print(f"  aceitos {len(score.accepted):3}   pendências de revisão {pending:3}"
          f"   aceites em coluna vazia {len(score.vacuous_targets)}")
    return len(score.vacuous_targets)


def _report_keyed_client(name: str, score: Score, key: AnswerKey, *, show_losses: bool) -> None:
    fabricated = score.count(Outcome.FABRICATED)
    # Os métodos saem como estão gravados no relatório em português.
    census = {
        report_schema.external_value("mappings", "method", method): count
        for method, count in method_census(score).items()
    }

    print(f"\n{name}  presentes={score.present}  (colunas anotadas no gabarito)")
    print(f"  CORRETO          {score.count(Outcome.CORRECT):3}      recall sobre presentes "
          f"{_pct(score.recall_on_present)}")
    print(f"  COLUNA_ERRADA    {score.count(Outcome.WRONG_COLUMN):3}")
    print(f"  PERDIDO          {score.count(Outcome.MISSED):3}")
    print(f"  FABRICADO        {fabricated:3}"
          + ("   <-- BLOQUEANTE" if fabricated else ""))
    print(f"  aceitos          {len(score.accepted):3}      precisão (limite inferior) "
          f"{_pct(score.accept_precision_lower)}")
    print(f"  ENTREGUES        {score.delivered_correct:3}      recall entregue "
          f"{_pct(score.delivered_recall)}   <-- o que o cliente recebe")
    if score.withheld_correct:
        print(f"  retidos          {len(score.withheld_correct):3}      coluna certa "
              "encontrada, pendente de revisão")
    print(f"  sem_veredito     {len(score.undetermined_accepts):3}      aceitos que o "
          "gabarito não consegue verificar")
    print(f"  aceites independentes de cabeçalho {_pct(score.header_independent_share)}"
          "   <-- métrica de generalização")
    print(f"  aceitos por método {census}")
    for source, targets in key.source_conflicts.items():
        labels = ", ".join(f"m{target}" for target in targets)
        print(f"  REVISAR GABARITO: c{source} está indicada para {labels}; "
              "a aplicação exige uma origem distinta por destino.")
    if key.corrections:
        print(f"  correções do gabarito aplicadas: {sorted(key.corrections)}")
    for row in score.vacuous_targets:
        print(f"    FABRICAÇÃO m{row.model_column} {row.model_name!r} <- c{row.client_column}"
              f" (0 linhas preenchidas, final={_decimal(row.final_confidence)})")
    if show_losses:
        _report_losses(score, key)


def _report_losses(score: Score, key: AnswerKey) -> None:
    """Lista primeiro os acertos pendentes de revisão, depois as perdas e escolhas de coluna erradas."""
    for row in score.withheld_correct:
        status = report_schema.external_value("mappings", "status", row.status)
        print(f"    {'retido':14} m{row.model_column:<4} "
              f"{row.model_name[:30]:30} <- c{row.client_column} "
              f"final={_decimal(row.final_confidence)} ({status})")
    for row in score.rows:
        if row.outcome in (Outcome.MISSED, Outcome.WRONG_COLUMN):
            want = key.present.get(row.model_column)
            chosen = "nenhuma" if row.client_column is None else row.client_column
            print(f"    {OUTCOME_LABELS[row.outcome]:14} m{row.model_column:<4} "
                  f"{row.model_name[:30]:30} escolhida={chosen} esperada=c{want}")


def _report_totals(found: int, delivered: int, present: int, fabrications: int) -> None:
    print(f"\n{_TOTAL_LABEL}encontrados {found}/{present} "
          f"({report_schema.format_percent_br(found / present)})   "
          f"ENTREGUES {delivered}/{present} "
          f"({report_schema.format_percent_br(delivered / present)})   "
          f"fabricações {fabrications}")
    if found != delivered:
        # A dica também aparece quando --show-losses já foi passado.
        print(f"{' ' * len(_TOTAL_LABEL)}{found - delivered} mapeamentos "
              "corretos encontrados, mas não entregues - rode com --show-losses para "
              "listá-los")


if __name__ == "__main__":
    sys.exit(main())
