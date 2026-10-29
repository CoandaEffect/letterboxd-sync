#!/usr/bin/env python3
"""Scrape a Letterboxd user's watched films and output as CSV."""

import csv
import os
from pathlib import Path
import tempfile
import time

from curl_cffi import requests as curl_requests
from letterboxdpy.core.exceptions import AccessDeniedError
from letterboxdpy.core.scraper import Scraper
from letterboxdpy.pages.user_diary import UserDiary
from letterboxdpy.pages.user_films import UserFilms
from letterboxdpy.url import FilmURL

USERNAME = os.environ.get("LETTERBOXD_USERNAME", "coanda_effect")
OUTPUT_FILE = os.environ.get("OUTPUT_FILE", "letterboxd-watched.csv")

# Letterboxd (Cloudflare) blocks some datacenter IPs, which intermittently
# 403s this scrape on GitHub-hosted runners. The block decision factors in the
# TLS fingerprint, so when one browser profile is denied we retry the whole
# sync under the others before giving up.
IMPERSONATE_PROFILES = ["chrome", "safari", "firefox", "safari_ios"]
RETRY_DELAY_SECONDS = 15

FIELDNAMES = [
    "Name", "Year", "Rating", "Liked",
    "Watch Date", "Runtime", "Rewatched", "Letterboxd URI",
]


class ImpersonateSession(curl_requests.Session):
    """curl_cffi session that forces a specific browser fingerprint."""

    profile = "chrome"

    def get(self, url, **kwargs):
        kwargs["impersonate"] = self.profile
        return super().get(url, **kwargs)


def format_date(date_value):
    """Convert a date value (dict or string) to YYYY-MM-DD string."""
    if not date_value:
        return ""
    if isinstance(date_value, str):
        return date_value
    if isinstance(date_value, dict) and date_value.get("year"):
        return f"{date_value['year']}-{date_value['month']:02d}-{date_value['day']:02d}"
    return ""


def build_diary_lookup(diary_data):
    """Build a slug -> most-recent-diary-entry lookup dict."""
    diary_by_slug = {}
    for entry in diary_data.get("entries", {}).values():
        slug = entry.get("slug", "")
        if not slug:
            continue
        entry_date = format_date(entry.get("date", {}))
        existing_date = format_date(diary_by_slug[slug].get("date", {})) if slug in diary_by_slug else ""
        if entry_date > existing_date:
            diary_by_slug[slug] = entry
    return diary_by_slug


def read_previous_rows():
    if not Path(OUTPUT_FILE).exists():
        return []
    with open(OUTPUT_FILE, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def scrape(previous_rows):
    """Fetch films and diary data, returning CSV rows."""
    # These page clients do not fetch the unrelated profile, favorites, etc.
    films_data = UserFilms(USERNAME).get_films()
    movies = films_data.get("movies", {})
    print(f"  Found {len(movies)} films")

    # Any diary failure must fail the sync, not erase every watch date.
    # Reuse known runtimes rather than requesting every diary film each run.
    diary_entries = UserDiary(USERNAME).get_diary(fetch_runtime=False)
    print(f"  Found {len(diary_entries['entries'])} diary entries")
    known_runtimes = {
        row["Letterboxd URI"]: row["Runtime"]
        for row in previous_rows if row.get("Runtime")
    }

    diary_by_slug = build_diary_lookup(diary_entries)

    rows = []
    for slug, info in movies.items():
        diary = diary_by_slug.get(slug, {})
        actions = diary.get("actions", {})
        rating = info.get("rating")
        uri = f"https://letterboxd.com/film/{slug}/"
        runtime = diary.get("runtime") or known_runtimes.get(uri, "")
        if diary and not runtime:
            runtime = FilmURL.json(slug).run_time or ""
        rows.append({
            "Name": info["name"],
            "Year": info.get("year", ""),
            "Rating": rating if rating is not None else "",
            "Liked": "Yes" if info.get("liked") else "",
            "Watch Date": format_date(diary.get("date", {})),
            "Runtime": runtime,
            "Rewatched": "Yes" if actions.get("rewatched") else "",
            "Letterboxd URI": uri,
        })
    return rows


def scrape_with_retries(previous_rows):
    """Run scrape(), rotating browser fingerprints on Cloudflare 403s."""
    for attempt, profile in enumerate(IMPERSONATE_PROFILES):
        if attempt:
            time.sleep(RETRY_DELAY_SECONDS)
            print(f"Retrying with '{profile}' fingerprint...")
        ImpersonateSession.profile = profile
        # Set only for this session; do not log proxy URLs or credentials.
        proxy = os.environ.get("LETTERBOXD_PROXY")
        session = ImpersonateSession(proxy=proxy) if proxy else ImpersonateSession()
        Scraper.set_instance(session)
        try:
            return scrape(previous_rows)
        except AccessDeniedError as e:
            print(f"  Blocked with '{profile}' fingerprint: {e}")
        finally:
            session.close()
    raise SystemExit(
        "All browser fingerprints were blocked; this IP is likely flagged "
        "by Letterboxd. A rerun lands on a different runner IP and may pass."
    )


def validate_rows(rows, previous_rows):
    """Refuse empty/partial results before touching the published CSV."""
    if not rows:
        raise ValueError("Scrape returned no films; keeping the previous CSV")
    by_uri = {row["Letterboxd URI"]: row for row in rows}
    if len(by_uri) != len(rows):
        raise ValueError("Scrape returned duplicate film URIs")
    missing = [row["Name"] for row in previous_rows
               if row["Letterboxd URI"] not in by_uri]
    if missing:
        raise ValueError(f"Scrape lost previously exported films: {missing}")
    lost_dates = [row["Name"] for row in previous_rows
                  if row.get("Watch Date")
                  and not by_uri[row["Letterboxd URI"]].get("Watch Date")]
    if lost_dates:
        raise ValueError(f"Scrape lost previously exported watch dates: {lost_dates}")


def write_rows(rows):
    """Replace the CSV only after the full file is safely written."""
    target = Path(OUTPUT_FILE)
    temp_path = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", newline="", encoding="utf-8", dir=target.parent,
            prefix=f".{target.name}.", delete=False,
        ) as f:
            temp_path = Path(f.name)
            writer = csv.DictWriter(f, fieldnames=FIELDNAMES)
            writer.writeheader()
            writer.writerows(rows)
        os.replace(temp_path, target)
    finally:
        if temp_path is not None:
            temp_path.unlink(missing_ok=True)


def main():
    print(f"Scraping films for {USERNAME}...")
    previous_rows = read_previous_rows()
    rows = scrape_with_retries(previous_rows)
    validate_rows(rows, previous_rows)

    rows.sort(key=lambda r: r["Name"].lower())

    write_rows(rows)

    print(f"Wrote {len(rows)} films to {OUTPUT_FILE}")


if __name__ == "__main__":
    main()
