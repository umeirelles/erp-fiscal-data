# Brazilian invoice column mapper

Maps client invoice exports to the canonical columns in `model.xlsx`. The included SAP, Datasul, and Oracle files are examples; the mapper does not contain layout-specific column mappings.

## How it works

For each model column, from left to right:

1. Compare column names using normalization and OpenAI embeddings.
2. Accept clear matches with a sufficient lead over the runner-up.
3. For uncertain matches, evaluate the names together with representative values, data shape, fiscal-code catalogs, and relevant arithmetic relationships.
4. Return `accepted`, `review`, or `no_match` with confidence scores and a reason.

Accepted source columns are not reused. Empty and all-zero values are weak evidence. A bogus header can still produce a viable mapping when its content is distinctive, but uncertain cases remain `review`.

Confidence values are decision signals, not calibrated probabilities. Review low-confidence mappings before using them in tax calculations.

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
export OPENAI_API_KEY='your-key'
```

The default low-cost LLM is `gpt-4o-mini`. Never commit the API key; `.env` is ignored by Git.

## Generate a mapping report

```bash
python invoice_mapper.py oracle.xlsx --model model.xlsx
```

The default output is `mapping_<client-file>.xlsx`. Its `Unmatched Client Columns` sheet lists every client column whose data did
not reach the base model, so a reviewer can judge whether each is genuinely
irrelevant or a real miss and feed the answer back. Nothing is omitted; a
shortlist is marked, and everything else carries a sentence explaining why it
was set aside. A long list is expected — the base model is a superset and a
client supplies only part of it.

Its `Mappings` sheet records the selected source column, locally computed name/data/final scores, LLM scores for audit, catalog evidence, status, and reason.

Three columns answer *which step of the analysis produced this mapping*: `supported_by` lists every named step that found evidence for the chosen column, `decided_by` names the strongest of them, and `vetoed_by` names any guard that refused an otherwise viable match. The steps are defined in `mapping_steps.py`, which is the single place they are named — seven evidence steps (`resolver`, `lexical`, `semantic`, `profile`, `identifier`, `catalog`, `arithmetic`) and four guards (`vacuity`, `family_conflict`, `underspecified`, `exclusivity`).

Each evidence step declares whether it **transfers** — whether its reasoning survives an ERP whose vocabulary nobody has seen. `lexical` and `semantic` read the words this client chose and do not; `catalog`, `arithmetic`, `identifier` and `profile` rest on tax law, official code tables and check digits, and do. `resolver` is ranked weakest and marked non-transferring on purpose: it is an adjudicator, not evidence, so `decided_by = resolver` keeps its honest meaning — nothing objective supported this and the model's judgment carried it. The share of accepts whose `decided_by` transfers is the generalization metric for the tool. `Run Metadata` records file hashes and run settings so stale reports can be rejected when applied.

If headers are not on the first row, pass their 1-based positions with `--client-header-row` or `--model-header-row`.

For a no-cost header-only run:

```bash
python invoice_mapper.py oracle.xlsx --offline
```

Only representative values from uncertain columns are sent to OpenAI. Confirm that the client permits this before using online mode. Run `python invoice_mapper.py --help` for all options.

## Create the mapped workbook

```bash
python apply_invoice_mapping.py \
  sap_terumo.XLSX \
  mapping_sap_terumo_full.xlsx \
  --model model.xlsx \
  --output sap_terumo_mapped.xlsx
```

The output carries two sheets. `Mapped Data` follows the model's column order. Mapping metadata and scores—including the separately reported LLM audit scores and, where one exists, the arithmetic proof—occupy the first rows; client data begins at Excel row 20 (`FIRST_DATA_EXCEL_ROW`). Confidence values are stored as numbers and displayed as percentages. By default, only `accepted` and reviewer-confirmed mappings are populated. Unconfirmed `review` columns remain blank.

Reviewer values supported in the mapping report are `correct`, `incorrect` with `correct_client_column`, and `no_mapping_exists`. Only `incorrect` consumes `correct_client_column`; setting it beside any other status raises rather than discarding the answer, and `correct` on a row with no proposed column raises rather than confirming a blank.

### The two review sheets

A report carries two reviewer surfaces, one for each side of the accept gate:

- **Unmatched Client Columns** — the client's columns that no accepted mapping consumed. Answers "is this column really useless, or did we miss something?"
- **Adjudication Queue** — the model's columns that received a real candidate the gate would not deliver, ranked by how close each came to shipping, with sample values from the proposed column. Answers "which of these should we have delivered?"

The queue is a ranked index, not a third place to write: rule on a row in the `Mappings` sheet's existing reviewer columns, which `apply_invoice_mapping.py` already honours. Rows sort by confidence, then by name margin *inside the clamped band only* — a whole class of rows shares one confidence, so that column stops ordering them there. Proposals onto a column with no populated rows sort last whatever their header score, because confirming one ships a blank column.

Use `--include-review` only to create an explicitly unsafe workbook containing unconfirmed proposals.

A client fills a minority of the model and that is the design — `model.xlsx` is a superset covering every field any client might send, so most columns are legitimately empty. Two conveniences follow from that:

- Columns carrying no data are **hidden**, so the delivered ones sit side by side rather than scattered across 227. Nothing is removed, no value changes, one click unhides, and anything reading the file programmatically is unaffected.
- The `Coverage` sheet indexes every model column into three states, sorted so the work comes first: **delivered**, **awaiting decision** (a candidate exists and a reviewer has to rule on it — these are the `Adjudication Queue` rows), and **not in this client's export**. That last state is a complete and correct answer, not a gap.

`Mapped Data` cannot answer "what is not here and should be", because every column it fills is accepted or reviewer-confirmed by construction. `Coverage` is the sheet to read first.

## Fiscal reference data

`reference_data/` contains checksummed snapshots for CFOP, NCM, municipality, CST, CSOSN, and IBS/CBS codes. Refresh them separately with:

```bash
python update_reference_data.py
```

Review and commit the updated CSV files and `manifest.json` together.

## Scoring a run

`mapeval` scores the archived reports against the `de_para_*.xlsx` answer keys:

```bash
python -m mapeval                 # all clients
python -m mapeval --show-losses   # list every miss, wrong column and withheld row
```

It reports two recall figures and they are not interchangeable:

- **found** — the mapper picked the client column the key names. Measures retrieval.
- **delivered** — that pick also cleared the accept gate, so `apply_invoice_mapping.py`
  populates it. Measures what the customer actually receives.

Delivered is currently 32/56 against found 43/56: eleven correct mappings are
located and then withheld, every one of them in the confidence band just under
the 0.90 accept threshold. Lowering the threshold is not the fix — on the keyed
clients, moving the gate from 0.90 to 0.83 recovers those eleven and admits 116
proposals the keys cannot adjudicate. Closing this gap needs reviewer
adjudication of the sub-gate band, not a looser constant.

The harness exits non-zero on any fabrication — a mapping delivered onto a
client column with zero populated rows.

`sap_procosa` and `sap_terumo` have no column-level key, so no precision or
recall figure is printed for them; only the accept count and the adjudication
backlog.

## Tests

```bash
pytest -q
```
