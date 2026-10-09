"""python -m mapeval — avalia os relatórios arquivados contra os gabaritos."""

from __future__ import annotations

import sys
from pathlib import Path

import report_schema
from cli_ptbr import PortugueseArgumentParser
from mapeval.outcomes import Outcome
from mapeval.score import method_census, score_report
from mapeval.truth import load_answer_key

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
    """Porcentagem com vírgula decimal em 6 caracteres; ``n/d`` quando não há valor."""
    return "  n/d " if value is None else report_schema.format_percent_br(value).rjust(6)


def _decimal(value: float | None) -> str:
    """Número com vírgula decimal e os mesmos dígitos do Python; ``n/d`` quando não há valor."""
    return "n/d" if value is None else report_schema.format_decimal_br(value)


def main(argv: list[str] | None = None) -> int:
    parser = PortugueseArgumentParser(prog="python -m mapeval", description=__doc__)
    parser.add_argument("--root", default=".", metavar="PASTA",
                        help="raiz do repositório com as planilhas")
    parser.add_argument("--client", action="append", metavar="CLIENTE",
                        help="limita a avaliação a este cliente; repita a opção para mais de um")
    parser.add_argument("--strict-key-only", action="store_true",
                        help="não aplica as quatro correções verificadas do gabarito")
    parser.add_argument("--show-losses", action="store_true",
                        help="lista cada acerto retido e cada linha perdida ou com coluna errada")
    args = parser.parse_args(argv)

    root = Path(args.root)
    wanted = args.client or list(CLIENTS)

    total_correct = 0
    total_delivered = 0
    total_present = 0
    fabrications = 0

    for name in wanted:
        client_file, report_file, key_file = CLIENTS[name]
        if key_file is None:
            report = root / report_file
            if not report.exists():
                continue
            print(f"\n{name}  SEM GABARITO — nenhum número de precisão ou recall seria honesto aqui.")
            from mapeval.score import _vacuous_client_columns, read_mappings  # noqa: PLC0415
            rows, column, schema = read_mappings(report)
            accepted = [
                r for r in rows[1:]
                if report_schema.internal_value("mappings", "status", r[column["status"]], schema)
                == "accepted"
            ]
            vacuous = _vacuous_client_columns(Path(root / client_file))
            bad = [r for r in accepted
                   if r[column["client_column_number"]] not in (None, "")
                   and int(r[column["client_column_number"]]) in vacuous]
            print(f"  aceitos {len(accepted):3}   pendências de revisão {len(accepted):3}"
                  f"   aceites em coluna vazia {len(bad)}")
            fabrications += len(bad)
            continue

        key = load_answer_key(root / key_file, name,
                              apply_corrections=not args.strict_key_only)
        score = score_report(root / report_file, root / client_file, key)

        correct = score.count(Outcome.CORRECT)
        total_correct += correct
        total_delivered += score.delivered_correct
        total_present += score.present
        fabrications += score.count(Outcome.FABRICATED)

        # Os métodos saem como estão gravados no relatório em português.
        census = {
            report_schema.external_value("mappings", "method", method): count
            for method, count in method_census(score).items()
        }

        print(f"\n{name}  presentes={score.present}  (nunca 227)")
        print(f"  CORRETO          {correct:3}      recall sobre presentes "
              f"{_pct(score.recall_on_present)}")
        print(f"  COLUNA_ERRADA    {score.count(Outcome.WRONG_COLUMN):3}")
        print(f"  PERDIDO          {score.count(Outcome.MISSED):3}")
        print(f"  FABRICADO        {score.count(Outcome.FABRICATED):3}"
              + ("   <-- BLOQUEANTE" if score.count(Outcome.FABRICATED) else ""))
        print(f"  aceitos          {len(score.accepted):3}      precisão (limite inferior) "
              f"{_pct(score.accept_precision_lower)}")
        print(f"  ENTREGUES        {score.delivered_correct:3}      recall entregue "
              f"{_pct(score.delivered_recall)}   <-- o que o cliente recebe")
        if score.withheld_correct:
            print(f"  retidos          {len(score.withheld_correct):3}      coluna certa "
                  "encontrada, retida pelo limiar de aceite")
        print(f"  sem_veredito     {len(score.undetermined_accepts):3}      aceitos que o "
              "gabarito não consegue verificar")
        print(f"  aceites independentes de cabeçalho {_pct(score.header_independent_share)}"
              "   <-- métrica de generalização")
        print(f"  censo de métodos {census}")
        if key.corrections:
            print(f"  correções do gabarito aplicadas: {sorted(key.corrections)}")
        for row in score.vacuous_targets:
            print(f"    FABRICAÇÃO m{row.model_column} {row.model_name!r} <- c{row.client_column}"
                  f" (0 linhas preenchidas, final={_decimal(row.final_confidence)})")
        if args.show_losses:
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

    if total_present:
        print(f"\n{_TOTAL_LABEL}encontrados {total_correct}/{total_present} "
              f"({report_schema.format_percent_br(total_correct / total_present)})   "
              f"ENTREGUES {total_delivered}/{total_present} "
              f"({report_schema.format_percent_br(total_delivered / total_present)})   "
              f"fabricações {fabrications}")
        if total_correct != total_delivered:
            print(f"{' ' * len(_TOTAL_LABEL)}{total_correct - total_delivered} mapeamentos "
                  "corretos encontrados, mas não entregues - rode com --show-losses para "
                  "listá-los")
    # A fabricação é o único bloqueio automático. Todo o resto precisa de uma
    # pessoa: o gabarito cobre 56 das 227 colunas do modelo, então uma mudança
    # que inventa mapeamentos fora dele faz subir "sem_veredito" e deixa este
    # número em zero. Leia aquela linha antes de confiar num código de saída 0.
    return 1 if fabrications else 0


if __name__ == "__main__":
    sys.exit(main())
