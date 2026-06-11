#!/usr/bin/env python3
"""Scrape a Letterboxd user's watched films and output as CSV."""

import csv
import os
import time

from curl_cffi import requests as curl_requests
from letterboxdpy.core.exceptions import AccessDeniedError
from letterboxdpy.core.scraper import Scraper
from letterboxdpy.user import User

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


def scrape():
    """Fetch films and diary data, returning CSV rows."""
    user = User(USERNAME)
    films_data = user.get_films()
    movies = films_data.get("movies", {})
    print(f"  Found {len(movies)} films")

    try:
        diary_data = user.pages.diary.get_diary(fetch_runtime=True)
        diary_entries = diary_data if isinstance(diary_data, dict) else {}
        print(f"  Found {len(diary_entries.get('entries', {}))} diary entries")
    except AccessDeniedError:
        # Don't write a CSV stripped of watch dates; let the caller retry.
        raise
    except Exception as e:
        print(f"  Warning: could not fetch diary: {e}")
        diary_entries = {}

    diary_by_slug = build_diary_lookup(diary_entries)

    rows = []
    for slug, info in movies.items():
        diary = diary_by_slug.get(slug, {})
        actions = diary.get("actions", {})
        rating = info.get("rating")
        rows.append({
            "Name": info["name"],
            "Year": info.get("year", ""),
            "Rating": rating if rating is not None else "",
            "Liked": "Yes" if info.get("liked") else "",
            "Watch Date": format_date(diary.get("date", {})),
            "Runtime": diary.get("runtime") if diary.get("runtime") is not None else "",
            "Rewatched": "Yes" if actions.get("rewatched") else "",
            "Letterboxd URI": f"https://letterboxd.com/film/{slug}/",
        })
    return rows


def scrape_with_retries():
    """Run scrape(), rotating browser fingerprints on Cloudflare 403s."""
    for attempt, profile in enumerate(IMPERSONATE_PROFILES):
        if attempt:
            time.sleep(RETRY_DELAY_SECONDS)
            print(f"Retrying with '{profile}' fingerprint...")
        ImpersonateSession.profile = profile
        Scraper.set_instance(ImpersonateSession())
        try:
            return scrape()
        except AccessDeniedError as e:
            print(f"  Blocked with '{profile}' fingerprint: {e}")
    raise SystemExit(
        "All browser fingerprints were blocked; this IP is likely flagged "
        "by Letterboxd. A rerun lands on a different runner IP and may pass."
    )


def main():
    print(f"Scraping films for {USERNAME}...")
    rows = scrape_with_retries()

    rows.sort(key=lambda r: r["Name"].lower())

    with open(OUTPUT_FILE, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDNAMES)
        writer.writeheader()
        writer.writerows(rows)

    print(f"Wrote {len(rows)} films to {OUTPUT_FILE}")


if __name__ == "__main__":
    main()
