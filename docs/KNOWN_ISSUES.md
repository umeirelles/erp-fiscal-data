# Known issues

This list was compiled on 2026-10-08 against commit `88c503b` plus the uncommitted reorganization that moved every workbook under `data/`. That move changed the `--model` defaults of both CLIs and the paths in `mapeval/cli.py` and `tests/test_mapeval.py`. Line numbers refer to that state and will drift as soon as the code changes, so treat the symbol names as the stable reference. Every count below was re-measured on that state. The owner chose to document these issues rather than fix them before the handover. Each entry gives where the problem is, what happens, how to see it, what it affects and a suggested fix.

Background lives elsewhere: the pipeline in [ARCHITECTURE.md](ARCHITECTURE.md), the workbooks in [DATA.md](DATA.md), the harness and its figures in [EVALUATION.md](EVALUATION.md), and setup and the project's ground rules in the [README](../README.md). Statements that were wrong in the previous README are corrected there and are not repeated here. This file lists only their causes that are still in the code. Brazilian tax terms are explained where they first appear in the entries below.

## Before you fix anything

- Matcher changes must hold up leave-one-client-out. Never branch on a client name. The de-para keys (the client-written "from → to" column correspondence) are a regression test, not training data. See [EVALUATION.md](EVALUATION.md).
- Run both checks before and after a change. Both are offline:

  ```bash
  python -m pytest -q   # 137 tests pass
  python -m mapeval     # DELIVERED 32/56 (found 43/56), fabrications 0
  ```

  Several tests pin absolute totals (see 3.19), so an improvement can fail a test. When that happens, update the pin on purpose.
- Do not regenerate `data/reports/mapping_sap_procosa_full.xlsx` with the mapper. It holds the repo's only 26 reviewer verdicts, and regenerating it erases them (see 3.3).

## Index

| ID | Issue | Main location |
|---|---|---|
| **1** | **Affects committed deliverables or reports** | |
| 1.1 | Backfill reads shifted cells, so `resolver` is absent from `supported_by` | `backfill_mapping_steps.py` `backfill` |
| 1.2 | Coverage shows reviewer-rejected rows as "awaiting decision" | `apply_invoice_mapping.py` `_write_coverage_sheet` |
| 1.3 | Coverage leaves `client_column_number` blank on delivered rows | `apply_invoice_mapping.py` `_write_coverage_sheet` |
| **2** | **Can mislead a reviewer or a metric** | |
| 2.1 | `vetoed_by` only ever records two of the four guards | `invoice_mapper.py` `map_columns` |
| 2.2 | `decided_by = resolver` never happens in practice | `invoice_mapper.py` `_supporting_step_ids` |
| 2.3 | Backfill and live run credit catalog evidence differently | `backfill_mapping_steps.py`, `invoice_mapper.py` |
| 2.4 | Two definitions of the generalization metric | `mapeval/score.py`, `mapping_steps.py` |
| 2.5 | mapeval prints the accept count as the "adjudication backlog" | `mapeval/cli.py` `main` |
| 2.6 | mapeval ignores reviewer verdicts | `mapeval/score.py` `Score.delivered_correct` |
| 2.7 | Accepts outside the key are not checked against the key (oracle m41/m42) | `mapeval/outcomes.py` `classify` |
| 2.8 | mapeval matches model columns by position, never by name | `mapeval/score.py` `score_report` |
| 2.9 | mapeval computes values it never reports | `mapeval/score.py`, `mapeval/outcomes.py` |
| 2.10 | `method` names the pipeline branch, not the evidence | `invoice_mapper.py` `map_columns` |
| 2.11 | `catalog_distinct_coverage` is empty on pass-2 rows | `invoice_mapper.py` `map_columns` |
| 2.12 | Several review rows can propose the same client column | `invoice_mapper.py` `map_columns` |
| 2.13 | `final_confidence = 0.8999` is a clamp, not a measurement | `invoice_mapper.py` `map_columns` |
| 2.14 | `name_margin` can be negative on accepted rows | `invoice_mapper.py` `_name_score_context` |
| 2.15 | Offline mode is a different pipeline | `invoice_mapper.py` `map_columns` |
| **3** | **Robustness and data handling** | |
| 3.1 | API failures become review rows and the run exits 0 | `invoice_mapper.py` `map_columns` |
| 3.2 | Client values go to OpenAI unredacted | `invoice_mapper.py` `OpenAIDataResolver.resolve` |
| 3.3 | Regenerating a report erases reviewer verdicts | `invoice_mapper.py` `write_results` |
| 3.4 | The default report path is the current directory | `invoice_mapper.py` `main` |
| 3.5 | CSV output drops three sheets | `invoice_mapper.py` `write_results` |
| 3.6 | apply's stale-report check can be skipped | `apply_invoice_mapping.py` `_run_metadata` |
| 3.7 | Smaller apply input-handling issues | `apply_invoice_mapping.py` |
| 3.8 | CPF/CNPJ check digits fail on numeric cells | `invoice_mapper.py` `_cnpj_is_valid` |
| 3.9 | 'ICM', 'IS' and 'DIF_ALIQ' are not tax families | `invoice_mapper.py` `_TAX_FAMILIES` |
| 3.10 | Identifier evidence depends on the CPF/CNPJ mix in model.xlsx's samples | `invoice_mapper.py` `identifier_match_score` |
| 3.11 | 'Tipo de Imposto …' model columns get no catalog evidence | `reference_catalog.py` `expected_catalogs` |
| 3.12 | The catalog score ignores how many values are well-formed | `reference_catalog.py` `ReferenceCatalogs.analyze` |
| 3.13 | Expired cClassTrib codes still count | `reference_catalog.py` `from_directory` |
| 3.14 | The reference refresh is fragile | `update_reference_data.py` |
| 3.15 | An unknown manifest key crashes with a raw traceback | `reference_catalog.py` `from_directory` |
| 3.16 | No test covers a checksum mismatch | `tests/` |
| 3.17 | `backfill_mapping_steps.py` has no safety checks and no tests | `backfill_mapping_steps.py` |
| 3.18 | Role-word prefix expansion matches ordinary words | `invoice_mapper.py` `expand_truncated_role_words` |
| 3.19 | The mapeval CLI and tests are fragile | `mapeval/cli.py`, `tests/test_mapeval.py` |
| 3.20 | Setup quirks: `.env` and no lockfile | repo root |
| **4** | **Documentation and comment drift in the code** | |
| 4.1 | Stale baseline numbers in comments | `mapeval/truth.py`, `tests/test_mapeval.py`, `invoice_mapper.py` |
| 4.2 | `mapping_steps.py` names functions that do not exist | `mapping_steps.py` |
| 4.3 | Comments describe behaviour the code does not have | several |
| **5** | **Data quirks** | |
| 5.1 | Report layouts differ, and sap_procosa's still claims schema 2 | `data/reports/` |
| 5.2 | Answer-key row counts | `data/keys/` |
| 5.3 | `de_para_procosa.xlsx` is not a column key and is shifted by one | `data/keys/` |
| 5.4 | model.xlsx has 'Descrição' twice | `data/model.xlsx` |
| 5.5 | The datasul export's headers are space-padded | `data/clients/datasul_maio.xlsx` |

---

## 1. Affects committed deliverables or reports

### 1.1 Backfill reads shifted cells, so `resolver` is absent from `supported_by`

- **Where:** `backfill_mapping_steps.py`, `backfill`. The column index is built at line 89, `sheet.insert_cols` runs at 96, `stored()` (100–101) keeps using the old index, and `llm_final_confidence` is read at 119.
- **What happens:** `backfill` inserts the three step columns at an anchor, then reads the other fields through positions it recorded before the insert. Every field to the right of the anchor is therefore read three cells to the left of where it now sits.
  - In the datasul, oracle and sap_terumo reports, the anchor is `arithmetic_proof`. The read of `llm_final_confidence` lands on the newly inserted `vetoed_by` cell, which is still empty, so the resolver (the LLM step) is never credited.
  - In sap_procosa, the anchor is `reviewer_status`, which comes after the `llm_*` columns, so that report is correct.
  - Every other field the backfill reads (`status`, `method`, the scores and the catalog columns) sits left of the anchor and is read correctly.
- **Evidence:** compare rows that have a proposal and `llm_final_confidence > 0` with rows whose `supported_by` contains `resolver`. The counts are datasul 75 vs 0, oracle 32 vs 0, sap_terumo 78 vs 0 and sap_procosa 60 vs 60.

  ```bash
  python - <<'EOF'
  import pandas as pd
  for c in ["datasul", "oracle", "sap_procosa", "sap_terumo"]:
      m = pd.read_excel(f"data/reports/mapping_{c}_full.xlsx", sheet_name="Mappings")
      llm = pd.to_numeric(m["llm_final_confidence"], errors="coerce").fillna(0) > 0
      proposed = m["client_column_number"].notna()
      credited = m["supported_by"].fillna("").str.contains("resolver")
      print(c, int((llm & proposed).sum()), int(credited.sum()))
  EOF
  ```

  The error carries into `data/mapped/`, because apply copies `supported_by` into the Mapped Data banner. On 3 delivered datasul columns and 7 delivered sap_terumo columns, the resolver contributed to the choice, but the banner does not say so.
- **Impact:** across the three committed reports, 185 rows understate the resolver's role. `decided_by` and `vetoed_by` are unaffected: the resolver ranks lowest, so it never wins `decided_by`. The mapeval figures are also unaffected, since mapeval does not read `supported_by`.
- **Fix:** after the header loop at 97–98, rebuild `index` from row 1. This was tested on scratch copies of the pre-backfill reports (`git show 88c503b^:mapping_<client>_full.xlsx`). With that one line added, the backfill credits the resolver on exactly 75/32/60/78 rows, and every `decided_by` and `vetoed_by` cell is identical to the committed reports. To repair the committed files:
  1. Restore the three affected pre-backfill reports from `88c503b^`.
  2. Run the fixed backfill against `data/clients/…`.
  3. Re-run `apply_invoice_mapping.py` for those three clients.

  sap_procosa needs no repair.

### 1.2 Coverage shows reviewer-rejected rows as "awaiting decision"

- **Where:** `apply_invoice_mapping.py`, `_write_coverage_sheet`, lines 343–349. The state is derived only from the mapper's `status`, which `build_mapped_workbook` collects from `mapping["status"]` at 482–484. `reviewer_status` is never consulted.
- **What happens:** suppose the mapper left a row as `review` and a reviewer then marked it `no_mapping_exists`. That is a finished decision: the client's export does not have this field. Coverage still files the row under "awaiting decision".
- **Evidence:** the Coverage sheet in `data/mapped/sap_procosa_mapped.xlsx` lists 51 rows as "awaiting decision". Nine of them carry `no_mapping_exists` in the report, so only 42 are actually pending.

  ```bash
  python - <<'EOF'
  import pandas as pd
  cov = pd.read_excel("data/mapped/sap_procosa_mapped.xlsx", sheet_name="Coverage")
  rep = pd.read_excel("data/reports/mapping_sap_procosa_full.xlsx", sheet_name="Mappings")
  m = cov.merge(rep[["model_column_number", "reviewer_status"]], on="model_column_number")
  print(pd.crosstab(m["state"], m["reviewer_status"].fillna("(none)")))
  EOF
  ```

- **Impact:** reviewers are sent back to rows that are already decided, and for sap_procosa the "not in this client's export" count is 9 lower than what the reviewer established. The other three deliverables have no verdicts, so they are unaffected today.
- **Fix:** pass `reviewer_status` into `_write_coverage_sheet` and map `no_mapping_exists` to `STATE_ABSENT`. An adjudicated absence is a complete answer.

### 1.3 Coverage leaves `client_column_number` blank on delivered rows

- **Where:** `apply_invoice_mapping.py`, `_write_coverage_sheet`, line 358.
- **What happens:** on a delivered row, the number cell is written as `""` while `client_column` gets the display name. Pending and absent rows do get the proposed number.
- **Evidence:** every delivered row in the four committed deliverables has a blank number: datasul 19, oracle 18, sap_procosa 62 and sap_terumo 67. `client_column` is filled on all of them.
- **Impact:** the rows a downstream user most wants to trace back to the export lack the column number used everywhere else, so the reader has to match on the display name instead.
- **Fix:** write `column.index + 1` when `column is not None`.

---

## 2. Can mislead a reviewer or a metric

### 2.1 `vetoed_by` only ever records two of the four guards

- **Where:** `invoice_mapper.py`, `map_columns`, lines 3006–3010, which is the only place that writes the column. `backfill_mapping_steps.py` `backfill` (170–175) writes the same two labels. The guard registry is `mapping_steps.py` `GUARDS` (141–184).
- **What happens:** only two guards are ever written, and only on pass-2 rows: `vacuity`, which refuses an empty client column, and `family_conflict`, which refuses a tax-family or role conflict. `underspecified` and `exclusivity` are registered, but no code writes them.

  Several other refusal paths act as vetoes and leave no label at all:
  - pass-1 refusals (2297–2305): header conflict, underspecified header, empty column
  - the strong arithmetic contradiction in the pass-2 gate (2900–2908)
  - the catalog audit that reopens a pass-1 accept (`_audit_accepted_catalog_matches`, 1508–1520)
  - the arithmetic demotion (`_demote_inconsistent_tax_header_matches`, from 1607)
- **Evidence:** `grep -rn --include='*.py' -e '"underspecified"' -e '"exclusivity"' .` finds only the registry (`mapping_steps.py` 165 and 175). In the committed reports, the values are: datasul `vacuity` 6, sap_terumo `vacuity` 11, sap_procosa `family_conflict` 3, and none in oracle.
- **Impact:** a blank `vetoed_by` reads as "no guard fired", which is often false.
- **Fix:** give each refusal path a guard id and record it on the row it affects. For pass-1 refusals, carry the id into the pass-2 row. Alternatively, document that the column covers only those two guards.

### 2.2 `decided_by = resolver` never happens in practice

- **Where:** `invoice_mapper.py` `_supporting_step_ids`, lines 1228–1231; `mapping_steps.py` `deciding_step` (201–212) and the comment at 59–64.
- **What happens:** `decided_by` is the highest-ranked step in `supported_by`, and `resolver` ranks lowest. The header step (`semantic` or `lexical`) is added whenever `name_confidence > 0`.
  - Online, header scores are cosine similarities clipped to [0, 1] (1048), and they are never 0 for a proposed column.
  - Offline, the resolver does not run at all.

  As a result, a row that the LLM picked when the header was not decisive is reported as decided by the header.
- **Evidence:**
  - No row in any of the four reports has `decided_by = resolver`.
  - The lowest `name_confidence` on any proposed row is 0.7688 (sap_procosa).
  - 222 review rows (datasul 68, oracle 32, sap_procosa 51, sap_terumo 71) have a resolver pick and a header below the 0.90 threshold or the 0.05 margin, yet say `decided_by = semantic`.
- **Impact:** the meaning promised in `mapping_steps.py`, "nothing objective supported this and the model's judgment carried it", never appears, so a reviewer cannot tell header-decided rows from LLM-decided ones. The transferring share is unaffected, because neither step transfers.
- **Fix:** count the header step as deciding only when the header was decisive by itself (`name_is_decisive`, 2832–2836). Otherwise list it as corroborating, so that `resolver` can win when nothing objective supports the row.

### 2.3 Backfill and live run credit catalog evidence differently

- **Where:**
  - Backfill: `backfill_mapping_steps.py` `DISTINCTIVE_CATALOG_SCORE` (57), the catalog block (139–145) and the `explained_by_catalog` branch (158–167).
  - Live: `invoice_mapper.py` `catalog_evidence_is_distinctive` (1547–1566). Live pass-1 labels are built at 2322–2329, before `_audit_accepted_catalog_matches` (called at 2335) attaches catalog evidence.
- **What happens:** the two paths differ in three ways.
  1. **Threshold.** Backfill treats a catalog as deciding at a flat score of 0.90 or more. The live rule uses a 0.50 floor plus at least 5 distinct codes for the specificity-1.0 catalogs, and 0.90 for the rest. The specificity-1.0 catalogs are CFOP (the 4-digit fiscal operation code), NCM (the 8-digit Mercosur product code) and the IBGE municipality code.
  2. **The `explained_by_catalog` shortcut.** Backfill also credits a catalog as deciding whenever `data_confidence` equals `catalog_confidence`, whatever the catalog. That includes CST tables (tax-situation codes), which the live gate never treats as decisive, because their specificity of 0.75 or less keeps the score below 0.90.
  3. **Pass-1 accepts.** A live pass-1 accept never lists `catalog`, because its labels are computed before the audit adds catalog evidence. The backfill does list it.
- **Evidence:**
  - Committed sap_procosa row 176, 'Código de Sit Tributária do CBS e IBD' (CBS and IBS are the new federal and subnational consumption taxes of the 2026 reform), is a review row showing `decided_by = catalog` from `ibs_cbs_cst` at 0.75.
  - Committed datasul row 204, 'Nº do NCM', is a pass-1 accept (`method = name`) showing `decided_by = catalog`. A freshly generated report would say `semantic`.
- **Impact:** `decided_by`, and any metric built on it (2.4), changes when a report is regenerated, even if the evidence is identical.
- **Fix:** make both paths reach the same verdict:
  - In the backfill, recompute catalog evidence from the client file, as it already does for arithmetic (`ReferenceCatalogs.analyze` is offline and cheap), and apply `catalog_evidence_is_distinctive`.
  - Drop the `explained_by_catalog` shortcut.
  - In the live pass 1, recompute `supported_by`/`decided_by` after the audit.

### 2.4 Two definitions of the generalization metric

- **Where:**
  - mapeval: `mapeval/score.py` `HEADER_INDEPENDENT_METHODS` (16) and `Row.header_independent` (31–50), printed by `mapeval/cli.py` `main` (109–110) as the "generalization metric".
  - Step registry: `mapping_steps.py` `TRANSFERRING_STEP_IDS` (196) and `rests_only_on_headers` (224–231). Only the tests use these.
- **What happens:** the two files decide differently whether an accept would transfer to an unseen ERP.
  - mapeval counts an accept as header-independent only if it has a decisive `arithmetic_proof` or `method == "catalog"`.
  - The registry counts an accept as transferring when it rests on profile, identifier, catalog or arithmetic evidence.

  Neither counts `unrecorded` accepts. Those rest on objective evidence that the backfill could not name.
- **Evidence:**
  - Datasul: mapeval prints 36.8% (7 of 19 accepts). Counting accepts whose `decided_by` is a transferring step gives 8 of 19 (42.1%). The difference is row 204, 'Nº do NCM' (see 2.3).
  - Oracle: both give 6 of 18 (33.3%).
  - Accepts labelled `unrecorded` count as header-bound under both definitions: datasul 2, including 'Chave de acesso' ← 'Chave Nfe' with `data_confidence` 1.0; sap_procosa 3; sap_terumo 1.
- **Impact:** the figure the project is trying to drive up depends on which file you read, and both versions undercount.
- **Fix:** keep one definition. Compute the share in mapeval from `supported_by`/`decided_by` through `mapping_steps.TRANSFERRING_STEP_IDS`, after fixing 2.3. Also record `profile` and `identifier` separately in the mapper, so that `unrecorded` stops appearing in new reports.

### 2.5 mapeval prints the accept count as the "adjudication backlog"

- **Where:** `mapeval/cli.py`, `main`, lines 79–80.
- **What happens:** the line for unkeyed clients formats `len(accepted)` twice.
- **Evidence:** `python -m mapeval` prints `accepted  45   adjudication backlog  45` for sap_procosa and `67 / 67` for sap_terumo. The number happens to be right today, because no accepted row in either report has a reviewer verdict. Confirming an accept would not lower it.
- **Impact:** the backlog never goes down as reviewers work.
- **Fix:** count accepted rows whose `reviewer_status` is blank, and decide whether pending `review` rows belong in the backlog too.

### 2.6 mapeval ignores reviewer verdicts

- **Where:**
  - `mapeval/score.py` `Score.delivered_correct` (76–88). Its docstring says reviewer-confirmed rows count, but the code counts only `status == "accepted"`.
  - The `delivered=` flag in `score_report` (220).
  - The unkeyed branch of `mapeval/cli.py` `main` (74–78).
  - The rule mapeval should match is `apply_invoice_mapping.py` `_effective_columns` (251–274). It delivers `correct` rows and `incorrect` rows that carry a correction, and it drops `no_mapping_exists` rows, even accepted ones.
- **What happens:** mapeval treats the mapper's `status` as "delivered", but what apply actually ships also depends on the reviewer's verdicts.
- **Evidence:**
  - Datasul and oracle have no verdicts, so their keyed figures are unaffected today.
  - sap_procosa has 26 verdicts: 15 `correct`, 9 `no_mapping_exists` and 2 `incorrect`. Its deliverable carries 62 columns (45 accepted plus 17 confirmed), while mapeval's line for it shows only the 45 accepts.
  - The fabrication check has the same blind spot. It only inspects accepted rows (`score.py` 218–220, `cli.py` 76–78), and apply has no empty-column check of its own (`grep -n vacu apply_invoice_mapping.py` finds nothing). If a reviewer confirmed a proposal onto an empty client column, apply would ship a blank column and mapeval's exit code would not fail. No such row exists today.
- **Impact:** once datasul or oracle get verdicts, the DELIVERED figure will be wrong. And the only automatic block, the fabrication check, does not cover reviewer-confirmed rows.
- **Fix:** move apply's row-selection rule into a function that both apply and mapeval call, and score both delivery and fabrication from it.

### 2.7 Accepts outside the key are not checked against the key (oracle m41/m42)

- **Where:** `mapeval/outcomes.py` `classify` (61–67) returns `UNDETERMINED` for any model column the key does not cover; also `mapeval/score.py` `Score.undetermined_accepts` (120–134).
- **What happens:** the key says which client column feeds each model column it covers. Suppose an accept outside the key takes a client column that the key assigns to a different model column. That is a direct contradiction, but mapeval only checks the model-column side, so it never sees it.
- **Evidence:**
  - Oracle accepts m41 'Base ICMS' ← c28 'BASE ICMS' and m42 'Valor ICMS' ← c24 'VALOR ICMS'. ICMS is the state value-added tax.
  - The oracle key assigns c28 to m99 'Base ICSP' and c24 to m101 'Valor ICSP'. `python -m mapeval --client oracle --show-losses` lists m99 and m101 as `missed`, with `want=c28` and `want=c24`.
  - The two accepts are only counted in the "unadjudicable" line (3 for oracle), never listed, and the exit code is 0.
- **Impact:** one of two things is true:
  - the deliverable puts the columns the key calls ICSP under the model's ICMS columns, which is a wrong tax column (exactly what the fabrication rule exists to stop), or
  - the key is wrong for these two columns, and they belong in `KEY_CORRECTIONS`.

  The harness has the data to force that decision and does not.
- **Fix:** in `score_report`, give an accept whose client column the key assigns elsewhere its own outcome (for example `CONFLICTS_KEY`), print it, and decide whether it should block. Then settle m41/m42 from the client's data.

### 2.8 mapeval matches model columns by position, never by name

- **Where:** `mapeval/score.py` `score_report`, line 196 (row position in the Mappings sheet), and `mapeval/truth.py` `load_answer_key`, line 71 (position in de-para row 1).
- **What happens:** neither function reads `model_column_number` or compares names. apply, by contrast, reorders rows by number and checks every name (`_ordered_mapping`, 142–161).
- **Evidence:** today all four reports and both keys line up with model.xlsx: the numbers equal the positions, and no names differ.
- **Impact:** a Mappings sheet that has been filtered, sorted or edited by hand would be scored against the wrong key entries, with no error.
- **Fix:** index rows by `model_column_number` and assert that each name matches model.xlsx, as apply does.

### 2.9 mapeval computes values it never reports

- **Where:** `mapeval/score.py` `Score.vacuous_proposals` (62, 226–227) and `Score.correct_pairs` (143–151); `mapeval/outcomes.py` `REGRESSION_GUARDED` (29) and `classify` (61–72).
- **What happens:**
  - `vacuous_proposals`, the review proposals onto empty client columns, is computed but never printed. It is 6 for datasul and 0 for oracle.
  - The CLI uses neither `correct_pairs` nor `REGRESSION_GUARDED`.
  - In `classify`, the `CORRECTLY_ABSENT` result and the branch that maps `expected is None` to `FABRICATED` are reachable only from unit tests. `score_report` passes `in_key = position in key.present`, which already implies `expected` is set. Both arms of the `if not in_key` block return `UNDETERMINED`.
- **Impact:** low, but a reader may assume these values feed the score. `correct_pairs` was written to catch "lost one, gained one" regressions that an unchanged count hides, and nothing runs it.
- **Fix:** print `vacuous_proposals`. Either wire `correct_pairs` into a check against a stored baseline, or delete it together with `REGRESSION_GUARDED`. Collapse the dead branches.

### 2.10 `method` names the pipeline branch, not the evidence

- **Where:** `invoice_mapper.py` `map_columns`, line 2474 (`decision_method = "catalog" if catalog_decision is not None else "data"`), written to the row at 2987. mapeval reads `method` through `HEADER_INDEPENDENT_METHODS` and `method_census`.
- **What happens:** every pass-2 row gets `method = data`, or `catalog` when the local catalog decision fired, whatever evidence actually carried it. `method = catalog` does not imply the row was accepted.
- **Evidence:**
  - sap_terumo accepts 'Utilização de material' ← 'Material usage' (`data_confidence` 0.84) and 'Nº NF-e dos serviços' ← 'Service NF-e Number' (`data_confidence` 0.00). Both carry `method = data` and `decided_by = semantic`, because the header was decisive. NF-e is the Brazilian electronic invoice.
  - Oracle 'CFOP' ← 'CFO' carries `method = catalog` and `status = review` (final 0.8547, catalog score 0.8824).
- **Impact:** mapeval's method census, and anyone who filters on `method`, misreads these rows.
- **Fix:** document `method` as "the branch taken" and use `decided_by` to answer "what decided". Alternatively, rename the column in the next schema version (with a version bump; see 5.1).

### 2.11 `catalog_distinct_coverage` is empty on pass-2 rows

- **Where:** the field `MappingResult.catalog_distinct_coverage` (1265) is set only by `_audit_accepted_catalog_matches` (1504), and only for pass-1 accepts. The pass-2 `MappingResult(...)` call (2977–3028) never sets it, even though the pass-2 gate uses `selected_catalog_distinct_coverage` (2824, 2921).
- **Evidence:** comparing rows that have a `catalog_name` with rows where this column is filled gives datasul 4 vs 1, oracle 5 vs 4 and sap_terumo 7 vs 6. Every filled row is a pass-1 accept (`method = name`). The CFOP rows in datasul and oracle, both decided by the catalog, are blank. sap_procosa's report predates the column.
- **Impact:** on catalog-driven rows, a reviewer cannot see the number the gate actually used. The sheet shows only the row-weighted `catalog_coverage`, which can be much lower: for oracle 'CFO' it is 0.6673, against a distinct-code coverage of 0.8824.
- **Fix:** pass `catalog_distinct_coverage=round(selected_catalog_distinct_coverage, 4)` whenever `selected_catalog` is set.

### 2.12 Several review rows can propose the same client column

- **Where:** `invoice_mapper.py` `map_columns`. Only accepts leave the candidate pool (`available.remove` at 2331 and 3029–3030).
- **What happens:** a review proposal does not consume its client column. One client column can therefore be proposed for many model columns, including a column that a later model column accepts.
- **Evidence:**
  - Client columns proposed by more than one review row: datasul 26 (up to 6 times each), oracle 7 (up to 11), sap_procosa 14 (up to 8) and sap_terumo 12 (up to 7).
  - Review rows whose client column is accepted elsewhere: datasul 1, sap_terumo 2.
  - Example: datasul's 'Vlr Isen ICM' (exempt ICMS amount) is proposed for 'Isentas IBS', 'Isentas ICMS', 'Isentas ICAP', 'Isentas ICSP', 'Isentas ICMS_FECP' and 'IVA' (see also 3.9).
- **Impact:** reviewers see the same column offered again and again. Confirming two such rows, or one whose client column is already accepted, stops apply with "mapped more than once" (`_effective_columns`, 298–303).
- **Fix:** in the Adjudication Queue, flag client columns that are shared or already accepted. Consider dropping review proposals whose client column a later row accepted.

### 2.13 `final_confidence = 0.8999` is a clamp, not a measurement

- **Where:** `invoice_mapper.py` `map_columns`, lines 2888–2899. When neither the header nor the objective data is decisive, the value is clamped to `final_threshold - 0.0001`. The code acknowledges this at 3115–3117.
- **Evidence:** clamped rows: datasul 11, oracle 2, sap_procosa 14 and sap_terumo 33. Of the 11 keyed rows found correctly but withheld, five sit at 0.8999: datasul m45, m50, m51, m52 and oracle m210.
- **Impact:** in the Mappings sheet and in `mapeval --show-losses`, which sorts by this value, "0.8999" reads as "almost accepted". The Adjudication Queue already explains the clamp in `why_held` and breaks ties by margin; the other views do not.
- **Fix:** keep the clamp for the gate, but also write the unclamped value to its own column, for example `unclamped_confidence`.

### 2.14 `name_margin` can be negative on accepted rows

- **Where:** `invoice_mapper.py` `_name_score_context` (1807–1821), called with the chosen column at 2803–2805.
- **What happens:** the margin is the chosen column's header score minus the best alternative's. When pass 2 picks a column that is not the top header match, the margin is negative.
- **Evidence:** datasul 'CFOP' ← 'Cod. Fiscal' is accepted with `name_margin` -0.0582 (decided by catalog). sap_procosa has 3 accepted rows with a negative margin.
- **Impact:** on an accepted row, a negative margin looks like a failed margin test.
- **Fix:** document the column as "header lead of the chosen column over the best other column; negative when data overrode the header", or rename it.

### 2.15 Offline mode is a different pipeline

- **Where:** in `invoice_mapper.py` `map_columns`:
  - Client profiles are skipped (2360–2369).
  - Arithmetic relationship evidence is skipped (2376–2387).
  - Catalog evidence is still computed (2388–2395), then thrown away when the offline branch closes the target with `method = name_only`, `status = review` (2445–2460).
  - The local catalog decision (2470) is never reached.
- **What happens:** with `--offline`, only pass 1 can accept. Every pass-2 row becomes `review` with no `supported_by` or `decided_by`. The `data_error` rows of 3.1 (2493–2506) have the same blank labels.
- **Impact:** offline results understate what an online run delivers and must never be quoted as the product figure. The catalog computation is wasted.
- **Fix:** skip `_candidate_catalog_evidence` when `data_resolver is None`. Alternatively, let offline mode use `_unique_distinctive_catalog_decision`, which needs no API. Either way, fill `supported_by` on these rows.

---

## 3. Robustness and data handling

### 3.1 API failures become review rows and the run exits 0

- **Where:** `invoice_mapper.py` `map_columns`, lines 2492–2507 (`except Exception`). `main` returns 0 at 3389.
- **What happens:** the mapper catches any exception from the resolver call, per model column. That covers a quota or rate limit hit partway through the run, a network error or a response that fails validation. The row is recorded as `method = data_error`, `status = review`, with the exception text in `reason`. The run completes and exits 0. No test covers this path: `grep -n data_error tests/*.py` finds nothing.
- **Impact:** a quota that runs out or a connection that drops mid-run produces a report that looks normal, with every uncertain column marked "review", and nothing tells the operator the run is degraded. The embeddings call at the start of `map_columns` (2247) is not wrapped, so a key that is bad from the start stops the run there instead.
- **Fix:** catch only per-decision errors, such as response parsing or validation. Let authentication, quota and connection errors propagate. Exit non-zero, or at least print a count, whenever a `data_error` row was written.

### 3.2 Client values go to OpenAI unredacted

- **Where:** in `invoice_mapper.py`:
  - `OpenAIEmbeddingHeaderScorer._embeddings` (1026–1038) sends every model and client header.
  - `OpenAIDataResolver.resolve` (1099–1132) runs once for each uncertain model column. It sends that column's own samples from model.xlsx and a profile of each candidate client column.
  - The candidate list is the union of up to `candidate_count` (10) columns ranked by each of header, profile, arithmetic and catalog (`_rank_name_and_profile_candidates`, 1754–1804), so up to about 40 columns.
  - Each profile includes up to `sample_size` (8) raw values (`summarize_values`, `"samples"` at 690) plus the minimum, maximum and quartiles.
  - `store=False` is set (1135), but nothing is masked.
- **Impact:** in online mode, CNPJs and CPFs (company and individual taxpayer IDs), NF-e access keys (44-digit invoice IDs) and amounts leave the firm. The repo is shared internally on purpose, but each client must permit an online run before you do one. Offline mode sends nothing.
- **Fix:** mask values shaped like identifiers before building the payload. The profile already carries `valid_cpf_fraction`, `valid_cnpj_fraction` and `valid_nfe_key_fraction`, so the model loses little. Alternatively, omit `samples` for columns that validate as identifiers.

### 3.3 Regenerating a report erases reviewer verdicts

- **Where:** `invoice_mapper.py` `write_results` (3164–3206). The reviewer columns are always written blank (3173–3174), and the output file is overwritten without a check (3178).
- **What happens:**
  - Pointing `--output` at an existing report, for example `data/reports/mapping_sap_procosa_full.xlsx`, erases its reviewer columns. The default name (see 3.4) does not collide with the `*_full.xlsx` files, so this needs an explicit path.
  - There is no merge path. If a client file changes, apply rejects the old report on its hash, and the only way forward is a new report with blank reviewer columns.
- **Evidence:** `data/reports/mapping_sap_procosa_full.xlsx` holds the repo's only 26 reviewer verdicts.
- **Impact:** a single command erases adjudication work.
- **Fix:**
  - Refuse to overwrite an existing report unless `--force` is passed.
  - Add a merge step that carries `reviewer_status`, `correct_client_column` and `reviewer_note` over by `model_column_number`, whenever the proposed client column has not changed.

### 3.4 The default report path is the current directory

- **Where:** `invoice_mapper.py` `main`, line 3282.
- **What happens:** without `--output`, the report is written as `mapping_<client stem>.xlsx` in the working directory, not under `data/reports/`, and the file is not gitignored. Until this changes, pass `--output data/reports/…`.
- **Fix:** default to `data/reports/mapping_<stem>.xlsx`, together with the overwrite guard from 3.3.

### 3.5 CSV output drops three sheets

- **Where:** `invoice_mapper.py` `write_results`, lines 3175–3176.
- **What happens:** a `.csv` output path writes only the Mappings frame, so Run Metadata, Unmatched Client Columns and the Adjudication Queue are lost. apply reads reports with `pd.read_excel`, so a CSV report cannot be applied at all.
- **Fix:** drop CSV output, or write one CSV per sheet.

### 3.6 apply's stale-report check can be skipped

- **Where:** in `apply_invoice_mapping.py`:
  - `_run_metadata` (183–196) returns `{}` when the report has no Run Metadata sheet.
  - `_validate_input_hashes` (199–213) skips any hash that is not recorded.
  - `_ordered_mapping` (136) reads the first sheet instead of the "Mappings" sheet by name.
- **What happens:**
  - A report without Run Metadata, or without `client_sha256`/`model_sha256`, is applied with no staleness check.
  - `mapping_schema_version`, `prompt_version` and the recorded `client_sheet`/`*_header_row` settings are never compared with apply's own arguments.
  - A sheet inserted before Mappings, for example by hand in Excel, makes apply fail with a misleading error about required report columns. The writer warns about this in a comment at `invoice_mapper.py` 3185–3187.
- **Fix:** require Run Metadata and both hashes, compare the sheet and header-row settings, and read the `Mappings` sheet by name.

### 3.7 Smaller apply input-handling issues

All of these are in `apply_invoice_mapping.py`.

| Issue | Where | What happens | Fix |
|---|---|---|---|
| `status` compared without normalizing | `_effective_columns` 270, 274, 278; normalized for Coverage only at 482–484 | Suppose `status` is hand-edited to `Accepted ` (capital A, trailing space). The row is dropped from the data, and Coverage files it under "not in this client's export" | Normalize `status` once in `_ordered_mapping` |
| `--include-review` labels unconfirmed proposals "delivered" | `_write_coverage_sheet` 344–345; duplicate sources allowed at 296–298 | A scratch oracle run with `--include-review` labelled 37 review rows "delivered", and 30 delivered rows shared a client column with another delivered row | Add a separate state such as "delivered (unconfirmed)" |
| Numeric text in `correct_client_column` is read as a column number | `_corrected_column` 106–114 | A client header that is literally a number, such as '2024', cannot be referenced by name | Try a name match first, or require a prefix such as `#12` for numbers |

### 3.8 CPF/CNPJ check digits fail on numeric cells

- **Where:** `invoice_mapper.py` `_digits_only` (557–558), `_cpf_is_valid` (561–572) and `_cnpj_is_valid` (575–587). These are used by `_validation_fraction` (604–605), which feeds `identifier_match_score`.
- **What happens:** the validators require exactly 11 or 14 digits and never restore leading zeros. When an ERP exports a CNPJ as a number, the leading zero is lost. A float cell also gains a trailing `0` from its `.0`.
- **Evidence:**

  ```bash
  python -c "import invoice_mapper as im; v='01234567000195'; print(im._cnpj_is_valid(im._digits_only(v)), im._cnpj_is_valid(im._digits_only(int(v))))"
  # True False
  ```

  - As a float, the same value becomes the digits `12345670001950`.
  - A column of such integers scores `valid_cnpj_fraction` 0.0 instead of 1.0.
  - The resolver prompt warns the LLM about lost leading zeros (1171–1173), but the local code does not apply that rule.
- **Impact:** identifier evidence, which is among the most transferable kinds, silently disappears for ERPs that store IDs as numbers.
- **Fix:** convert int and float cells to an integer string, then try `zfill(11)` and `zfill(14)`. Leave text cells as they are. A 44-digit NF-e key stored as a float has already lost precision and cannot be recovered.

### 3.9 'ICM', 'IS' and 'DIF_ALIQ' are not tax families

- **Where:** `invoice_mapper.py` `_TAX_FAMILIES` (170–187) and `_TAX_FAMILY_ALIASES` (163–168). These are read by `header_tax_families` (190–196), which feeds `semantic_header_conflict` (378) and `tax_calculation_scope` (894–905).
- **What happens:** three headers produce no tax family:
  - 'ICM', Datasul's truncation of ICMS
  - 'IS', the Imposto Seletivo excise tax created by the CBS/IBS reform
  - 'DIF_ALIQ', the ICMS rate differential

  Without a family, `family_conflict` cannot fire, and the column never joins an arithmetic trio scope (base × rate = value).
- **Evidence:**

  ```bash
  python -c "from invoice_mapper import header_tax_families as f; print([sorted(f(h)) for h in ('Vlr Isen ICM', 'Valor IS', 'Base DIF_ALIQ', 'Isentas ICMS')])"
  # [[], [], [], ['icms']]
  ```

  - `semantic_header_conflict` returns `None` between 'Vlr Isen ICM' and every one of the 17 'Isentas …' model columns, which is why datasul proposes it for five of them, and for 'IVA' (2.12).
  - The 14 model columns in the IS and DIF_ALIQ blocks get no family and no trio scope.
- **Impact:** arithmetic, the strongest transferable evidence, and the main cross-tax guard are both unavailable for these columns.
- **Fix:**
  - Alias `icm` to `icms`.
  - Map the DIF_ALIQ block to `icms` with the `dif` qualifier, which `tax_calculation_scope` already knows.
  - Add `is` as a family, guarded against the English word `is`.
  - Validate the change leave-one-client-out.

### 3.10 Identifier evidence depends on the CPF/CNPJ mix in model.xlsx's samples

- **Where:** `invoice_mapper.py` `identifier_match_score` (777–791). For each identifier kind, it requires at least 80% valid values on both sides: the client column, and the model column's own sample rows in model.xlsx.
- **What happens:** a client column earns identifier evidence only for the kind that dominates the model's sample rows.
  - 'Nº ID fiscal 1' has 209 sample values: 91.4% are valid CPFs and 8.6% valid CNPJs. Only a client column made mostly of CPFs can match it.
  - 'Chave de acesso' samples are 100% valid NF-e keys.
  - No other model column's samples reach the 80% bar. The next highest is 'Nome do Fornecedor do Local de Início', at 1.9% valid CPFs.
- **Evidence:** against 'Nº ID fiscal 1', a scratch column of 50 valid CNPJs scores `identifier_match_score` 0.0, and a column of 50 valid CPFs scores 0.914.
- **Impact:** whether a client's taxpayer-ID column earns this transferable evidence depends on the mix of people and companies in the model's sample rows, not on the client's data. `mapping_steps.py` 106–108 promises more ("whatever it is called").
- **Fix:** model.xlsx stays as it is. Treat CPF and CNPJ as one "taxpayer ID" kind, or declare each model column's identifier kind in code, keyed by the model header, and score the client side alone.

### 3.11 'Tipo de Imposto …' model columns get no catalog evidence

- **Where:** `reference_catalog.py` `expected_catalogs` (24–59), in particular the clause `"tipo imposto" in normalized` at line 45.
- **What happens:** the catalogs to test are chosen from the model header. The 14 model columns named 'Tipo de Imposto <tax>' normalize to 'tipo de imposto …', so the clause matches no model header, and none of those columns gets a catalog. The mapper itself treats these columns as tax-status columns (`header_semantic_role`; see `test_a_family_name_does_not_smuggle_a_rate_role` in `tests/test_invoice_mapper.py`).
- **Evidence:** `expected_catalogs('Tipo Imposto ICMS')` returns the three ICMS CST catalogs, while `expected_catalogs('Tipo de Imposto ICMS')` returns `()`. Only 15 model columns get any catalog at all.
- **Impact:** either the clause is dead code, or 14 columns lose a signal.
- **Fix:** decide which applies.
  - If these columns hold CST codes, match on the tokens `{"tipo", "imposto"}`. CST catalogs cannot auto-accept (their score stays below 0.90), so the change would only add evidence for the resolver.
  - If they do not, delete the clause.

### 3.12 The catalog score ignores how many values are well-formed

- **Where:** `reference_catalog.py` `ReferenceCatalogs.analyze`. The score is `distinct_coverage * specificity * sample_factor` (188), and `sample_factor` counts non-empty values rather than well-formed ones (184).
- **Evidence:** a free-text column of 1,000 values that contains a single '5102' scores a perfect CFOP match:

  ```bash
  python - <<'EOF'
  import pandas as pd
  from reference_catalog import ReferenceCatalogs
  rc = ReferenceCatalogs.from_directory("reference_data")
  column = pd.Series([f"free text {i}" for i in range(999)] + ["5102"])
  e = rc.analyze(column, "cfop")
  print(e["score"], e["coverage"], e["format_fraction"])   # 1.0 0.001 0.001
  EOF
  ```

- **Impact:** auto-accept is safe, because `catalog_evidence_is_distinctive` requires `format_fraction >= 0.90` (`invoice_mapper.py` 1559). The raw score is still used unchecked in three places:
  - `_catalog_score` (1436–1438), to pull candidates into the pool (1787–1797)
  - the preserve-best-name check (2753–2756), where a junk score of 0.90 or more stops the header favourite from being restored
  - the `catalog_evidence` sent to the resolver
- **Fix:** multiply the score by `format_fraction`.

### 3.13 Expired cClassTrib codes still count

- **Where:** `reference_catalog.py` `ReferenceCatalogs.from_directory` (128–146) keeps only the `code` column (144). `valid_from` and `valid_to` are ignored everywhere.
- **Evidence:** `reference_data/ibs_cbs_classification.csv` holds cClassTrib, the 6-digit CBS/IBS tax classification code. Three of its codes, 220001 to 220003, have `valid_to` 2026-01-01, and all three still load as valid.
- **Fix:** drop codes whose `valid_to` is before the run date, or keep them and report matches on expired codes separately.

### 3.14 The reference refresh is fragile

- **Where:** `update_reference_data.py`. Do not run it casually: it downloads from government sites and rewrites `reference_data/`.
- **What happens:**
  - **Sheet names** are hard-coded to one publication, 'CST 2026-06-01 Pub' (116) and 'cClass 2026-06-01 Pub' (130). A new release will crash the script or has to be edited in by hand.
  - **Version labels:** the `source_version` strings for CFOP, IBGE and both IBS/CBS tables are literals (172, 186, 193, 204). Only NCM's version is read from the download (179), so the other tables would label new data with the old version.
  - **No length filter for IBS/CBS CST** (115–127), unlike CFOP (96), the municipality table (111–113) and cClassTrib (142). As a result, line 2 of `reference_data/ibs_cbs_cst.csv` is a row with a blank code. It is harmless today, because the loader drops blank codes (`reference_catalog.py` 144).
  - **Manifest:** the loader never checks `manifest.json`'s `schema_version` or `generated_at`, and the manifest has no checksum of its own. The mapper records the manifest's sha256 in Run Metadata (`invoice_mapper.py` 3353–3357).
- **Fix:** find sheets by prefix, read versions from the source files, add the 3-digit filter, and validate the manifest schema on load.

### 3.15 An unknown manifest key crashes with a raw traceback

- **Where:** `reference_catalog.py` `from_directory`, line 134 (`CatalogSpec(name=name, **raw_spec)`). `invoice_mapper.py` `main` (3296–3302) catches only `OSError`, `ValueError`, `KeyError` and `json.JSONDecodeError`.
- **Evidence:** run `python invoice_mapper.py data/clients/oracle.xlsx --offline --reference-data <copy> --output <scratch>.xlsx` against a scratch copy of `reference_data/` whose manifest has one extra key, `valid_until`. It exits 1 with `TypeError: CatalogSpec.__init__() got an unexpected keyword argument 'valid_until'`, not the friendly "Could not load reference catalogs … Run update_reference_data.py or use --no-reference-data" message.
- **Fix:** validate the manifest keys and raise `ValueError`, or add `TypeError` to the `except` clause.

### 3.16 No test covers a checksum mismatch

- **Where:** `reference_catalog.py` lines 136–140 raise on a checksum mismatch; nothing in `tests/` exercises that path.
- **Evidence:** `grep -rn "checksum\|sha256\|manifest" tests/` finds only an unrelated `client_sha256` at `tests/test_apply_invoice_mapping.py:330`.
- **Fix:** add a test that tampers with one CSV under `tmp_path` and expects the `ValueError`.

### 3.17 `backfill_mapping_steps.py` has no safety checks and no tests

- **Where:** `backfill_mapping_steps.py` `backfill` (79–197) and `_header_step` (69–76).
- **What happens:**
  - **Wrong client file goes unnoticed.** It reads the client export from sheet 0, header row 0 (86), whatever the report's Run Metadata says, and it never checks `client_sha256`. Passing the wrong export silently produces wrong arithmetic and veto labels.
  - **No backup.** It saves over the report in place (194–195).
  - **Wrong offline label.** `_header_step` treats `embedding_model == "disabled"` as lexical (75), but the mapper writes `"offline"` (`invoice_mapper.py` 3346). A backfilled offline report would therefore claim `semantic`. All four committed reports are online runs, so none is affected.
  - **Runs only once.** It refuses a report that already has the step columns (83–84), so a corrected backfill must start from the pre-backfill report in git history (see 1.1).
  - **No tests.** `grep -rln backfill tests/ mapeval/` finds nothing, which is how the bug in 1.1 shipped.
- **Fix:**
  - Read the sheet and header row from Run Metadata, and verify the client hash.
  - Write to a new file instead of saving in place.
  - Compare against `"offline"`.
  - Add a test that backfills a small fixture report and checks every field the backfill reads.

### 3.18 Role-word prefix expansion matches ordinary words

- **Where:** `invoice_mapper.py` `expand_truncated_role_words` (238–255), which uses `_ROLE_PREFIXES`: 21 prefixes of 3 or more letters, built at 217–230.
- **What happens:** any header token equal to a prefix of a role word is expanded: 'out' becomes other, 'red' becomes reduced, 'exe' becomes exempt. The function's docstring acknowledges the risk.
- **Evidence:** `header_semantic_role('Valor Out')` returns `'other'`.
- **Impact:** low. An English header that uses "Out" as a word gains the "other" role.
- **Fix:** expand prefixes only in headers that also name a tax family.

### 3.19 The mapeval CLI and tests are fragile

- **Unknown client names.** `--client` is not validated, so an unknown name raises a raw `KeyError` (`mapeval/cli.py` 62).
- **Silent skip.** For unkeyed clients, an absent report is skipped without a word (65–66). For example, `python -m mapeval --root /some/empty/dir --client sap_terumo` prints nothing and exits 0. An absent client export, by contrast, raises an error.
- **Working directory.** `--root` defaults to `.`, so mapeval must be run from the repo root.
- **Skip marker.** The workbook tests skip only when `data/keys/de_para_oracle.xlsx` is absent (`tests/test_mapeval.py` 35–38). If any other workbook is absent, they fail instead of skipping.
- **Pinned totals.** Several tests pin absolute totals in `tests/test_mapeval.py`: the denominators 30/26 at 111, `43` at 124, `(39, 43)` at 143 and `(43, 32)` at 191. Regenerating any keyed report breaks them by design, whether the change is an improvement or not. Update them on purpose, alongside the report.
- **Fix:**
  - Set `choices=list(CLIENTS)` on `--client`.
  - Print a line when a report is skipped.
  - Resolve `--root` relative to the package.
  - Check every workbook in the skip marker.

### 3.20 Setup quirks: `.env` and no lockfile

- **`.env` is never loaded.** A working copy may contain a `.env` at the repo root. It is gitignored, and no code reads it (nothing imports `dotenv`), so online runs need `OPENAI_API_KEY` exported in the shell. When sharing the folder as an archive rather than as a git clone, leave `.env` out.
- **No lockfile.** `requirements.txt` gives version ranges only. The test suite was verified with Python 3.13.
- **Fix:** either delete `.env` or load it explicitly, and pin versions in a constraints or lock file.

---

## 4. Documentation and comment drift in the code

### 4.1 Stale baseline numbers in comments

| Where | Says | Current |
|---|---|---|
| `mapeval/truth.py:21` | The key corrections are "the difference between 33/56 and 37/56" | 39/56 found with `--strict-key-only`, 43/56 with the corrections |
| `tests/test_mapeval.py:179` | "Picking recall is 42/56 but the customer receives 31" | The test asserts `(43, 32)` at line 191 |
| `invoice_mapper.py:3051` (`AdjudicationRow` docstring) | "finds 42 of 56 and delivers 31" | It finds 43 and delivers 32. "All 11 of the difference" is still right |

**Fix:** update the figures, or point to `python -m mapeval` instead of quoting numbers in comments.

### 4.2 `mapping_steps.py` names functions that do not exist

- Line 66: the resolver's `implemented_by="_openai_data_decision"`. The real function is `OpenAIDataResolver.resolve` (`invoice_mapper.py` 1090).
- Line 182: exclusivity's `implemented_by="build_mapping (the available set)"`. The real function is `map_columns` (`invoice_mapper.py` 2234), through its `available` set.

**Fix:** correct both strings. A test that resolves every `implemented_by` name against the modules would stop this from recurring.

### 4.3 Comments describe behaviour the code does not have

- `mapping_steps.py` 59–64 says `decided_by = resolver` "carries its honest meaning", but that value is never produced (2.2).
- `mapping_steps.py` 106–108 says a validating column "is that identifier whatever it is called", but the step also depends on the model's samples (3.10).
- `mapping_steps.py` `GUARDS` (140–184) reads as if all four guards are reported, but two are never written (2.1).
- `mapeval/score.py` 80–81, the docstring of `delivered_correct`, says reviewer-confirmed rows count, but the code counts accepted rows only (2.6).
- `backfill_mapping_steps.py` 53–56 says `DISTINCTIVE_CATALOG_SCORE` "applies the same bar" as the live run, but it does not (2.3).
- `backfill_mapping_steps.py` 91 says "Match a fresh report's layout", but it does not (5.1).

**Fix:** correct each comment along with its linked issue, or now if that fix is deferred.

---

## 5. Data quirks

### 5.1 Report layouts differ, and sap_procosa's still claims schema 2

- **sap_procosa's report is the oldest.** `data/reports/mapping_sap_procosa_full.xlsx` was generated on 2026-08-04; the other three were generated on 2026-08-25. It has only the Mappings and Run Metadata sheets: no Unmatched Client Columns, no Adjudication Queue, no `arithmetic_proof` and no `catalog_distinct_coverage`. Yet its Run Metadata says `mapping_schema_version = 2`, like the others: `MAPPING_SCHEMA_VERSION` (`invoice_mapper.py` 30) was not bumped when those columns and sheets were added.
- **The step columns sit in three different places:**
  - A fresh report puts them after `arithmetic_proof`, in the order `supported_by, decided_by, vetoed_by` (`MappingResult`, 1270–1274).
  - The backfilled datasul, oracle and sap_terumo reports put `decided_by, supported_by, vetoed_by` before `arithmetic_proof`, as columns 17–19.
  - The backfilled sap_procosa report puts them after `llm_final_confidence` and before `reviewer_status`, as columns 18–20.
- **A blank banner row.** The banner in `data/mapped/sap_procosa_mapped.xlsx` has an `arithmetic_proof` row that is entirely blank, because apply adds absent optional columns as blanks (`_ordered_mapping`, 163–179).
- **Impact:** read report columns by name, never by position, and do not infer the layout from the schema version. Do not regenerate sap_procosa just to upgrade its layout (3.3).
- **Fix:** bump `MAPPING_SCHEMA_VERSION` whenever columns or sheets change.

### 5.2 Answer-key row counts

- In the de-para files, row 1 holds the "Coluna X" annotations, row 2 the model headers, and data starts at row 3.
- `data/keys/de_para_oracle.xlsx` has 7,338 data rows. `data/clients/oracle.xlsx` loads as 7,340 data rows, but Excel row 7340 is entirely blank, so it has 7,339 populated rows.
- `de_para_datasul.xlsx` has 6,804 populated data rows, matching `datasul_maio.xlsx`, but the sheet declares 7,340 rows, so openpyxl's `max_row` overstates it. `de_para_procosa.xlsx` declares 209 rows, of which 101 are populated (its two header rows plus 99 data rows).
- **Impact:** none for scoring today, since mapeval reads only row 1. It matters if someone builds the "row-aligned de-para data channel" that `mapeval/truth.py` 17–19 mentions: the oracle rows cannot be aligned by position.
- **Fix:** align on a document key, such as invoice number plus item, rather than on row position, and count populated rows rather than trusting `max_row`.

### 5.3 `de_para_procosa.xlsx` is not a column key and is shifted by one

- It is a record-granularity spec (its row 1 marks each column 'ITEM' or 'NOTA'), written against a different export, so mapeval treats sap_procosa as unkeyed.
- It has 226 columns. Because 'Tipo de Imposto CBS' is absent, file column N corresponds to model column N+1.
- Do not load it with `load_answer_key`.

### 5.4 model.xlsx has 'Descrição' twice

- 'Descrição' is the header of both column 35 and column 195.
- `pandas.read_excel` renames the second one to 'Descrição.1', while `SheetTable` keeps both `name`s unchanged and gives the second the `display_name` 'Descrição (occurrence 2)'. Address model columns by number.
- The oracle row withheld at m195 refers to the second 'Descrição'.

### 5.5 The datasul export's headers are space-padded

- 206 of the 348 headers in `data/clients/datasul_maio.xlsx` carry trailing spaces, for example 'Filial      '. `_header_text` (`invoice_mapper.py` 414–417) strips them on load.
- One annotation in row 1 of `de_para_datasul.xlsx` has stray whitespace, which `parse_coluna_annotation` (`mapeval/excel.py` 43–55) tolerates.
- Any new code that reads these files must strip headers and annotations the same way.
