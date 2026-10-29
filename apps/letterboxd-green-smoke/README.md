# Letterboxd Green client diagnostic

A one-shot ARM64 test of the production Letterboxd HTTP client. This diagnoses
the inconclusive stdlib HTTP 403 result before any scheduled sync is enabled.
It fetches two pages, checks expected film/diary content, measures local Home
Assistant API responses, and exits. It does not scrape the full account,
publish an export, schedule work, or start the five-day observation.

Use only the prebuilt image after its ARM64 checks pass. See [DOCS.md](DOCS.md).
