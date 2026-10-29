# letterboxd-sync

Automatically scrapes my [Letterboxd](https://letterboxd.com/coanda_effect/) watched films list every other day and commits the results as a CSV to this repo.

## Raw CSV URL

```
https://raw.githubusercontent.com/CoandaEffect/letterboxd-sync/main/letterboxd-watched.csv
```

## How it works

- A GitHub Action runs on odd-numbered days at 6 AM UTC (midnight Mountain during daylight saving time; 11 PM the previous day during standard time). GitHub may delay scheduled runs.
- Letterboxd blocked the default Linux/Azure runners from October 15–27, 2026. Tests on October 29 also found paginated film/diary requests blocked on macOS ARM, macOS Intel, Linux ARM, and Windows runners, including a real-browser test. Changing hosted runner OS alone is not a verified fix.
- It scrapes the films list and diary pages, merges and deduplicates entries
- The resulting `letterboxd-watched.csv` is committed if changed
- Can also be triggered manually via `workflow_dispatch`
- Known runtimes are reused from the existing CSV to reduce requests. Diary errors, missing previously exported films, and lost watch dates fail the sync and preserve the existing CSV. Intentional deletions require reviewing/updating the baseline CSV.
- CSV updates are atomic and overlapping runs are serialized.

## Failure notifications

GitHub sends Actions failure emails according to your personal notification settings; this repo does not send SMTP email. In October 2026, the alerts were delivered but Gmail classified them as spam. In Gmail, create a filter for `from:notifications@github.com subject:letterboxd-sync` and select **Never send it to Spam**. Moving an alert out of Spam recovers that message but does not guarantee future delivery.

Check the Actions run status for sync freshness. A recent watch date or unchanged CSV does not prove the latest sync succeeded.

## Scheduled sync network

Full scheduled scraping needs an allowed outbound network. Configure either:

- **Self-hosted runner:** install a GitHub Actions runner on an always-on machine with a working Letterboxd connection, give it a dedicated label (for example `letterboxd`), and set the repository Actions variable `LETTERBOXD_RUNNER` to that label. This repo runs trusted code from `main`; pull-request tests remain on GitHub-hosted runners.
- **Proxy:** put the proxy URL in the repository Actions secret `LETTERBOXD_PROXY`. It is passed only to the Letterboxd HTTP client; do not put credentials in code, repository variables, or workflow logs. The default hosted runner can then remain in use.

Without either configuration, the workflow retains its existing hosted-runner default and can still fail with access-denied errors. Runtime/film retries change browser profiles on the same IP; they cannot repair an IP-level block.

## Home Assistant Green diagnostic

The [one-shot ARM64 diagnostic](apps/letterboxd-green-smoke/README.md) tests the
production HTTP client on the Green before scheduled App deployment. Its image
is built in GitHub Actions; the Green does not build it or install dependencies.
Passing the diagnostic is a network/client gate, not a completed deployment or
a five-day reliability result. Current rollout gates are in [WORK-STATUS.md](WORK-STATUS.md).

The [guarded Green App](apps/letterboxd-sync/README.md) adds a four-hour idle
baseline, isolated resource-limited worker, phone alerts, and a separate GitHub
health watchdog. It starts inactive and holds its first candidate for review.
The hosted schedule remains active until the Green's first validated publication
and alert checks pass.

## Local usage

```bash
uv pip install -r requirements.txt
python scrape.py
```

Run the offline regression tests with `python -m unittest discover -s tests -v`.

Environment variables:
- `LETTERBOXD_USERNAME` — Letterboxd username (default: `coanda_effect`)
- `OUTPUT_FILE` — output CSV path (default: `letterboxd-watched.csv`)
- `LETTERBOXD_PROXY` — optional proxy URL for Letterboxd requests; treat credentials as secrets
