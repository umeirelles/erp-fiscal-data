"""python3 -m mapeval — score the archived reports against the answer keys."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from mapeval.outcomes import Outcome
from mapeval.score import method_census, score_report
from mapeval.truth import load_answer_key

#: client -> (client export, mapping report, de-para key or None)
CLIENTS: dict[str, tuple[str, str, str | None]] = {
    "datasul": ("datasul_maio.xlsx", "mapping_datasul_full.xlsx", "de_para_datasul.xlsx"),
    "oracle": ("oracle.xlsx", "mapping_oracle_full.xlsx", "de_para_oracle.xlsx"),
    # de_para_procosa.xlsx is a record-granularity spec, not a column key.
    "sap_procosa": ("sap_procosa.XLSX", "mapping_sap_procosa_full.xlsx", None),
    "sap_terumo": ("sap_terumo.XLSX", "mapping_sap_terumo_full.xlsx", None),
}


def _pct(value: float | None) -> str:
    return "  n/a " if value is None else f"{value * 100:5.1f}%"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default=".", help="repo root holding the workbooks")
    parser.add_argument("--client", action="append", help="limit to these clients")
    parser.add_argument("--strict-key-only", action="store_true",
                        help="do not apply the four verified key corrections")
    parser.add_argument("--show-losses", action="store_true",
                        help="print every missed and wrong-column row")
    args = parser.parse_args(argv)

    root = Path(args.root)
    wanted = args.client or list(CLIENTS)

    total_correct = 0
    total_present = 0
    fabrications = 0

    for name in wanted:
        client_file, report_file, key_file = CLIENTS[name]
        if key_file is None:
            report = root / report_file
            if not report.exists():
                continue
            print(f"\n{name}  UNKEYED — no precision or recall figure is honest here.")
            from mapeval.score import _vacuous_client_columns  # noqa: PLC0415
            import openpyxl
            workbook = openpyxl.load_workbook(report, read_only=True, data_only=True)
            rows = list(workbook["Mappings"].iter_rows(values_only=True))
            workbook.close()
            column = {h: i for i, h in enumerate(rows[0])}
            accepted = [r for r in rows[1:] if r[column["status"]] == "accepted"]
            vacuous = _vacuous_client_columns(Path(root / client_file))
            bad = [r for r in accepted
                   if r[column["client_column_number"]] not in (None, "")
                   and int(r[column["client_column_number"]]) in vacuous]
            print(f"  accepted {len(accepted):3}   adjudication backlog {len(accepted):3}"
                  f"   vacuous accepts {len(bad)}")
            fabrications += len(bad)
            continue

        key = load_answer_key(root / key_file, name,
                              apply_corrections=not args.strict_key_only)
        score = score_report(root / report_file, root / client_file, key)

        correct = score.count(Outcome.CORRECT)
        total_correct += correct
        total_present += score.present
        fabrications += score.count(Outcome.FABRICATED)

        print(f"\n{name}  present={score.present}  (never 227)")
        print(f"  CORRECT          {correct:3}      recall on present {_pct(score.recall_on_present)}")
        print(f"  WRONG_COLUMN     {score.count(Outcome.WRONG_COLUMN):3}")
        print(f"  MISSED           {score.count(Outcome.MISSED):3}")
        print(f"  FABRICATED       {score.count(Outcome.FABRICATED):3}"
              + ("   <-- BLOCKING" if score.count(Outcome.FABRICATED) else ""))
        print(f"  accepted         {len(score.accepted):3}      precision (lower bound) "
              f"{_pct(score.accept_precision_lower)}")
        print(f"  header-independent accepts        {_pct(score.header_independent_share)}"
              "   <-- generalization metric")
        print(f"  method census    {dict(method_census(score))}")
        if key.corrections:
            print(f"  key corrections applied: {sorted(key.corrections)}")
        for row in score.vacuous_targets:
            print(f"    FABRICATION m{row.model_column} {row.model_name!r} <- c{row.client_column}"
                  f" (0 populated rows, final={row.final_confidence})")
        if args.show_losses:
            for row in score.rows:
                if row.outcome in (Outcome.MISSED, Outcome.WRONG_COLUMN):
                    want = key.present.get(row.model_column)
                    print(f"    {row.outcome.value:12} m{row.model_column:<4} "
                          f"{row.model_name[:30]:30} chose={row.client_column} want=c{want}")

    if total_present:
        print(f"\nKEYED TOTAL  {total_correct}/{total_present} "
              f"({total_correct / total_present * 100:.1f}%)   fabrications {fabrications}")
    return 1 if fabrications else 0


if __name__ == "__main__":
    sys.exit(main())
