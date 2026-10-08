# Data files and formats

This page describes every data file in the repository and every file the tools write: the base model, the client exports, the de-para files, the mapping reports, the mapped workbooks and the fiscal reference catalogs.

Related documents:

- [README](../README.md): setup and the main commands.
- [ARCHITECTURE.md](ARCHITECTURE.md): how the mapper reaches each decision.
- [EVALUATION.md](EVALUATION.md): how the reports are scored against the keys.
- [KNOWN_ISSUES.md](KNOWN_ISSUES.md): bugs that affect the files described here.

All counts below were measured on 2026-10-08 against the files on the `organize-and-document` branch.

## Contents

- [Terms](#terms)
- [Layout](#layout)
- [Confidentiality](#confidentiality)
- [The base model: `data/model.xlsx`](#the-base-model-datamodelxlsx)
- [Client exports: `data/clients/`](#client-exports-dataclients)
- [De-para files: `data/keys/`](#de-para-files-datakeys)
- [Mapping reports: `data/reports/`](#mapping-reports-datareports)
- [Mapped workbooks: `data/mapped/`](#mapped-workbooks-datamapped)
- [Reference catalogs: `reference_data/`](#reference-catalogs-reference_data)
- [Regenerating the outputs](#regenerating-the-outputs)

## Terms

| Term | Meaning |
|---|---|
| NF-e | *Nota Fiscal eletrônica*, Brazil's electronic invoice. Each one has a 44-digit access key (*chave de acesso*) that ends in a check digit. |
| CNPJ / CPF | Taxpayer numbers for companies (14 digits) and individuals (11 digits). Both end in check digits. |
| CFOP | 4-digit fiscal operation code that describes the nature of a transaction (sale, return, transfer, and so on). |
| NCM | 8-digit Mercosur product classification code. |
| CST / CSOSN | Tax situation codes, which say how a tax applies to an invoice line. Each tax has its own CST table. CSOSN is the equivalent table for companies in the Simples Nacional regime. |
| ICMS, IPI, PIS, COFINS, ISS | The current taxes: ICMS is the state VAT, IPI the federal excise on manufactured goods, PIS and COFINS federal contributions on revenue, and ISS the municipal service tax. |
| CBS, IBS, IS | The taxes introduced by the consumption-tax reform (Lei Complementar 214/2025). CBS is the federal VAT that replaces PIS and COFINS, IBS is the VAT shared by states and municipalities, and IS (*Imposto Seletivo*) is an excise on specific goods. |
| de-para | Portuguese for "from-to": a table that says which client column feeds which model column. |

## Layout

| Path | What it is | Written by | Read by |
|---|---|---|---|
| `data/model.xlsx` | The firm's base model: 227 target columns and 209 sample rows | the firm (fixed) | `invoice_mapper.py`, `apply_invoice_mapping.py` |
| `data/clients/` | Four real client ERP exports | the clients | the mapper, apply, `backfill_mapping_steps.py`, `mapeval` |
| `data/keys/` | Three client-authored de-para files. Two of them are answer keys. | the clients | `mapeval`, `tests/test_mapeval.py` |
| `data/reports/mapping_<client>_full.xlsx` | Mapping reports, one per client | `invoice_mapper.py`, then reviewers and `backfill_mapping_steps.py` | `apply_invoice_mapping.py`, `mapeval`, `tests/test_mapeval.py` |
| `data/mapped/<client>_mapped.xlsx` | Deliverables in the model's column layout | `apply_invoice_mapping.py` | the downstream application, reviewers |
| `reference_data/` | Snapshot of the official fiscal code tables | `update_reference_data.py` | `reference_catalog.py`, through the mapper |

Every file in `data/` and `reference_data/` is tracked in git. The two SAP exports use an upper-case `.XLSX` extension, so type it exactly in commands.

`mapeval/cli.py` `CLIENTS` and `tests/test_mapeval.py` `KEYED` hard-code these paths, and both CLIs default `--model` to `data/model.xlsx`. If you rename or move a workbook, update them.

## Confidentiality

The workbooks hold real client data, including CNPJ and CPF numbers, NF-e access keys, partner names and document numbers. That applies to:

- the four exports;
- the de-para files;
- the sample rows of `model.xlsx` (91% of its `Nº ID fiscal 1` values are valid CPFs, and all of its `Chave de acesso` values are valid NF-e keys);
- everything derived from them, including the mapped workbooks and the report sheets that quote sample values.

The repository is shared with you on purpose, for internal work only:

- Keep it on machines and git remotes the firm controls. Do not push it to a public or personal host, do not paste values into outside tools, and do not attach workbooks to tickets outside the firm.
- Everything under `data/` is committed, so it is also in git history. Deleting a file from the working tree does not remove it from a clone.
- **Online mode sends data to OpenAI.** Without `--offline`, `invoice_mapper.py` sends every model and client header to the embeddings API. For each model column that the header pass cannot settle, it also sends value profiles to the chat model. Each profile includes up to `--sample-size` sample values (default 8), taken from the model column and from every shortlisted client column. Nothing is masked; the chat requests set `store=False`. Confirm that the client permits this before you run online.
- Offline mapping, apply, `mapeval` and the tests never contact OpenAI. `update_reference_data.py` downloads from government sites but sends no client data.
- `.env` at the repo root is gitignored, and no code reads it. Online runs need `OPENAI_API_KEY` exported in the shell.

## The base model: `data/model.xlsx`

The model has one sheet, `Sheet1`:

- **Row 1** is the header: 227 columns, A to HS.
- **Rows 2-210** hold 209 sample rows.
- An autofilter covers `A1:HS210`. There are no frozen panes and no merged cells.

The sample rows matter. In online mode the resolver compares a model column's sample values with those of each candidate client column. 180 model columns have at least one sample value. The other 47 have none, so the resolver gets no example values for them.

### Reform-tax blocks, columns 1-28

The model opens with one seven-field block per reform tax:

| Columns | Tax |
|---|---|
| 1-7 | CBS |
| 8-14 | IBSM (from its name, the municipal share of IBS) |
| 15-21 | IBS |
| 22-28 | IS |

Every block has the same fields in the same order, with the tax as a suffix (`Base CBS`, `Aliquota IBSM`, `Valor IS`):

| Position in block | Field | Meaning |
|---|---|---|
| 1 | `Tipo de Imposto` | tax type |
| 2 | `Base` | tax base |
| 3 | `Aliquota` | rate |
| 4 | `Valor` | tax amount (base × rate) |
| 5 | `Isentas` | exempt amount |
| 6 | `Outras` | other, non-taxed amount |
| 7 | `Base Reduzida` | reduced base |

In the sample rows the four `Tipo de Imposto` columns (1, 8, 15, 22) are empty, and the other 24 reform-tax fields are 0 on every row.

### Columns 29-227

These are SAP-style Portuguese headers, in four groups:

- **Document fields:** `Data de lançamento`, `Nº de documento`, `Nº da Nota Fiscal`, `Série`, `CFOP`, the partner's `Nome` and `Nº ID fiscal 1`, `Chave de acesso`.
- **Current taxes:** ICMS, ICMS-ST (ICMS collected in advance under tax substitution), IPI, PIS, COFINS, ISS, FECP (a state poverty-fund surcharge on ICMS) and DIF_ALIQ (the ICMS rate differential on interstate operations), along with further ICMS-related SAP tax types (ICAP, ICEP, ICSP, ICOP). Most of them use the same Tipo/Base/Aliquota/Valor/Isentas/Outras/Base Reduzida fields, though a tax's fields are not always adjacent. ICMS, for example, has `Base ICMS` at 41 and `Aliquota ICMS` at 190.
- **Item and import fields:** `Material`, `Nº do NCM`, `Quantidade`, `Preço líq.`, customs clearance dates and places.
- **Code and legal-text columns:**
  - `CST IPI`, `CST COFINS`, `CST PIS` and `CST ICMS` are columns 219-222.
  - The IBS/CBS CST and classification code are columns 176-177.
  - `Texto ICMS`, `Texto IPI`, `Lei COFINS` and `Lei tributária PIS` hold SAP tax-law references.

### Repeated headers

Two header names repeat:

- `Descrição` at columns 35 and 195, with identical text.
- `Domicílio fiscal` (76) and `Domicílio Fiscal` (154), which differ only in case.

`SheetTable.from_excel` (invoice_mapper.py) compares normalized headers, so it names the second column of each pair `... (occurrence 2)`. Reports and keys address model columns by number, so the repeats are never ambiguous there. If you read `Mapped Data` with pandas, the second `Descrição` comes back as `Descrição.1`.

### Do not change it

`model.xlsx` is fixed for now. Do not trim, reorder, rename or restructure it, and do not propose changes that depend on it changing.

It is a deliberately oversized superset that covers every field any client might send. A run that delivers only part of the model is the expected outcome, not a shortfall.

There are also mechanical reasons to leave it alone:

- Every report records `model_sha256`, and `apply_invoice_mapping.py` refuses a report whose model hash differs.
- Reports, keys and `mapeval` all address model columns by position.

## Client exports: `data/clients/`

These are four real ERP exports, one sheet each, with the header on row 1. They are reference examples for learning mapping strategies that transfer to other clients. They are not the product: production will see many clients nobody has seen yet.

- Do not tune thresholds, vocabulary or rules against these four files.
- No code may branch on a client name.
- Validate changes leave-one-client-out (see [EVALUATION.md](EVALUATION.md)).

| File | ERP | Sheet | Data rows | Columns | Notes |
|---|---|---|---|---|---|
| `datasul_maio.xlsx` | Datasul | `datasul` | 6,804 | 348 | Every header is a fixed 12-character field, and 206 of them end in padding spaces (`'Filial      '`). 3 header names repeat. 36 columns hold no values. |
| `oracle.xlsx` | Oracle | `oracle` | 7,340 | 48 | Upper-case headers (`ORGANIZACAO`, `DATA GL`, `CFO`). 1 column holds no values. |
| `sap_procosa.XLSX` | SAP | `Sheet1` | 18,209 | 118 | `Direito` appears 3 times. 1 column holds no values. |
| `sap_terumo.XLSX` | SAP | `Sheet1` | 727 | 365 | English SAP field labels, some in Portuguese. 23 header names repeat (`Reserved` 5 times). 89 columns hold no values. |

How the tools read an export: `SheetTable.from_excel` loads the sheet with `header=None, dtype=object`, so no value is coerced.

- Headers lose their surrounding spaces.
- A blank header becomes `Unnamed column N`.
- A repeated header gets a ` (occurrence N)` suffix, for example `Name 1 (occurrence 2)` in sap_terumo. Repeats are detected after `normalize_header`, so headers that differ only in case, accents or punctuation count as repeats.

That display name is what reports and reviewers use. For an export whose header is not on row 1, pass `--client-header-row` (1-based) to both the mapper and apply.

Every report stores the SHA-256 of its export (`client_sha256`), and apply refuses an export that differs. Re-saving an export in Excel changes its hash and invalidates its report. All four exports match the hashes recorded in their reports.

## De-para files: `data/keys/`

The clients wrote three de-para files. Only two of them are answer keys.

### Answer-key layout (datasul, oracle)

| Row | Content |
|---|---|
| 1 | For each model column the client has, free text `Coluna X` naming the export's Excel column letter (`Coluna I` is column 9). The cell is blank for every other model column. Some cells carry trailing spaces (`'Coluna BV '`). |
| 2 | The 227 model headers, identical to row 1 of `data/model.xlsx`. |
| 3 onward | The client's data rearranged into the model layout, row by row in step with the export: key row 3 matches export row 2, and so on. |

| File | Sheet | Model columns annotated | Data rows | Export data rows |
|---|---|---|---|---|
| `de_para_datasul.xlsx` | `Data Sul` | 30 | 6,804 | 6,804 |
| `de_para_oracle.xlsx` | `Oracle` | 26 | 7,338 | 7,340 |

Both sheets declare a range of `A1:HS7340`, which is larger than their data. Count populated rows instead of trusting `max_row`.

`mapeval` reads row 1 only, through `mapeval/truth.py` `load_answer_key` and `mapeval/excel.py` `parse_coluna_annotation`. The two keys together annotate 56 model columns (30 + 26). Those 56 are the only recall denominator; 227 never is. No code reads the data rows. They are what the corrections below were checked against.

The keys are a regression test, not training data. Never derive rules, vocabulary or thresholds from them.

### Four known key defects

`KEY_CORRECTIONS` in `mapeval/truth.py` overrides four annotations where the key is wrong and the mapper is right:

| Client | Model column | Key names | Corrected to | Why |
|---|---|---|---|---|
| oracle | 213 `Aliquota PIS` | c37 `ALIQ COFINS` | c36 `ALIQ PIS` | the key gives the COFINS rate for the PIS rate |
| oracle | 220 `CST COFINS` | c39 `CST-PIS` | c40 `CST-COFINS` | the PIS/COFINS pair is transposed in the key |
| oracle | 221 `CST PIS` | c40 `CST-COFINS` | c39 `CST-PIS` | same transposition |
| datasul | 184 `Aliquota COFINS` | c99 `Aliq. COFINS` | c188 `Aliq. Cofins` | c99 belongs to another block of the export and does not fit the rest of the key; c188 satisfies base × rate = value with the key's own PIS/COFINS base and value columns (model 52-55) |

`mapeval` applies the corrections by default. `python -m mapeval --strict-key-only` scores against the raw keys instead. The corrections are worth exactly four columns in both the delivered and the found counts. `tests/test_mapeval.py` (`test_the_four_key_corrections_are_worth_exactly_four_columns`) asserts this for the found count only. See [EVALUATION.md](EVALUATION.md) for the figures.

### `de_para_procosa.xlsx` is not a key

- Its sheet, `SAP`, has 226 columns. Row 1 labels each column `ITEM` (155 columns) or `NOTA` (71): whether the field belongs to the invoice line or to the invoice as a whole. There are no `Coluna X` annotations.
- Row 2 is the model header without its first column (`Tipo de Imposto CBS`), so file column N is model column N+1.
- File column 31 (model 32, `Nº da Nota Fiscal`) holds a note instead of the header. The note says the NF-e number was placed in column GZ, which is file column 208 and model column 209 (`Número de nota fiscal eletrônica`).
- It has 99 data rows (rows 3-101), although the sheet declares 209.
- None of its 97 document numbers occurs anywhere in `sap_procosa.XLSX`, so it was written against a different extract. Its `Chave de acesso` column holds 52 valid NF-e access keys, and the export holds no access keys at all.

It documents record granularity, not which export column feeds which model column. That is why `mapeval/cli.py` `CLIENTS` gives sap_procosa no key. sap_terumo has no de-para at all.

For both unkeyed clients, `mapeval` prints only the accept count and checks for accepts onto client columns that hold no values.

## Mapping reports: `data/reports/`

`invoice_mapper.py` writes one report per run, through `write_results`. The committed reports are named `mapping_<client>_full.xlsx`, where `<client>` is `datasul`, `oracle`, `sap_procosa` or `sap_terumo`.

The mapper's default output is `mapping_<export stem>.xlsx` in the current directory, so pass `--output` explicitly. An output ending in `.csv` writes only the Mappings table; apply cannot read it.

> **Reviewer verdicts are easy to destroy.** `write_results` always writes the three reviewer columns blank, and it overwrites its output path without asking. Pointing `--output` at an existing report erases that report's verdicts; today that would erase sap_procosa's 26. Write each new run to a new file name.

### Sheets

The sheets come in this order:

1. **`Mappings`** must stay first. `apply_invoice_mapping.py` (`_ordered_mapping`) reads sheet index 0 without naming it, so a sheet inserted in front breaks apply with a misleading `Mapping report is missing columns` error. If you add a sheet in Excel, put it at the end.
2. **`Run Metadata`**.
3. **`Unmatched Client Columns`**.
4. **`Adjudication Queue`**.

Sheets 3 and 4 are absent from the older sap_procosa report (see [sap_procosa's older report](#sap_procosas-older-report)). A current run writes both sheets even when it has nothing to list, so an absent sheet always means an older report.

### Status counts

| Report | accepted | review | no_match | Reviewer verdicts |
|---|---|---|---|---|
| datasul | 19 | 95 | 113 | 0 |
| oracle | 18 | 37 | 172 | 0 |
| sap_procosa | 45 | 66 | 116 | 26 |
| sap_terumo | 67 | 92 | 68 | 0 |

The three statuses mean:

- **accepted**: the row cleared the accept gate and is delivered.
- **review**: a candidate exists, but the gate withheld it.
- **no_match**: the mapper found no counterpart. Because the model is a superset, this is often the correct answer: the client does not send that field.

Every review row carries a proposed client column, and no no_match row does.

### `Mappings`

`Mappings` has one row per model column, in model order. These are its columns as a fresh report writes them (`MappingResult` plus `REVIEWER_COLUMNS` in invoice_mapper.py):

| Column | Meaning |
|---|---|
| `model_column_number` | 1-based position in `data/model.xlsx`. |
| `model_column` | Model header. Apply checks it against the model, position by position. |
| `client_column_number` | 1-based export column proposed. Blank on no_match rows. |
| `client_column` | Its display name. Apply checks it against the export, which catches a report made from a different layout. |
| `name_confidence` | Header similarity of the chosen column: embeddings online, lexical offline. |
| `name_runner_up_confidence` | Best header similarity among the other client columns. |
| `name_margin` | The chosen column's lead over the runner-up. |
| `data_confidence` | Local score from the values. Blank on pass-1 (header-only) accepts. |
| `final_confidence` | The score the accept gate tests (`--final-threshold`, default 0.90). |
| `method` | The branch that produced the row: `name`, `data`, `catalog`, `name_only` (offline review) or `data_error` (the resolver call failed). See "Values of `method`" in [ARCHITECTURE.md](ARCHITECTURE.md). |
| `status` | `accepted`, `review` or `no_match`. |
| `reason` | One sentence explaining the row. |
| `catalog_name` | Reference catalog the column's values matched, if any. |
| `catalog_coverage` | Share of non-empty rows whose value is a valid code. |
| `catalog_distinct_coverage` | Share of distinct well-formed codes that are valid. Only the pass-1 audit fills it. |
| `catalog_confidence` | Catalog score: distinct coverage × catalog specificity × a small-sample factor. |
| `arithmetic_proof` | Text, set when a base × rate = value trio in the export places this column here. |
| `supported_by` | Evidence steps that back the chosen column, weakest first, joined with ` + `. |
| `decided_by` | The strongest of them. |
| `vetoed_by` | The guard that refused an otherwise viable match: `vacuity` or `family_conflict`. |
| `llm_data_confidence`, `llm_final_confidence` | The resolver's own scores, kept for audit. The gate does not use them. |
| `reviewer_status`, `correct_client_column`, `reviewer_note` | Written blank. Reviewers fill them in (see [Reviewer columns](#reviewer-columns)). |

The confidence values are decision signals, not calibrated probabilities.

The step names come from `mapping_steps.py`. These evidence steps are listed from weakest to strongest:

1. `resolver`
2. `lexical`
3. `semantic`
4. `profile`
5. `identifier`
6. `catalog`
7. `arithmetic`

The last four transfer to an unseen ERP, because they rest on tax law, official code tables and check digits rather than on this client's header words. The share of accepts that rest on header-independent evidence is the project's generalization metric. For the keyed clients, `mapeval` prints a narrower version as "header-independent accepts". It counts an accept only when the row carries a decisive `arithmetic_proof` or its `method` is `catalog`, and it does not read the step columns (see [EVALUATION.md](EVALUATION.md#the-generalization-metric) and item 2.4 in [KNOWN_ISSUES.md](KNOWN_ISSUES.md)). "Step attribution" in [ARCHITECTURE.md](ARCHITECTURE.md) explains the rest.

**All four committed reports got their step columns from a backfill.** `backfill_mapping_steps.py` added them after the runs (commit 88c503b) by rebuilding them from the stored scores. Three consequences:

- It writes two labels that a fresh run never does:
  - `unrecorded`: an objective step ran, but the stored scores do not say which.
  - `catalog (agrees)`: catalog evidence agreed but was too weak to decide.
- It reads `llm_final_confidence` from a stale position, three cells off. As a result `resolver` never appears in `supported_by` in the datasul, oracle and terumo reports. That includes the 75, 32 and 78 rows (respectively) that have a proposed column and a positive resolver score. See [KNOWN_ISSUES.md](KNOWN_ISSUES.md).
- The step columns sit in different places in different reports:

| Report | Position of the step columns |
|---|---|
| fresh run | after `catalog_confidence`: `arithmetic_proof`, `supported_by`, `decided_by`, `vetoed_by` |
| datasul, oracle, sap_terumo | before `arithmetic_proof`: `decided_by`, `supported_by`, `vetoed_by`, `arithmetic_proof` |
| sap_procosa | after `llm_final_confidence`, just before `reviewer_status` (this report has no `arithmetic_proof`) |

Every tool looks columns up by name, so do the same.

### Reviewer columns

Reviewers record their verdicts in `Mappings`, and `apply_invoice_mapping.py` (`_effective_columns`) applies them. Values are case-insensitive.

| `reviewer_status` | `correct_client_column` | Effect on the mapped workbook |
|---|---|---|
| blank | blank | The mapper's decision stands: accepted rows are delivered, others are not. |
| `correct` | blank | The proposed `client_column_number` is delivered. Raises if the row has no proposal. |
| `incorrect` | required | The column named here is delivered. Give either a 1-based column number or the export header. For a repeated header, use the display name with its ` (occurrence N)` suffix. The bare name silently selects the first occurrence. Text that parses as a number is always read as a column number. |
| `no_mapping_exists` | blank | Nothing is delivered. This confirms the client does not send the field. |

`reviewer_note` is free text, and it is copied into the mapped workbook's banner.

Apply refuses to write a workbook in any of these cases:

- the status is not one of the values above;
- `correct_client_column` is set with any status other than `incorrect`;
- the status is `incorrect` and the correction is blank;
- the status is `correct` and the row has no proposal;
- a correction names a column the export does not have;
- the same client column would be delivered twice.

The Adjudication Queue is a ranked index into these rows, not a second place to write. Record verdicts here.

### `Run Metadata`

`Run Metadata` has two columns, `key` and `value`, with 19 keys:

| Key | Meaning | Value in the committed reports |
|---|---|---|
| `mapping_schema_version` | Report schema (`MAPPING_SCHEMA_VERSION`). | 2 |
| `generated_at_utc` | When the mapper ran. | 2026-08-25 for datasul, oracle and sap_terumo; 2026-08-04 for sap_procosa |
| `prompt_version` | Version of the resolver prompt. | 2026-08-04.2 |
| `client_file`, `model_file` | File names only, without a directory. | for example `oracle.xlsx`, `model.xlsx` |
| `client_sha256`, `model_sha256` | SHA-256 of the inputs. Apply refuses files that differ. | match the files now in `data/` |
| `client_sheet`, `model_sheet` | Sheet index or name used. | 0 |
| `client_header_row`, `model_header_row` | 1-based header rows. | 1 |
| `embedding_model` | Header-embedding model, or `offline`. | `text-embedding-3-large` |
| `llm_model` | Resolver model, or `offline`. | `gpt-4o-mini` |
| `name_threshold`, `name_margin`, `final_threshold` | Gate settings. | 0.9, 0.05, 0.9 |
| `candidate_count` | Size of each pass-2 candidate shortlist per model column (`--candidates`). Online there is one shortlist each by header score, value profile, arithmetic and catalog evidence, and the resolver sees their union. | 10 |
| `sample_size` | Sample values per column in each profile (`--sample-size`). | 8 |
| `reference_manifest_sha256` | SHA-256 of `reference_data/manifest.json`, or `disabled`. | `c4e2106d...`, which matches the current manifest |

All four committed reports came from online runs. Apply checks only the two input hashes, and it skips the check silently when the sheet or a key is absent. It does not compare the sheet, header-row or manifest settings.

### `Unmatched Client Columns`

This sheet lists every export column that no accepted mapping consumed. A long list is normal, because a client sends many fields the model does not hold. The list is how a reviewer spots a real miss.

| Column | Meaning |
|---|---|
| `client_column_number`, `client_column` | The export column. |
| `disposition` | `proposed_not_accepted`: some model row proposed it as review. `never_referenced`: no row did. |
| `review_priority` | `shortlist` when the column holds informative values (not empty, not all zeros) and either carries a signal or was proposed. `low` otherwise. |
| `rows_with_value`, `distinct_values` | How much data the column holds. |
| `signals` | Comma-separated header-independent hints: `arithmetic`, `catalog`, `nfe_key_checkdigit`, `cnpj_checkdigit`, `cpf_checkdigit`, `date`. |
| `signal_detail` | A sentence per signal, or why the column was set aside. |
| `closest_model_column`, `closest_name_confidence` | Nearest model header. This is always computed lexically, even in online runs. |

Rows are sorted shortlist first, then by client column number.

| Report | Rows | Shortlisted |
|---|---|---|
| datasul | 329 | 37 |
| oracle | 30 | 14 |
| sap_terumo | 298 | 40 |

The sheet is written when the mapper runs, so later reviewer verdicts do not change it.

### `Adjudication Queue`

This sheet lists every model column that has a proposed client column and a `final_confidence` above 0 but was not accepted. In the committed reports that is exactly the review rows.

| Column | Meaning |
|---|---|
| `rank` | Position in the queue. |
| `model_column_number`, `model_column` | The model column. |
| `proposed_client_column_number`, `proposed_client_column` | The withheld proposal. |
| `final_confidence` | The score the gate tested. |
| `gate_distance` | `final_threshold` minus `final_confidence`. |
| `name_margin` | Header lead over the runner-up. |
| `rows_with_value` | Populated rows in the proposed column. |
| `resolver_disagrees` | `resolver scored it X` when the resolver's score is more than 0.10 below `final_confidence`. |
| `arithmetic_proof` | Copied from Mappings. |
| `client_sample_values` | Up to 6 values from the proposed column, joined with ` \| `. This is real client data. |
| `why_held` | One of three reasons, listed below. |

The three `why_held` values:

- `below the accept threshold`
- `name evidence only - this branch cannot auto-accept however well the headers match`: the row is pinned just under the gate (0.8999).
- `the proposed column has no populated rows - confirming this would ship a blank column`

The ordering comes from `summarize_adjudication_queue` in invoice_mapper.py:

1. Proposals onto columns that hold data come before proposals onto empty columns.
2. Higher `final_confidence` comes first, compared in steps of 0.0002.
3. Within one step, a larger `name_margin` comes first, then more `rows_with_value`.

The 0.0002 step exists because many rows share the pinned 0.8999. Inside that band, `name_margin` is what orders them.

| Report | Rows | below threshold | name evidence only | no populated rows |
|---|---|---|---|---|
| datasul | 95 | 78 | 11 | 6 |
| oracle | 37 | 35 | 2 | 0 |
| sap_terumo | 92 | 50 | 31 | 11 |

The accept gate stays at 0.90. Reviewer adjudication is how these rows become delivered columns; [EVALUATION.md](EVALUATION.md) explains why lowering the gate is not the answer.

### sap_procosa's older report

`mapping_sap_procosa_full.xlsx` was generated on 2026-08-04 with an older schema:

- It has only `Mappings` and `Run Metadata`.
- `Mappings` has 23 columns. There is no `catalog_distinct_coverage` and no `arithmetic_proof`.
- The step columns were backfilled later, after `llm_final_confidence`.

It is also the only report with reviewer verdicts:

| Mapper status | Reviewer verdict | Rows | Result |
|---|---|---|---|
| review | `correct` | 15 | delivered |
| no_match | `incorrect` | 2 | delivered from the corrected column: `Nome` from column 6 (`NF Parceiro descrição`), `Nº ID fiscal 1` from column 32 (`CNPJ`) |
| review | `no_mapping_exists` | 9 | confirmed as not in the export |

All 26 verdicts carry a `reviewer_note`.

One review session turned 17 rows into delivered columns: 45 accepted plus 17 confirmed makes 62 delivered. That result is why reviewer adjudication of the withheld band is the planned way to lift delivered columns.

Do not regenerate this report; that would erase the 26 verdicts. Apply still accepts it, because it fills the absent optional columns with blanks.

## Mapped workbooks: `data/mapped/`

`apply_invoice_mapping.py` (`build_mapped_workbook`) turns an export and its report into a workbook in the model's column layout. It has two sheets, `Mapped Data` and `Coverage`.

By default it fills a model column only from an accepted row with no reviewer verdict, or from a reviewer-confirmed row (`correct` or `incorrect`). `--include-review` also fills unconfirmed proposals. That output is explicitly unsafe; never deliver it.

### `Mapped Data`

| Excel rows | Content |
|---|---|
| 1 | Header. `A1` is `__mapping_info__`, and columns B to HT are the 227 model headers in model order. Repeated headers are kept as they are. |
| 2-22 | A 21-row metadata banner. Column A holds the label, and each model column holds that row's value from the report. |
| 23 onward | Client data, one row per export data row, in export order. Column A is blank. |

**Client data starts at Excel row 23.** Older notes said row 20; that is out of date. The constant is `FIRST_DATA_EXCEL_ROW = len(MAPPING_METADATA) + 2` (apply_invoice_mapping.py:47). It has moved before, so import it rather than hard-coding a number. Panes are frozen at `B23`.

The banner rows, as listed in `MAPPING_METADATA`:

| Excel rows | Labels |
|---|---|
| 2-3 | `client_column_number`, `client_column`: the column actually delivered, after reviewer decisions. `correct_client_column` itself is not shown. |
| 4-5 | `status`, `method`: the mapper's values, not the reviewer's. |
| 6-15 | `name_confidence`, `name_runner_up_confidence`, `name_margin`, `data_confidence`, `llm_data_confidence`, `llm_final_confidence`, `catalog_name`, `catalog_coverage`, `catalog_confidence`, `final_confidence` |
| 16-19 | `decided_by`, `supported_by`, `vetoed_by`, `arithmetic_proof` |
| 20-22 | `reason`, `reviewer_status`, `reviewer_note` |

The confidence rows are stored as numbers and displayed as percentages. In sap_procosa the `arithmetic_proof` row is blank, because its report has no such column. Its two `incorrect` rows show status `no_match` even though they are delivered.

Values are copied unchanged. The export is read with `dtype=object`, and the only re-typing is that `datetime.time` values are written back as Excel time cells (`hh:mm:ss`).

Formatting:

- The banner has a light-blue fill.
- Delivered headers have a green fill.
- Every model column without a delivered client column is hidden, not deleted, so the delivered columns sit side by side. One click unhides them, and programmatic readers see all 228 columns.

| Workbook | Data rows (Excel rows) |
|---|---|
| `datasul_mapped.xlsx` | 6,804 (23-6,826) |
| `oracle_mapped.xlsx` | 7,340 (23-7,362) |
| `sap_procosa_mapped.xlsx` | 18,209 (23-18,231) |
| `sap_terumo_mapped.xlsx` | 727 (23-749) |

Reading the data with pandas, from the repo root:

```python
import pandas as pd
from apply_invoice_mapping import FIRST_DATA_EXCEL_ROW

df = pd.read_excel(
    "data/mapped/sap_terumo_mapped.xlsx",
    sheet_name="Mapped Data",
    skiprows=range(1, FIRST_DATA_EXCEL_ROW - 1),  # keep the header, drop the banner
)
# 727 rows x 228 columns; the second 'Descrição' arrives as 'Descrição.1'
```

### `Coverage`

`Coverage` has one row per model column and says where each one stands. Read it first, because `Mapped Data` cannot show what is not there. Its columns:

| Column | Meaning |
|---|---|
| `state` | One of the three states below. |
| `model_column_number`, `model_column` | The model column. |
| `excel_column` | Its letter in `Mapped Data` (model column 1 is B). |
| `client_column_number` | The proposal on rows that were not delivered. Blank on delivered rows (see [KNOWN_ISSUES.md](KNOWN_ISSUES.md)). |
| `client_column` | The delivered column, or else the proposal. |
| `final_confidence` | From the report, shown as a percentage. |
| `reason` | From the report. |

The three states:

- **delivered**: apply put a client column here.
- **awaiting decision**: nothing was delivered and the mapper's status is review. These are the Adjudication Queue rows, and the only ones that are anyone's work.
- **not in this client's export**: everything else (no_match). This is a complete and correct answer, not a defect.

Rows are sorted by state in that order, then by model column number. The header is frozen and has an autofilter.

| Client | delivered | awaiting decision | not in this client's export |
|---|---|---|---|
| datasul | 19 | 95 | 113 |
| oracle | 18 | 37 | 172 |
| sap_procosa | 62 | 51 | 114 |
| sap_terumo | 67 | 92 | 68 |

Two caveats (see [KNOWN_ISSUES.md](KNOWN_ISSUES.md)):

- The state comes from the mapper's status, not from `reviewer_status`. sap_procosa's 9 `no_mapping_exists` rows therefore show as awaiting decision, so only 42 of its 51 are actually pending.
- With `--include-review`, unconfirmed proposals are labelled delivered.

Treat `data/mapped/` as build output: regenerate it rather than editing it by hand.

## Reference catalogs: `reference_data/`

This directory holds a snapshot of official code tables. The mapper uses them as header-independent evidence: a column whose values are valid CFOP codes is a CFOP column whatever its header says.

| File | Catalog | Codes | Width | Normalizer | Specificity | Source (`source_version`) |
|---|---|---|---|---|---|---|
| `cfop.csv` | CFOP | 619 | 4 | `cfop` | 1.0 | NF-e portal, IT 2023.002 v1.00; effective 2023-04-24 |
| `ncm.csv` | NCM | 10,515 | 8 | `punctuated_digits` | 1.0 | Siscomex Portal Único, "Vigente em 04/08/2026" |
| `ibge_municipality.csv` | IBGE municipality code | 5,571 | 7 | `digits` | 1.0 | NF-e portal municipality table published 2025-02-18 |
| `ibs_cbs_classification.csv` | IBS/CBS classification (cClassTrib) | 164 | 6 | `digits` | 0.85 | NF-e portal, IT 2025.002 v1.60; published 2026-06-23 |
| `ibs_cbs_cst.csv` | IBS/CBS CST | 18 | 3 | `digits` | 0.75 | same publication |
| `cst_icms.csv` | ICMS CST (origin digit + treatment) | 99 | 3 | `digits` | 0.75 | EFD ICMS/IPI Guia Prático 3.2.2, table 4.3.1 |
| `csosn.csv` | CSOSN | 10 | 3 | `digits` | 0.75 | same, table 4.3.1 |
| `cst_icms_taxation.csv` | ICMS CST treatment digits only | 11 | 2 | `digits` | 0.50 | same, table 4.3.1 |
| `cst_ipi.csv` | IPI CST | 14 | 2 | `digits` | 0.55 | same, table 4.3.2 |
| `cst_pis_cofins.csv` | PIS/COFINS CST | 33 | 2 | `digits` | 0.55 | EFD-Contribuições CST tables II and III |

Every CSV has the columns `code,description,valid_from,valid_to`. Only `code` is read (`reference_catalog.py` `ReferenceCatalogs.from_directory`). Before lookup a cell is normalized to the catalog's width:

- Leading zeros that Excel dropped are restored.
- `cfop` also accepts the form `5102/XX`.
- `punctuated_digits` strips `.`, `-`, `/` and spaces (so `8471.30.12` works).

Specificity measures how much a match proves. CFOP, NCM and IBGE codes come from large, nationally unique domains (1.0). Short CST domains collide across taxes, so they are discounted. At the default `--final-threshold` of 0.90, only the specificity-1.0 catalogs can decide a mapping on their own. "Reference catalogs" in [ARCHITECTURE.md](ARCHITECTURE.md) covers the rules.

`manifest.json` holds:

- `schema_version`: 1.
- `generated_at`: `2026-08-04T22:14:31.359847+00:00`.
- `catalogs`: one entry per catalog with `file`, `width`, `normalizer`, `specificity`, `source_version`, `source_url` and `sha256`.

On load, each CSV's SHA-256 must match its manifest entry, or the mapper stops and suggests `update_reference_data.py` or `--no-reference-data`. Every report records the manifest's own hash as `reference_manifest_sha256`. `--reference-data` defaults to the `reference_data` directory next to `invoice_mapper.py`, so the mapper finds it from any working directory.

Two quirks:

- `ibs_cbs_cst.csv` has 19 rows but 18 codes. One row has a blank code, which the loader drops.
- In `ibs_cbs_classification.csv`, codes 220001-220003 have `valid_to` 2026-01-01 but still count as valid, because dates are not read.

**Refreshing.** `update_reference_data.py` needs network access, and it overwrites files:

- It downloads CFOP, NCM, the municipality table and the IBS/CBS workbook (which yields two CSVs).
- It writes the five CST/CSOSN tables from lists in the script itself.
- It rewrites every CSV and `manifest.json`. `--output` defaults to `reference_data` relative to the current directory, so a run from the repo root replaces the snapshot in place.

The IBS/CBS sheet names are hard-coded to the 2026-06-01 publication, so a newer publication will probably need a code change. To refresh, write to a scratch directory first, compare, then copy across and commit the CSVs and the manifest together:

```bash
python update_reference_data.py --output /tmp/reference_data_new
diff -r reference_data /tmp/reference_data_new
```

A new snapshot changes `reference_manifest_sha256` in future reports. Apply does not check that hash, so existing reports still apply.

## Regenerating the outputs

Run these from the repo root. Offline mapping, apply and `mapeval` never contact OpenAI.

To rebuild the four deliverables from the committed reports, run the commands below. They print `mapped_columns=19`, `18`, `62` and `67` respectively, which matches the committed workbooks:

```bash
python apply_invoice_mapping.py data/clients/datasul_maio.xlsx data/reports/mapping_datasul_full.xlsx --output data/mapped/datasul_mapped.xlsx
python apply_invoice_mapping.py data/clients/oracle.xlsx data/reports/mapping_oracle_full.xlsx --output data/mapped/oracle_mapped.xlsx
python apply_invoice_mapping.py data/clients/sap_procosa.XLSX data/reports/mapping_sap_procosa_full.xlsx --output data/mapped/sap_procosa_mapped.xlsx
python apply_invoice_mapping.py data/clients/sap_terumo.XLSX data/reports/mapping_sap_terumo_full.xlsx --output data/mapped/sap_terumo_mapped.xlsx
```

To produce a header-only report without OpenAI:

```bash
python invoice_mapper.py data/clients/sap_terumo.XLSX --offline --output /tmp/mapping_sap_terumo_offline.xlsx
```

Offline reports are a degenerate regime. Every unresolved model column becomes review with `method` `name_only`, so there are no no_match rows while unused client columns remain. They are not comparable with the committed online reports, and their figures are never the product figure.

An online run sends data to OpenAI (see [Confidentiality](#confidentiality)) and needs `OPENAI_API_KEY`:

```bash
python invoice_mapper.py data/clients/oracle.xlsx --output data/reports/mapping_oracle_rerun.xlsx
```

Write online runs to a new name. Overwriting a committed report has three effects:

- It erases any reviewer verdicts in that report.
- It moves the `mapeval` baseline that `tests/test_mapeval.py` pins, because the resolver is not deterministic.
- It needs a matching regenerated workbook in `data/mapped/`.

The tests in `tests/test_mapeval.py` that need the workbooks skip when `data/keys/de_para_oracle.xlsx` is absent, so the rest of the suite runs without the data.
