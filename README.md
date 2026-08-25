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

The default output is `mapping_<client-file>.xlsx`. Its `Mappings` sheet records the selected source column, locally computed name/data/final scores, LLM scores for audit, catalog evidence, status, and reason. `Run Metadata` records file hashes and run settings so stale reports can be rejected when applied.

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

The output follows the model's column order. Mapping metadata and scores—including the separately reported LLM audit scores—occupy the first rows; client data begins at Excel row 19. Confidence values are stored as numbers and displayed as percentages. By default, only `accepted` and reviewer-confirmed mappings are populated. Unconfirmed `review` columns remain blank.

Reviewer values supported in the mapping report are `correct`, `incorrect` with `correct_client_column`, and `no_mapping_exists`.

Use `--include-review` only to create an explicitly unsafe workbook containing unconfirmed proposals.

## Fiscal reference data

`reference_data/` contains checksummed snapshots for CFOP, NCM, municipality, CST, CSOSN, and IBS/CBS codes. Refresh them separately with:

```bash
python update_reference_data.py
```

Review and commit the updated CSV files and `manifest.json` together.

## Tests

```bash
pytest -q
```
