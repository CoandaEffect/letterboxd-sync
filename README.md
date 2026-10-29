# letterboxd-sync

Automatically scrapes my [Letterboxd](https://letterboxd.com/coanda_effect/) watched films list every other day and commits the results as a CSV to this repo.

## Raw CSV URL

```
https://raw.githubusercontent.com/CoandaEffect/letterboxd-sync/main/letterboxd-watched.csv
```

## How it works

- A GitHub Action runs on odd-numbered days at 6 AM UTC (midnight Mountain during daylight saving time; 11 PM the previous day during standard time). GitHub may delay scheduled runs.
- The sync uses the macOS runner pool; Letterboxd repeatedly blocked the Linux/Azure pool in October 2026 despite browser fingerprint retries.
- It scrapes the films list and diary pages, merges and deduplicates entries
- The resulting `letterboxd-watched.csv` is committed if changed
- Can also be triggered manually via `workflow_dispatch`
- Known runtimes are reused from the existing CSV to reduce requests. Diary errors, missing previously exported films, and lost watch dates fail the sync and preserve the existing CSV. Intentional deletions require reviewing/updating the baseline CSV.
- CSV updates are atomic and overlapping runs are serialized.

## Failure notifications

GitHub sends Actions failure emails according to your personal notification settings; this repo does not send SMTP email. In October 2026, the alerts were delivered but Gmail classified them as spam. In Gmail, create a filter for `from:notifications@github.com subject:letterboxd-sync` and select **Never send it to Spam**. Moving an alert out of Spam recovers that message but does not guarantee future delivery.

Check the Actions run status for sync freshness. A recent watch date or unchanged CSV does not prove the latest sync succeeded.

## Local usage

```bash
pip install -r requirements.txt
python scrape.py
```

Run the offline regression tests with `python -m unittest discover -s tests -v`.

Environment variables:
- `LETTERBOXD_USERNAME` — Letterboxd username (default: `coanda_effect`)
- `OUTPUT_FILE` — output CSV path (default: `letterboxd-watched.csv`)
