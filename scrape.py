#!/usr/bin/env python3
"""Scrape a Letterboxd user's watched films and output as CSV."""

import csv
import os

from letterboxdpy.user import User

USERNAME = os.environ.get("LETTERBOXD_USERNAME", "coanda_effect")
OUTPUT_FILE = os.environ.get("OUTPUT_FILE", "letterboxd-watched.csv")

FIELDNAMES = [
    "Name", "Year", "Rating", "Liked",
    "Watch Date", "Runtime", "Rewatched", "Letterboxd URI",
]


def format_date(date_dict):
    """Convert a {year, month, day} dict to YYYY-MM-DD string."""
    if not date_dict or not date_dict.get("year"):
        return ""
    return f"{date_dict['year']}-{date_dict['month']:02d}-{date_dict['day']:02d}"


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


def main():
    print(f"Scraping films for {USERNAME}...")

    user = User(USERNAME)
    films_data = user.get_films()
    movies = films_data.get("movies", {})
    print(f"  Found {len(movies)} films")

    try:
        diary_data = user.pages.diary.get_diary(fetch_runtime=True)
        diary_entries = diary_data if isinstance(diary_data, dict) else {}
        print(f"  Found {len(diary_entries.get('entries', {}))} diary entries")
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

    rows.sort(key=lambda r: r["Name"].lower())

    with open(OUTPUT_FILE, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDNAMES)
        writer.writeheader()
        writer.writerows(rows)

    print(f"Wrote {len(rows)} films to {OUTPUT_FILE}")


if __name__ == "__main__":
    main()
