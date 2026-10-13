# European Artist Visibility Project

This is a standalone data-preparation project for studying the historical visibility of European artists in the English Google Books N-gram corpus.

## Project contents

- `scripts/build_artist_dictionary.mjs` builds the Excel artist dictionary.
- `scripts/build_project_proposal.py` builds the short Word proposal.
- `scripts/ngram_sample.py` plans targeted downloads, resumes and verifies raw shards, and validates exact-name yearly frequencies.
- `tests/test_ngram_sample.py` tests the download and validation workflow offline.
- `docs/ngram_workflow.md` contains the Chinese step-by-step workflow and interpretation notes.
- `data/wikidata/` contains the downloaded Wikidata source records.
- `docs/` contains the construction plan and submitted proposal.
- `outputs/` contains the generated artist dictionary.

The current baseline contains 15 movements and 1,370 European painters. Each artist has a Wikidata QID, canonical English name, birth and death years, nationality, movement mapping, and Getty ULAN ID.

## Open in PyCharm

Open this directory as a separate project. Create a new Python interpreter for it instead of reusing the `2221lab` project environment.

## Ngram download and small-sample validation

Use Python 3.9+ from the project root. This workflow uses only the standard library; no Spark, HDFS, Excel package, or additional installation is required for the smoke test.

```bash
# One-artist smoke test: about 501 MiB, English corpus 20200217, years 1800–2019.
python scripts/ngram_sample.py plan --artists Q296 --manifest data/ngram/smoke_manifest.json
python scripts/ngram_sample.py download --manifest data/ngram/smoke_manifest.json --max-gib 1
python scripts/ngram_sample.py validate --manifest data/ngram/smoke_manifest.json --output outputs/ngram_smoke --require-all

# A 25-artist download plan only: inspect cost before downloading.
python scripts/ngram_sample.py plan --sample-size 25

# Synthetic offline tests; localhost socket access is required.
python -m unittest discover -s tests -v
```

Open `outputs/ngram_smoke/report.html` to view the result. Read the [complete workflow](docs/ngram_workflow.md) before scaling up. Raw data and generated Ngram reports are Git-ignored; no downloaded corpus files should be pushed to GitHub.

## Python setup

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python scripts/build_project_proposal.py
```

## Dictionary build

The spreadsheet builder uses `@oai/artifact-tool`. Run it in an environment where that package is available:

```bash
node scripts/build_artist_dictionary.mjs
```

Generated files are written to `outputs/`. Preview files are written to `.tmp/`.
