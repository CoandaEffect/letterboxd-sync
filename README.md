# letterboxd-sync

Automatically scrapes my [Letterboxd](https://letterboxd.com/coanda_effect/) watched films list every other day and commits the results as a CSV to this repo.

## Raw CSV URL

```
https://raw.githubusercontent.com/CoandaEffect/letterboxd-sync-/main/letterboxd-watched.csv
```

## How it works

- A GitHub Action runs every other day at 6 AM UTC (midnight Mountain)
- It scrapes the films list and diary pages, merges and deduplicates entries
- The resulting `letterboxd-watched.csv` is committed if changed
- Can also be triggered manually via `workflow_dispatch`

## Local usage

```bash
pip install -r requirements.txt
python scrape.py
```

Environment variables:
- `LETTERBOXD_USERNAME` — Letterboxd username (default: `coanda_effect`)
- `OUTPUT_FILE` — output CSV path (default: `letterboxd-watched.csv`)
