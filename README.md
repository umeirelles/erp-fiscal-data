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

Its `Mappings` sheet records the selected source column, locally computed name/data/final scores, LLM scores for audit, catalog evidence, status, and reason. `Run Metadata` records file hashes and run settings so stale reports can be rejected when applied.

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

The output follows the model's column order. Mapping metadata and scores—including the separately reported LLM audit scores and, where one exists, the arithmetic proof—occupy the first rows; client data begins at Excel row 20 (`FIRST_DATA_EXCEL_ROW`). Confidence values are stored as numbers and displayed as percentages. By default, only `accepted` and reviewer-confirmed mappings are populated. Unconfirmed `review` columns remain blank.

Reviewer values supported in the mapping report are `correct`, `incorrect` with `correct_client_column`, and `no_mapping_exists`. Only `incorrect` consumes `correct_client_column`; setting it beside any other status raises rather than discarding the answer, and `correct` on a row with no proposed column raises rather than confirming a blank.

### The two review sheets

A report carries two reviewer surfaces, one for each side of the accept gate:

- **Unmatched Client Columns** — the client's columns that no accepted mapping consumed. Answers "is this column really useless, or did we miss something?"
- **Adjudication Queue** — the model's columns that received a real candidate the gate would not deliver, ranked by how close each came to shipping, with sample values from the proposed column. Answers "which of these should we have delivered?"

The queue is a ranked index, not a third place to write: rule on a row in the `Mappings` sheet's existing reviewer columns, which `apply_invoice_mapping.py` already honours. Rows sort by confidence, then by name margin *inside the clamped band only* — a whole class of rows shares one confidence, so that column stops ordering them there. Proposals onto a column with no populated rows sort last whatever their header score, because confirming one ships a blank column.

Use `--include-review` only to create an explicitly unsafe workbook containing unconfirmed proposals.

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

Delivered is currently 31/56 against found 42/56: eleven correct mappings are
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
