# Evaluation: how to tell whether a change helps

This page covers how the mapper is measured: what the ground truth is and where it falls short, how `mapeval` turns a report into numbers, today's baseline, why the 0.90 accept gate stays, and the steps to follow before you call a change an improvement. For how the mapper decides, see [ARCHITECTURE.md](ARCHITECTURE.md). For the workbooks themselves, see [DATA.md](DATA.md). Bugs and code/comment mismatches are collected in [KNOWN_ISSUES.md](KNOWN_ISSUES.md).

Every number on this page was measured on 2026-10-08 against the files in `data/`. Run all commands from the repo root. Nothing here calls OpenAI unless it says so.

```bash
python -m mapeval                                   # score all four archived reports (about 10 s)
python -m mapeval --show-losses                     # also list every withheld, missed and wrong-column row
python -m mapeval --client oracle                   # one client; repeat --client for more
python -m mapeval --strict-key-only                 # score against the keys exactly as the client wrote them
python -m pytest -q                                 # 137 tests, offline, about 7 s
```

## Ground truth and its limits

There are two sources of truth, and neither covers much.

### 1. Answer keys (de-para files)

A *de-para* ("from-to") is a correspondence table. Two clients sent one: a copy of the base model in which they wrote, above each model column they can supply, the letter of their own column that supplies it (`Coluna FY`).

| Client | Key | Keyed model columns |
|---|---|---|
| datasul | `data/keys/de_para_datasul.xlsx` | 30 |
| oracle | `data/keys/de_para_oracle.xlsx` | 26 |
| **total** | | **56** |

`mapeval/truth.py` `load_answer_key` reads only row 1. A cell's position gives the model column number, and `mapeval/excel.py` `parse_coluna_annotation` turns `Coluna FY` (trailing spaces allowed) into a client column number. Row 2 repeats the model headers and is not read. Rows 3 onward hold the client's own data under the annotated columns, and mapeval does not read those either.

`data/keys/de_para_procosa.xlsx` is not a key. Its row 1 says `ITEM`/`NOTA` (a record-granularity spec), so it yields no column pairs. sap_terumo has no key at all.

The keys have four limits.

- **They are small.** One column is 1.8 points of the 56-column keyed total and 3–4 points of a single client's figure. **Treat every recall number as ±2 columns.** A one- or two-column move on its own is not a signal. Look at which rows moved and why.
- **They are partly wrong.** In four rows the key is wrong and the mapper is right. `mapeval/truth.py` `KEY_CORRECTIONS` overrides them:

  | Client | Model column | Key says | Corrected to | Why |
  |---|---|---|---|---|
  | oracle | m213 Aliquota PIS | c37 | c36 | c37 is the COFINS rate. c36 is the PIS rate. |
  | oracle | m220 CST COFINS | c39 | c40 | The CST pair is transposed in the key. |
  | oracle | m221 CST PIS | c40 | c39 | Same transposition. |
  | datasul | m184 Aliquota COFINS | c99 | c188 | c188 matches the key's own m52–m55 tax block. |

  PIS and COFINS are federal contributions on revenue. CST is the tax-situation code each tax line carries. `--strict-key-only` turns the corrections off. Today they are worth exactly +4 delivered and +4 found (28 → 32 and 39 → 43), and `tests/test_mapeval.py` pins the found difference.
- **They say nothing about the other model columns.** A model column the client did not annotate scores as *undetermined*. The key cannot confirm that a `no_match` is right, and it cannot judge an accept outside its 56 columns.
- **They sometimes assign one client column to two model columns.** datasul c8 feeds both m39 Região and m147 Municipio Inicio. oracle c23 feeds both m100 Aliquota ICSP and m190 Aliquota ICMS (ICMS is the state VAT on goods). The mapper withdraws a client column from the pool once it is accepted, and `apply_invoice_mapping.py` refuses to deliver one client column twice. So at most 54 of the 56 keyed columns can be delivered under the current rules.

### 2. Reviewer verdicts

A reviewer can rule on a row in a report's `Mappings` sheet (`reviewer_status` = `correct`, `incorrect` with `correct_client_column`, or `no_mapping_exists`). Only one report has verdicts: `data/reports/mapping_sap_procosa_full.xlsx` holds 26 (15 `correct`, 2 `incorrect` with a corrected column, 9 `no_mapping_exists`). `apply_invoice_mapping.py` honours them. mapeval ignores them: it counts a row as delivered only when the mapper's own `status` is `accepted`. Its fabrication check has the same blind spot: it never inspects reviewer-confirmed rows (see [KNOWN_ISSUES.md: mapeval ignores reviewer verdicts](KNOWN_ISSUES.md#26-mapeval-ignores-reviewer-verdicts)).

## How mapeval scores a report

`python -m mapeval` is read-only. It never runs the mapper and writes no files. For each client in `mapeval/cli.py` `CLIENTS` it reads three workbooks under `--root`: the client export in `data/clients/`, the archived report `data/reports/mapping_<client>_full.xlsx` and, for datasul and oracle, the key in `data/keys/`.

`mapeval/score.py` `score_report` reads the report's `Mappings` sheet by **row position**: data row *n* is model column *n*. The `model_column_number` cell is not read, so a sheet someone sorted or filtered by hand is mis-scored without an error ([KNOWN_ISSUES.md](KNOWN_ISSUES.md#28-mapeval-matches-model-columns-by-position-never-by-name)). From each row it takes `model_column` (the name, for printing only), `client_column_number`, `status`, `method`, `final_confidence` and `arithmetic_proof`.

### Outcome classes

`mapeval/outcomes.py` `classify` assigns each model column one outcome. The checks run in this order, and the first one that matches wins:

| # | Outcome | Condition |
|---|---|---|
| 1 | `FABRICATED` | The row is `accepted` and its client column has zero populated cells. This is checked first, so it applies even when the key agrees. |
| 2 | `UNDETERMINED` | The model column is not in the key, whatever the mapper did. |
| 3 | `MISSED` / `CORRECTLY_ABSENT` | The mapper proposed no column. The result is `MISSED` if the key names one, otherwise `CORRECTLY_ABSENT`. |
| 4 | `FABRICATED` | A column was proposed but the key names none. |
| 5 | `CORRECT` / `WRONG_COLUMN` | The proposal equals the key's column, or it does not. |

"Zero populated cells" (vacuity) is computed by `mapeval/score.py` `_vacuous_client_columns`: no non-blank value under the header in the export's first sheet. A column of zeros is *not* vacuous, because an exempt operation legitimately has a zero base.

When `classify` is called from `score_report`, a keyed column always has an expected client column. That makes `CORRECTLY_ABSENT` and check 4 reachable only from unit tests. In practice every column outside the key is `UNDETERMINED`: datasul has 197 and oracle 201.

`review` rows are classified as well. A `review` proposal onto an empty column is not `FABRICATED`, because apply does not deliver it (unless run with `--include-review`). It falls through to the normal checks. `Score.vacuous_proposals` counts these (datasul 6, oracle 0), but the CLI does not print them.

### What each figure means

| Printed line | Definition |
|---|---|
| `CORRECT` / recall on present | **Found.** Rows whose proposal (accepted *or* review) matches the key, divided by the keyed columns. This measures retrieval. |
| `DELIVERED` / delivered recall | `CORRECT` rows whose `status` is `accepted`, divided by the keyed columns. This is what apply puts in the customer's workbook, and **it is the number that describes the product.** |
| `withheld` | `CORRECT` rows that are not `accepted`: the mapper found the right column and the gate held it back. `--show-losses` lists them, highest confidence first. |
| `accepted` / precision (lower bound) | `CORRECT` accepts divided by all accepts. Unadjudicable accepts count as wrong, so a new unverified accept lowers the bound. |
| `unadjudicable` | Accepts on model columns outside the key. The key cannot say whether they are right. |
| `header-independent accepts` | The generalization metric, using mapeval's definition (see [below](#the-generalization-metric)). |
| `method census` | Accepts by `method`: `name` is a pass-1 header accept, `data` a pass-2 decision, `catalog` a pass-2 decision by a distinctive official code table. |
| `key corrections applied` | Which `KEY_CORRECTIONS` rows were used. |
| `KEYED TOTAL` | Found and delivered summed over the keyed clients, plus total fabrications. |

The denominator is always the number of keyed columns (`AnswerKey.denominator`). Never divide by 227. The base model is a deliberate superset, and most of its columns are legitimately absent from any one client.

### Fabrication and the exit code

`main` in `mapeval/cli.py` returns 1 if any fabrication was found and 0 otherwise. Fabrications are keyed `FABRICATED` rows plus "vacuous accepts" on unkeyed clients. Fabrication is the failure that matters: a fabricated `Base CBS` is far worse than an empty one. (CBS is the new federal consumption tax from the tax reform; `Base CBS` is its taxable base.)

This is the only automatic block. An exit code of 0 does **not** mean a change is safe. A change that adds wrong accepts on populated columns outside the key raises `unadjudicable` and lowers the precision bound, but the exit code stays 0. Read those two lines before trusting a clean exit.

The exit path was checked on 2026-10-08 with a scratch copy of the datasul report in which one `review` row onto an empty column was marked `accepted`. Scoring the copy printed `FABRICATED 1 <-- BLOCKING` and a `FABRICATION m32 ... (0 populated rows ...)` line, and exited 1.

### Unkeyed clients

For sap_procosa and sap_terumo, mapeval prints no precision or recall figure:

```text
sap_procosa  UNKEYED — no precision or recall figure is honest here.
  accepted  45   adjudication backlog  45   vacuous accepts 0
```

`accepted` counts rows with `status == "accepted"`. `vacuous accepts` counts accepted rows whose client column is empty, and those count as fabrications for the exit code. The "adjudication backlog" is the accepted count printed a second time. It does not count review rows or reviewer verdicts (see [KNOWN_ISSUES.md](KNOWN_ISSUES.md#25-mapeval-prints-the-accept-count-as-the-adjudication-backlog)).

### Flags

| Flag | Effect |
|---|---|
| `--client NAME` | Score only this client. Repeat it for more. The name must be a key of `CLIENTS` (`datasul`, `oracle`, `sap_procosa`, `sap_terumo`). Any other name stops with a `KeyError` traceback. |
| `--show-losses` | For keyed clients, list every withheld row (with `final_confidence` and status), then every `MISSED` and `WRONG_COLUMN` row with the column chosen and the column wanted. |
| `--strict-key-only` | Skip `KEY_CORRECTIONS`. |
| `--root DIR` | Resolve every workbook path in `CLIENTS` against `DIR` instead of `.`. Use this to score candidate reports without touching `data/reports/` (see [the protocol](#protocol-for-a-proposed-change)). If you run from outside the repo, also set `PYTHONPATH` to the repo. |

When a report file is absent, an unkeyed client is skipped silently, but a keyed client stops with `FileNotFoundError`. When you point `--root` at a directory that holds only some reports, name those clients with `--client`.

A full run takes about 10 s. Almost all of that is the vacuity scans of the two large exports, datasul (8.9 MB, about 5 s) and sap_procosa (9.8 MB, about 4.5 s). Each scan is cached within one process.

## Baseline (measured 2026-10-08)

Produced by `python -m mapeval` and `python -m mapeval --show-losses` on the archived online reports:

| Client | Keyed | **Delivered** | Found | Withheld | Wrong column | Missed | Fabricated | Accepted | Precision (lower bound) | Unadjudicable |
|---|---|---|---|---|---|---|---|---|---|---|
| datasul | 30 | **17 (56.7%)** | 24 (80.0%) | 7 | 1 | 5 | 0 | 19 | 89.5% | 2 |
| oracle | 26 | **15 (57.7%)** | 19 (73.1%) | 4 | 1 | 6 | 0 | 18 | 83.3% | 3 |
| **keyed total** | 56 | **32 (57.1%)** | 43 (76.8%) | 11 | 2 | 11 | 0 | 37 | 86.5% | 5 |

Unkeyed: sap_procosa 45 accepted, sap_terumo 67 accepted, 0 vacuous accepts on either. Exit code 0.

With `--strict-key-only`, the keyed total is delivered 28/56 (50.0%) and found 39/56 (69.6%).

None of the 37 keyed accepts is `WRONG_COLUMN`, so precision on the accepts the key *can* check is 32/32. The 5 that keep the lower bound below 100% are the unadjudicable ones.

### The 11 withheld-correct rows

Every one is a `review` row below 0.90. Five sit at exactly 0.8999. That value is not a model hedge. In pass 2, when neither the header nor objective data evidence is decisive, `map_columns` in `invoice_mapper.py` caps `final_confidence` at `final_threshold - 0.0001`, so the gate can never accept a row from that branch.

| Client | Model column | Proposed | final_confidence |
|---|---|---|---|
| datasul | m45 Outras ICMS | c16 | 0.8999 |
| datasul | m50 Isentas IPI | c19 | 0.8999 |
| datasul | m51 Outras IPI | c20 | 0.8999 |
| datasul | m52 Base PIS | c181 | 0.8999 |
| datasul | m210 Valor Total do Item | c90 | 0.891 |
| datasul | m44 Isentas ICMS | c15 | 0.8701 |
| datasul | m30 Data de lançamento | c3 | 0.8612 |
| oracle | m210 Valor Total do Item | c20 | 0.8999 |
| oracle | m195 Descrição | c13 | 0.8823 |
| oracle | m34 CFOP | c16 | 0.8547 |
| oracle | m31 Nº de documento | c8 | 0.8339 |

IPI is the federal tax on manufactured goods. *Isentas* and *Outras* are a tax's exempt and other (untaxed) amounts. CFOP is the 4-digit code for the type of operation.

### Other losses

- **datasul.** Missed: m31 Nº de documento (c4), m39 Região (c8), m147 Municipio Inicio (c8), m194 Nº item do documento (c67), m211 Nome do Fornecedor do Local de Início (c6). Wrong column: m213 Aliquota PIS, which proposed c96 where the key wants c187 (`review`, 0.8999).
- **oracle.** Missed: m30 Data de lançamento (c2), m37 Nome (c1), m99 Base ICSP (c28), m100 Aliquota ICSP (c23), m101 Valor ICSP (c24), m211 Nome do Fornecedor do Local de Início (c7). Wrong column: m194 Nº item do documento, which proposed c15 where the key wants c12 (`review`, 0.8752).

### The 5 unadjudicable accepts

- datasul: m161 Despesas ← c76, m165 Código do Item ← c69.
- oracle: m41 Base ICMS ← c28, m42 Valor ICMS ← c24, m199 Quantidade ← c17.

The oracle pair needs a human. The client's own headers for c28 and c24 are `BASE ICMS` and `VALOR ICMS`, and the mapper accepted them onto the ICMS slots. The key puts those same columns in the model's ICSP slots (m99, m101), which therefore score `MISSED`. mapeval scores the two sides separately and does not link them ([KNOWN_ISSUES.md](KNOWN_ISSUES.md#27-accepts-outside-the-key-are-not-checked-against-the-key-oracle-m41m42)). ICSP is a separate tax family in the model, and the mapper's family-conflict guard deliberately refuses to match an ICMS header to an ICSP column. Do not "fix" this with a rule that maps ICMS headers to ICSP. That would gain key points on one client by encoding its vocabulary, which is exactly the hand-tuning the ground rules forbid (the docstring of `Score.undetermined_accepts` in `mapeval/score.py` gives this exact example).

## The generalization metric

The four sample clients are reference material. Production will see ERPs nobody here has seen. The question that predicts the next client is how many accepts rest on evidence other than the client's header text. This page calls that share of accepts the generalization metric. Raising it is a goal in its own right. Two definitions exist in the code, and they disagree:

- **mapeval** (`mapeval/score.py` `Row.header_independent`, printed as `header-independent accepts`). An accept counts if its `arithmetic_proof` is non-empty and does not contain "does not single out" (a proof that two disjoint tax blocks both satisfy is not evidence for either), or if its `method` is `catalog`.
- **mapping_steps** (`mapping_steps.py`). Every evidence step has a `transfers` flag. `profile`, `identifier`, `catalog` and `arithmetic` transfer. `resolver`, `lexical` and `semantic` do not. A report's `decided_by` is the strongest supporting step in the order `resolver < lexical < semantic < profile < identifier < catalog < arithmetic` (`deciding_step`). The metric is the share of accepts whose `decided_by` is in `TRANSFERRING_STEP_IDS`. A related helper, `rests_only_on_headers`, asks whether `supported_by` names at least one evidence step and no transferring one.

The two differ when a pass-1 header accept is *corroborated* by a catalog. mapping_steps counts it as transferring (`decided_by = catalog`). mapeval does not (`method = name`, no arithmetic proof). Measured on the accepted rows of the current reports:

| Client | Accepts | mapeval definition | `decided_by` transfers |
|---|---|---|---|
| datasul | 19 | 7 (36.8%), printed | 8 (42.1%). Differs on m204 Nº do NCM (`semantic + catalog`) |
| oracle | 18 | 6 (33.3%), printed | 6 (33.3%) |
| sap_procosa | 45 | not printed; 0, because the report has no `arithmetic_proof` column | 17 (37.8%) |
| sap_terumo | 67 | not printed; 15 (22.4%) | 16 (23.9%). Differs on m34 CFOP |

NCM is the 8-digit product classification code. Some accepts are labelled `decided_by = unrecorded` (datasul 2, sap_procosa 3, sap_terumo 1). Objective evidence carried them, but the backfilled report cannot say which step it was. Both definitions count them as not transferring. Until the two definitions are unified, quote both and say which is which. See [KNOWN_ISSUES.md: two definitions of the generalization metric](KNOWN_ISSUES.md#24-two-definitions-of-the-generalization-metric).

When two changes recover the same column, prefer the one that rests on transferring evidence: tax arithmetic (base × rate = value), official code catalogs, or check digits on CPF/CNPJ (individual and company taxpayer IDs) and NF-e access keys (the 44-digit key of an electronic invoice). Avoid one that adds header vocabulary.

## Why the accept gate stays at 0.90

The withheld rows invite the obvious move: lower the gate. The table below shows what that would ship. It is a what-if on the archived scores. At gate *g*, a row ships if it is accepted today, or if it is a `review` proposal with *g* ≤ `final_confidence` < 0.90. Review rows at or above 0.90 are held by a guard, not by the gate, so they are left out. There are 5 today, all pointing at empty client columns. Rows are then checked against the keys with the corrections applied.

**Measured 2026-10-08. Keyed clients (datasul + oracle):**

| Gate | Rows that would ship | Key-correct | Key-wrong | Not adjudicable by the key | Client column claimed twice |
|---|---|---|---|---|---|
| 0.90 (today) | 37 | 32 | 0 | 5 | 0 |
| 0.89 | 59 | 38 | 1 | 20 | 3 |
| 0.86 | 114 | 41 | 2 | 71 | 27 |
| 0.83 | 158 | 43 | 2 | 113 | 55 |

"Claimed twice" counts shipped rows that reuse a client column another shipped row of the same client already uses.

**Unkeyed clients (sap_procosa + sap_terumo)**, which nobody can adjudicate. Rows that would ship: 112 / 180 / 223 / 253 at the same four gates, with 0 / 11 / 28 / 45 claimed twice. Of those rows, 0 / 3 / 8 / 10 point at a client column with no data, so they would be fabrications. On the keyed side, one such row appears at 0.83.

Read it this way. Going to 0.83 recovers all 11 withheld-correct columns (32 → 43 delivered). It also ships 121 more keyed rows, of which 2 are known wrong and 108 no one can check. On the unkeyed clients it ships 141 more unverifiable rows, 10 of them onto empty columns. Precision on the rows the key can check falls from 32/32 to 43/45. Where two shipped rows claim the same client column, at most one can be right. The gate currently delivers nothing the keys can show to be wrong. That is worth more than 11 columns.

Re-running the mapper with `--final-threshold 0.83` is a different experiment. The 0.8999 cap is `final_threshold - 0.0001`, and objective evidence counts as decisive at `final_threshold`. A lower flag value therefore moves the cap and the evidence bar as well as the line rows must cross. It also needs an online run.

**The unlock is reviewer adjudication, not a looser constant.** sap_procosa is the measured proof. 26 verdicts produced 17 more delivered columns: 15 confirmed review proposals and 2 corrected rows. Apply now delivers 62 columns (45 accepted + 17 reviewer-confirmed). An apply run into a scratch file on 2026-10-08 printed `mapped_columns=62`. The `Adjudication Queue` sheet ranks the withheld proposals for exactly that work. Reports written by the current code have it; sap_procosa's older report does not.

mapeval has no flag for this curve. Re-measure it with the snippet below before anyone proposes a threshold change. It reads the archived reports and takes about 10 s.

```bash
python - <<'EOF'
from collections import Counter

import openpyxl

from mapeval.cli import CLIENTS
from mapeval.score import _vacuous_client_columns
from mapeval.truth import load_answer_key

def mappings(report):
    book = openpyxl.load_workbook(report, read_only=True)
    rows = list(book["Mappings"].iter_rows(values_only=True))
    book.close()
    col = {h: i for i, h in enumerate(rows[0])}
    return [(m, r[col["client_column_number"]], r[col["status"]], r[col["final_confidence"]] or 0.0)
            for m, r in enumerate(rows[1:], start=1)]

for gate in (0.90, 0.89, 0.86, 0.83):
    keyed, unkeyed = Counter(), Counter()
    for name, (client, report, key_file) in CLIENTS.items():
        key = load_answer_key(key_file, name).present if key_file else None
        empty = _vacuous_client_columns(client)
        tally = keyed if key else unkeyed
        ships = [(m, int(c)) for m, c, status, final in mappings(report)
                 if status == "accepted" or (status == "review" and c is not None and gate <= final < 0.90)]
        tally["ships"] += len(ships)
        tally["claimed twice"] += len(ships) - len({c for _, c in ships})
        tally["empty column"] += sum(c in empty for _, c in ships)
        if key:
            tally["right"] += sum(key.get(m) == c for m, c in ships)
            tally["wrong"] += sum(m in key and key[m] != c for m, c in ships)
            tally["not in key"] += sum(m not in key for m, _ in ships)
    print(f"{gate:.2f}  keyed {dict(keyed)}  unkeyed {dict(unkeyed)}")
EOF
```

## Protocol for a proposed change

The ground rules in [README.md](../README.md#ground-rules) apply. In short: never tune against the four samples, no code may branch on a client name, the keys are a regression test and never a training set, and accept precision outranks recall.

### 1. Run the tests and record the current numbers

```bash
python -m pytest -q
python -m mapeval --show-losses > /tmp/mapeval-before.txt
```

### 2. Produce baseline and candidate reports outside `data/reports/`

Never write candidate reports over the archived ones while you evaluate. mapeval reads fixed file names, so give each run its own root with the same layout:

```bash
run_all() {   # usage: run_all <root dir> [mapper flags...]
  root=$1; shift
  mkdir -p "$root/data/reports"
  ln -sf "$PWD/data/clients" "$PWD/data/keys" "$root/data/"
  for pair in datasul:datasul_maio.xlsx oracle:oracle.xlsx \
              sap_procosa:sap_procosa.XLSX sap_terumo:sap_terumo.XLSX; do
    python invoice_mapper.py "data/clients/${pair#*:}" "$@" \
      --output "$root/data/reports/mapping_${pair%%:*}_full.xlsx"
  done
}

BASE=/tmp/mapeval-base   # any directory outside the repo
CAND=/tmp/mapeval-cand

run_all "$BASE" --offline   # with the code BEFORE your change (e.g. after `git stash`)
run_all "$CAND" --offline   # with your change applied
python -m mapeval --root "$BASE"
python -m mapeval --root "$CAND"
```

These reports contain sample values from real client data. Keep the scratch roots on your machine and never commit them (see [README.md](../README.md#confidential-data)).

Compare like with like. The archived reports were made online. datasul, oracle and sap_terumo were regenerated on 2026-08-25. sap_procosa was generated on 2026-08-04 by older code (no `arithmetic_proof` column), so its 45 accepts are not a baseline for the current code. Always produce `BASE` with the unchanged code in the same mode as `CAND`.

### 3. Compare the rows, not only the totals

A change can lose one correct column and gain another, leaving the totals flat. This prints what moved on each client, using found and delivered pairs for keyed clients and accepted pairs for unkeyed ones:

```bash
python - "$BASE" "$CAND" <<'EOF'
import sys
from pathlib import Path

import openpyxl

from mapeval.cli import CLIENTS
from mapeval.outcomes import Outcome
from mapeval.score import score_report
from mapeval.truth import load_answer_key

base, cand = Path(sys.argv[1]), Path(sys.argv[2])

def accepted(root, report):
    book = openpyxl.load_workbook(root / report, read_only=True)
    rows = list(book["Mappings"].iter_rows(values_only=True))
    book.close()
    col = {h: i for i, h in enumerate(rows[0])}
    return {(m, int(r[col["client_column_number"]]))
            for m, r in enumerate(rows[1:], start=1) if r[col["status"]] == "accepted"}

def correct(root, client, report, key):
    score = score_report(root / report, root / client, key)
    found = {(r.model_column, r.client_column) for r in score.rows if r.outcome is Outcome.CORRECT}
    return found, found & accepted(root, report)

for name, (client, report, key_file) in CLIENTS.items():
    if not (base / report).exists() or not (cand / report).exists():
        continue
    if key_file is None:
        pairs = {"accepted": (accepted(base, report), accepted(cand, report))}
    else:
        key = load_answer_key(base / key_file, name)
        (f0, d0), (f1, d1) = correct(base, client, report, key), correct(cand, client, report, key)
        pairs = {"found": (f0, f1), "delivered": (d0, d1)}
    for label, (old, new) in pairs.items():
        print(f"{name:12} {label:9} {len(old):3} -> {len(new):3}"
              f"  lost {sorted(old - new)}  gained {sorted(new - old)}")
EOF
```

Pairs are `(model column, client column)`. Pass `.` as the first argument to compare against the archived reports.

### 4. Acceptance criteria

A change is an improvement only if all of these hold:

- **Fabrications stay at 0.** mapeval exits 0 on `CAND`.
- **Delivered does not drop** on either keyed client. Every pair it loses is explained. Found is secondary.
- **Unadjudicable accepts and the precision lower bound do not get worse without explanation.** Every new out-of-key accept is listed and checked by someone who reads Portuguese invoice headers.
- **No accepted-count regression on sap_procosa or sap_terumo.** These have no key, so a rising count is not proof of improvement either. Every newly accepted pair needs a look, and `vacuous accepts` must stay 0.
- **The generalization share does not fall**, under both definitions. If a gain comes only from header vocabulary, say so.
- **The move exceeds the noise.** A ±1–2 column change in the keyed total is not evidence by itself.
- **The tests pass**, except pins you deliberately update (see [below](#regenerating-archived-reports)).

### 5. Leave one client out

For each change, write down which client's rows motivated it. Then judge it only on the other three. A rule found by staring at datasul has to show its value on oracle's key, or at least do no harm on oracle, sap_procosa and sap_terumo. If it helps only the client that inspired it, it is a fit to that sample, so drop it. Justify a rule by something outside the samples: tax law, an official code table, a national standard such as a SPED field definition (SPED is the federal digital bookkeeping system). Do not open a de-para file to design a rule.

No executable code may name a client. Today every hit of this search is a comment or docstring that explains a rule:

```bash
grep -nEi 'datasul|oracle|procosa|terumo' invoice_mapper.py reference_catalog.py mapping_steps.py apply_invoice_mapping.py
```

### 6. Offline versus online

| | Offline (`--offline`) | Online (default) |
|---|---|---|
| Header scoring | lexical (`LexicalHeaderScorer`) | OpenAI embeddings |
| Pass-2 evidence (arithmetic, catalogs, identifiers, resolver) | **not used**. Every model column left after pass 1 becomes `review` on its best header candidate, so in practice there is no `no_match` (0 on all four clients) | used, with gpt-4o-mini as resolver |
| Reproducible | yes. Three runs of all four clients on 2026-10-08 produced identical `Mappings` sheets | not guaranteed: the resolver can churn `review` rows between runs (`backfill_mapping_steps.py` docstring) |
| Cost and data exposure | none | paid API calls. Every model and client header is embedded. Each model column that reaches the resolver sends sample values from the model and up to 8 from each candidate client column (`--sample-size`). The archived runs record 167–211 resolver answers per client. See [README.md](../README.md#confidential-data) |
| Runtime (measured 2026-10-08 on the development Mac) | datasul 36 s, oracle 17 s, sap_procosa 83 s, sap_terumo 12 s | not measured |

Offline A/B is valid only for changes to pass 1 (see [KNOWN_ISSUES.md](KNOWN_ISSUES.md#215-offline-mode-is-a-different-pipeline)): header normalization and lexical scoring, the pass-1 guards, and the catalog audit and tax-trio demotion that follow it. A pass-2 change needs online runs of both `BASE` and `CAND`. Because online runs are not reproducible, inspect the moved rows rather than trusting a one-column delta.

Offline baseline for the current code, measured 2026-10-08, **for offline A/B only**: keyed delivered 26/56, found 39/56, fabrications 0. sap_procosa 27 accepted, sap_terumo 47 accepted. Offline found means little: every model column gets a header-only guess, so `MISSED` is always 0 and misses show up as `WRONG_COLUMN`. Compare delivered and accepted, and **never quote offline numbers as the product figure.**

## Regenerating archived reports

When a change is accepted, regenerating `data/reports/` has two consequences.

**Reviewer decisions are lost.** `invoice_mapper.py` `write_results` writes every reviewer column blank and overwrites the output file without asking. There is no tool to carry verdicts from an old report to a new one. Without `--output`, the mapper writes `mapping_<client file stem>.xlsx` to the current directory (for example `mapping_datasul_maio.xlsx`), so you must pass `--output data/reports/mapping_<client>_full.xlsx` explicitly to replace an archived report. **Do not regenerate `mapping_sap_procosa_full.xlsx` in place:** it holds the 26 verdicts. `backfill_mapping_steps.py` is the precedent for adding columns to an existing report in place without re-running the mapper. It has known bugs (see [KNOWN_ISSUES.md](KNOWN_ISSUES.md#11-backfill-reads-shifted-cells-so-resolver-is-absent-from-supported_by) and [no safety checks](KNOWN_ISSUES.md#317-backfill_mapping_stepspy-has-no-safety-checks-and-no-tests)). The proposed fix for lost verdicts is in [KNOWN_ISSUES.md](KNOWN_ISSUES.md#33-regenerating-a-report-erases-reviewer-verdicts). After regenerating, re-run apply for the matching `data/mapped/` workbook (see [DATA.md](DATA.md)).

**Pinned tests fail by design.** `tests/test_mapeval.py` asserts today's absolute totals on the archived reports:

| Line | Assertion |
|---|---|
| 124 | `total == 43` (found) |
| 142–143 | corrections worth exactly 4, and `(strict, corrected) == (39, 43)` |
| 190–191 | `found - delivered == 11`, and `(found, delivered) == (43, 32)` |
| 203 | every withheld-correct row has `final_confidence < 0.90` |

A legitimate improvement must update these deliberately, in the same commit as the regenerated reports, with the before/after figures and the moved pairs in the commit message. Also update the baseline on this page and the status in the README. Two invariants should survive any improvement and need investigating, not editing, if they fail: corrections are worth exactly 4 (line 142; line 143 is an absolute pin you do update), and nothing delivered points at an empty column (`test_no_report_delivers_a_column_with_no_data`). The withheld-below-0.90 test (line 203) fails if a guard, rather than the gate, starts holding back a correct row. That case needs investigating too. Some comments carry older figures (`tests/test_mapeval.py:179` says 42/56 and 31; `mapeval/truth.py:21` says 33/56 and 37/56). The assertions are authoritative.

## Test suite

```bash
python -m pytest -q                              # all 137 tests
python -m pytest -q tests/test_mapeval.py        # one file
python -m pytest -q -k vacuous                   # by name
```

`pyproject.toml` sets `pythonpath = ["."]` and `testpaths = ["tests"]`. The suite ran in 6.6–6.9 s on 2026-10-08 (Python 3.13.1, pytest 8.4.1). About 5.5 s of that is the first datasul vacuity scan in `test_harness_reproduces_the_corrected_baseline`. No test touches the network: the OpenAI scorer and resolver tests inject fake clients, and the suite passes with `OPENAI_API_KEY` unset.

| File | Tests | Covers |
|---|---|---|
| `tests/test_invoice_mapper.py` | 76 | The mapper on synthetic tables: pass-1 acceptance and margins, column exclusivity, header normalization and conflict/role detection, fiscal arithmetic and tax-trio discovery, the vacuity guard, NF-e check digits and value profiles, the OpenAI scorer and resolver with fakes, the report's reviewer columns and Run Metadata, the unmatched-column and Adjudication Queue logic, and the step columns (including that an offline run never claims `semantic`). |
| `tests/test_mapeval.py` | 25 | Column-letter parsing, outcome precedence in `classify`, the non-discriminating-proof rule. 10 of them score the real archived reports and pin the baseline. They are skipped if `data/keys/de_para_oracle.xlsx` is absent. |
| `tests/test_mapping_steps.py` | 17 | The step registry: unique ids, deciding-step ranking, resolver ranked weakest and non-transferring, every step documented. |
| `tests/test_apply_invoice_mapping.py` | 12 | Apply on temporary workbooks: only accepted and confirmed rows are filled, `--include-review`, reviewer-status validation, the stale-input hash check, time values preserved, empty columns hidden, and the Coverage states. |
| `tests/test_reference_catalog.py` | 7 | Catalog loading from `reference_data/`, code normalization (leading zeros, ERP CFOP suffixes), distinct coverage, a distinctive catalog rescuing a bogus header as a `review` row, and the rule that short CST domains cannot decide alone. |

`backfill_mapping_steps.py` and `update_reference_data.py` have no tests.
