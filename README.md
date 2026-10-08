# cbs-base-fiscal: invoice column mapper

An internal tool for a Brazilian tax advisory firm. Clients send invoice spreadsheets exported from their ERP (SAP, Datasul, Oracle, ...), and every ERP and every client lays them out differently. The tool maps a client's columns onto the firm's fixed base model, `data/model.xlsx`. It never changes a value. A downstream application reads only the base model.

The base model is a deliberately oversized superset. It has 227 columns covering every field any client might send, and it starts with the new reform taxes CBS and IBS (the federal and subnational consumption taxes that replace PIS/COFINS and ICMS/ISS). A client fills only part of it, so an empty model column is often the correct answer.

## Pipeline at a glance

```text
data/clients/<export>.xlsx
      │  python invoice_mapper.py
      ▼
data/reports/mapping_<client>.xlsx     one row per model column: accepted / review / no_match
      │  human review: reviewer columns in the Mappings sheet
      │  python apply_invoice_mapping.py
      ▼
data/mapped/<client>_mapped.xlsx       sheets: Mapped Data, Coverage

python -m mapeval                      scores the archived reports against the answer keys (read-only)
```

1. **Map** (`invoice_mapper.py` `map_columns`). Pass 1 accepts a client column on its header alone. It does this when the header similarity (OpenAI embeddings online, lexical matching offline) is at least 0.90, leads the runner-up by at least 0.05, and no guard objects. Pass 2 takes the remaining model columns and weighs the values: tax arithmetic (base × rate = value), official code catalogs, overlap with the model's sample values, and check digits on identifiers. An LLM (`gpt-4o-mini`) usually proposes the column, then local code recomputes the confidence and applies the 0.90 accept gate. Each model column ends up `accepted`, `review` or `no_match`, with a reason. The `supported_by` / `decided_by` / `vetoed_by` columns name the analysis steps behind the result; `mapping_steps.py` defines those steps. Confidences are decision signals, not calibrated probabilities.
2. **Review.** A reviewer rules on rows in the report's `Mappings` sheet (`correct`, `incorrect` + `correct_client_column`, or `no_mapping_exists`). The `Adjudication Queue` sheet ranks the proposals the gate withheld. `Unmatched Client Columns` lists client columns whose data reached no model column. The archived procosa report predates both of these sheets and has only `Mappings` and `Run Metadata`.
3. **Apply** (`apply_invoice_mapping.py`). Copies client values unchanged into model column order. It fills only accepted and reviewer-confirmed columns. Its `Coverage` sheet marks each model column as delivered, awaiting decision, or not in this client's export.
4. **Measure** (`mapeval/`). Read-only scoring of the archived reports against the client-written answer keys. It exits 1 on any fabrication.

## Repository layout

```text
invoice_mapper.py          mapper CLI and algorithm: header scoring, evidence, accept gate, report writer
mapping_steps.py           registry of the named evidence steps and guards used in supported_by/decided_by/vetoed_by
reference_catalog.py       loads reference_data/, verifies checksums, scores a column against each code catalog
apply_invoice_mapping.py   apply CLI: client export + mapping report -> mapped workbook
backfill_mapping_steps.py  one-off: added the step columns in place to reports written before they existed
update_reference_data.py   rebuilds reference_data/ from official sources (network)
mapeval/                   evaluation harness, run as `python -m mapeval`
docs/                      the four detailed docs listed under "Where to read next"
tests/                     pytest suite; runs offline
reference_data/            10 fiscal-code catalogs (CSV) + manifest.json with their checksums
data/model.xlsx            the base model: 227 headers and 209 sample rows (immutable for now)
data/clients/              four real client exports: datasul_maio, oracle, sap_procosa, sap_terumo
data/keys/                 de-para files: datasul and oracle are column answer keys; procosa's is a record-level spec, not a key
data/reports/              archived mapper reports, mapping_<client>_full.xlsx (procosa's holds 26 reviewer verdicts)
data/mapped/               apply outputs, <client>_mapped.xlsx
requirements.txt           dependency ranges, no lockfile (pyproject.toml holds only pytest settings)
```

A *de-para* ("from-to") is a correspondence table. Here it is a spreadsheet in which the client marked model columns with the letter of its own column that supplies them.

## Setup

Developed and tested on Python 3.13.1 (pandas 2.3.3, openpyxl 3.1.5, openai 2.30.0).

```bash
python3.13 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
export OPENAI_API_KEY='...'      # online mode only
```

No code loads `.env`, so the key must be exported in the shell. `.env` and `.venv/` are git-ignored; never commit a key. The models default to `gpt-4o-mini` and `text-embedding-3-large`. Override them with `--llm-model` / `--embedding-model`, or with `OPENAI_MODEL` / `OPENAI_EMBEDDING_MODEL`.

Run every command from the repo root. The `--model` default (`data/model.xlsx`), mapeval's workbook paths and the reference-data refresh all resolve against the current directory.

## Everyday commands

**Mapping report, online** (calls OpenAI, costs money, sends client data out; see [Confidential data](#confidential-data)):

```bash
python invoice_mapper.py data/clients/oracle.xlsx --output data/reports/mapping_oracle.xlsx
```

Without `--output`, the report lands in the current directory as `mapping_<client file stem>.xlsx`. The writer overwrites existing files without asking and always starts the reviewer columns blank. Never point `--output` at a report that holds verdicts, such as `data/reports/mapping_sap_procosa_full.xlsx`. If the headers are not on row 1, pass `--client-header-row N` / `--model-header-row N` (1-based), and pass the same flags to apply. `python invoice_mapper.py --help` lists every option.

**Mapping report, offline** (no network, no cost):

```bash
python invoice_mapper.py data/clients/oracle.xlsx --offline --output /tmp/mapping_oracle_offline.xlsx
```

Offline mode scores headers lexically and skips the LLM. Pass 1 still accepts on headers, but pass 2 never accepts or rejects: every model column left for it becomes `review` on its best header match. Use it for smoke tests and for work on header logic. Treat its reports as scratch output: don't commit them or quote their counts as a product figure.

**Mapped workbook:**

```bash
python apply_invoice_mapping.py data/clients/sap_terumo.XLSX data/reports/mapping_sap_terumo_full.xlsx \
  --output data/mapped/sap_terumo_mapped.xlsx
```

Apply refuses a report whose recorded SHA-256 of the client or model file differs from the files you pass. `--include-review` also fills unconfirmed proposals. That produces an explicitly unsafe workbook for review, never a deliverable.

**Evaluation and tests:**

```bash
python -m mapeval                     # all four clients
python -m mapeval --show-losses       # list every withheld, missed and wrong-column row
python -m mapeval --client datasul    # one client (repeatable)
python -m pytest -q                   # 137 tests, offline, about 7 s
```

mapeval scores the archived `data/reports/mapping_<client>_full.xlsx` files listed in `mapeval/cli.py` `CLIENTS`; it never runs the mapper. Some tests pin those archived figures, so a regenerated `_full` report fails them by design until someone updates the pins on purpose.

**Refreshing reference data** (network; downloads from Brazilian government portals):

```bash
python update_reference_data.py
```

This rewrites `reference_data/` in place: CFOP (4-digit code for the type of operation), NCM (8-digit product classification), IBGE municipality codes, IBS/CBS tax-situation and classification codes, and the hard-coded CST/CSOSN tables (CST is the tax-situation code each tax line carries; CSOSN is its variant for small companies in the Simples Nacional regime). Review the diff, then commit the CSVs and `manifest.json` together. The mapper refuses to start if a catalog does not match its manifest checksum.

## Ground rules

These come from the project owner and hold for every change.

1. **Scope is the matching algorithm**: header interpretation, evidence fusion and column assignment. A UI, reusing a client's mapping across deliveries, and scale/cost are out of scope for now.
2. **The four sample clients are reference examples, not the product.** Production will see ERPs and layouts nobody here has seen. No code may branch on a client name, and nothing gets hand-tuned to these four. Validate a change leave-one-client-out. The keys are a regression test, never a training set.
3. **Prefer evidence that transfers to an unseen ERP.** Tax arithmetic, official code catalogs, and check digits on CNPJ/CPF (company/individual taxpayer IDs) and NF-e (electronic invoice) access keys beat learned header vocabulary. The generalization metric to push up is the share of accepts that rest on header-independent evidence.
4. **Fabrication is the failure that matters**, meaning a model column filled from data the client does not have. A fabricated `Base CBS` is far worse than an empty one, so accept precision outranks recall. The 0.90 gate stays. Reviewer adjudication, not a lower threshold, is how more of the withheld band gets delivered.
5. **Partial coverage is the design.** `no_match` is often correct. Never use 227 as a denominator and never report "N of 227 filled". Describe a model column as *delivered*, *awaiting decision* or *not in this client's export*, never as "missing", a "gap" or "incomplete".
6. **Quote delivered recall** when describing the product. Quote found recall only when discussing retrieval, and never present offline-mode numbers as the product figure.
7. **`data/model.xlsx` is immutable for now.** Do not propose trimming or restructuring it.

## Confidential data

The repo contains real client data: CNPJs, CPFs, NF-e access keys and invoice values. It is shared with internal developers on purpose. Keep it on approved machines and never push it to a public remote. Online mode sends every model and client header to the OpenAI embeddings API. For each pass-2 model column it asks the chat model about, it also sends up to 8 sample values (`--sample-size`) and summary statistics (counts, min, quartiles, max) from each candidate client column and from the model, with no redaction. Confirm that the client permits this before an online run, and use `--offline` otherwise.

## Current status

*Found* means the mapper picked the client column the answer key names. *Delivered* means that pick also cleared the accept gate, so apply fills it.

- On the two clients with answer keys (datasul and oracle, 56 keyed model columns): **delivered 32/56 (57.1%)**, found 43/56 (76.8%), 0 fabrications. All 11 found-but-withheld columns are `review` rows below the 0.90 gate (final confidence 0.83 to 0.8999).
- sap_procosa shows what adjudication unlocks: 26 reviewer verdicts took it from 45 accepted columns to 62 delivered (15 confirmed proposals + 2 corrections).
- 137 tests pass, offline.

## Where to read next

- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md): how the mapper decides (passes, evidence steps, guards, the accept gate) and how apply builds the workbook.
- [docs/DATA.md](docs/DATA.md): every workbook (model, client exports, keys, report sheets and columns, the mapped workbook) and the reference catalogs.
- [docs/EVALUATION.md](docs/EVALUATION.md): mapeval outcomes, found versus delivered, key corrections, and how to validate a change.
- [docs/KNOWN_ISSUES.md](docs/KNOWN_ISSUES.md): known bugs and places where comments and code disagree.
