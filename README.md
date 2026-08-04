# Invoice column mapper

Maps a client's invoice spreadsheet to the columns of `model.xlsx` in two passes:

1. Compare model and client column names using generic normalization and multilingual OpenAI embeddings.
2. Accept a name match only when its score is high enough and it clearly leads the runner-up.
3. For uncertain matches, add row-shape, fiscal-equation, and versioned official-code evidence to the candidate set.
4. Resolve a uniquely distinctive catalog match locally; otherwise send the compact evidence, names, scores, and representative values to an OpenAI model for a second decision.

The model columns are processed from left to right. An accepted client column is not reused. A low-confidence `review` proposal does not reserve its client column, so an early uncertain decision cannot prevent a later model field from selecting the correct source. Review proposals may therefore reference the same source column until a reviewer confirms one.

The name pass contains no client- or spreadsheet-specific header pairs. It only normalizes reusable language and tax terms, and ignores word order for cases such as `Valor COFINS` / `COFINS Value`, `Data de lançamento` / `Posting date`, and `Base ICMS` / `ICMS Base`. The row-data pass keeps the names and their scores in the decision. Empty or all-zero samples are weak evidence: they do not invalidate name evidence, but they cannot resolve an ambiguous name match by themselves.

The four included exports are validation examples, not a list of supported layouts. A new layout should not require a source-code rule. If a mapping cannot be supported, the mapper can return `review` or `no_match`.

Row-shape retrieval compares general properties such as data type, numeric content, character composition, and typical value length. It does not encode that a particular client header belongs to a particular model field. `--candidates 10` can therefore send up to ten strong name candidates plus ten strong row-shape candidates after duplicates are removed.

A bogus client header can still be mapped when the values have distinctive evidence, such as 44-digit NF-e keys. Because the name offers little support, the final confidence must still clear the stricter threshold; otherwise the viable candidate is returned as `review`.

Explicit tax-family and semantic-role conflicts are reusable guardrails rather than client-layout rules. For example, a rate cannot be automatically accepted as a base, and an IPI field cannot be automatically accepted as ICMS. A conflicting proposal can remain available for review when distinctive content suggests that the client header itself is bogus, but it cannot be automatically accepted.

The row pass also checks conservative fiscal relationships. For a tax family, it tests both common rate representations when comparing `tax value ~= calculation base * rate`; for item totals, it can compare `line value ~= quantity * unit price`. A relationship only becomes decisive when it has enough usable rows and either clearly leads the alternatives or has supporting header evidence. Zero-only columns do not create arithmetic evidence.

## Versioned fiscal reference data

Finite official code lists are a third evidence source for CFOP, NCM, municipality code, CST, CSOSN, and the IBS/CBS tax-classification codes. They are stored under `reference_data/` as checksummed snapshots and are not downloaded during a mapping run.

Each CSV retains `code`, `description`, `valid_from`, and `valid_to`; `manifest.json` records the source URL, source version, normalization method, specificity discount, and SHA-256 checksum. Codes are read as strings so leading zeros survive. The mapper reports `catalog_name`, `catalog_coverage`, and `catalog_confidence` for the selected candidate.

`catalog_coverage` is the observed fraction of nonblank values found in the official table. `catalog_confidence` discounts small collision-prone domains and small samples; neither is claimed to be a calibrated probability. A catalog match is not sufficient by itself when code domains overlap. PIS and COFINS CST use the same domain, and short CST/status values can occur in unrelated integer columns. All-zero code columns are explicitly prevented from becoming catalog evidence.

Refresh the snapshots separately from client processing:

```bash
python update_reference_data.py
```

The updater currently uses the Receita Federal Classif JSON for NCM and NF-e Portal spreadsheets for CFOP, municipalities, and IBS/CBS codes. CST/CSOSN domains are generated from the official SPED definitions. Review and commit the changed CSVs and `manifest.json` together after an update.

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
export OPENAI_API_KEY='your-replacement-key'
```

Never put the API key in this repository or pass it as a command-line argument.

The low-confidence data pass defaults to the older, inexpensive `gpt-4o-mini`. The models can be changed with `OPENAI_EMBEDDING_MODEL` and `OPENAI_MODEL`, or with the corresponding command-line options.

Reference data is enabled by default. Use `--reference-data path/to/snapshot` to select another pinned snapshot or `--no-reference-data` for a comparison run.

## Run

```bash
python invoice_mapper.py oracle.xlsx --model model.xlsx
```

This writes `mapping_oracle.xlsx` with:

- model and client column names and positions;
- name confidence, runner-up confidence, and the margin between them;
- data confidence for low-confidence name matches;
- official catalog name, coverage, and confidence when applicable;
- final confidence, status, method, and reason.

Useful options:

```bash
python invoice_mapper.py datasul_maio.xlsx \
  --name-threshold 0.90 \
  --name-margin 0.05 \
  --final-threshold 0.90 \
  --candidates 10 \
  --sample-size 8 \
  --output mapping_datasul.xlsx
```

`--offline` performs a no-cost dry run using lexical name similarity. It does not call OpenAI or run the row/catalog decision pass:

```bash
python invoice_mapper.py oracle.xlsx --offline --output /tmp/mapping_oracle.xlsx
```

Only the header names and representative values used by the low-confidence second pass are sent to OpenAI. Review whether the client permits those values to leave your environment before running the online mode.

The reported embedding and LLM confidence values are decision signals, not statistically calibrated probabilities. They can be calibrated later using mappings confirmed by reviewers across many independent client layouts.

## Tests

```bash
pytest -q
```

## Create the mapped client workbook

After generating and optionally reviewing a mapping report, apply it to the client export:

```bash
python apply_invoice_mapping.py \
  sap_terumo.XLSX \
  mapping_sap_terumo_full.xlsx \
  --model model.xlsx \
  --output sap_terumo_mapped.xlsx
```

The `Mapped Data` sheet contains:

- an extra `__mapping_info__` column used to label the metadata rows;
- all model columns in their canonical order;
- mapping status, method, reason, source column, and confidence rows at the top;
- confidence values stored numerically and displayed as percentages;
- the mapped client rows beginning at Excel row 17;
- blank cells for model columns without a viable mapping.

By default, only `accepted` and reviewer-confirmed mappings are populated. Unconfirmed `review` proposals remain visible in the metadata rows but their data columns stay blank. This safe default prevents uncertain mappings from entering tax calculations.

To create an explicitly unsafe workbook that also populates unconfirmed review proposals, use:

```bash
python apply_invoice_mapping.py \
  client.xlsx mapping_client.xlsx \
  --include-review \
  --output client_mapped.xlsx
```

Reviewer fields in the mapping report are honored:

- `correct` keeps the proposed client column;
- `incorrect` requires `correct_client_column`, which can contain an exact header, occurrence display name, or 1-based column number;
- `no_mapping_exists` leaves the model column blank.

The script validates the model order, mapping completeness, source headers, corrected columns, and one-to-one column usage before writing any result.
