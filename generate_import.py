#!/usr/bin/env python3
"""Generate a Letterboxd-compatible import CSV for watched films missing diary entries.

This creates a CSV you can upload at https://letterboxd.com/import/ to bulk-log
films to your diary. Only films without an existing diary entry are included.
"""

import csv
import os
from datetime import date

from letterboxdpy.user import User

USERNAME = os.environ.get("LETTERBOXD_USERNAME", "coanda_effect")
OUTPUT_FILE = os.environ.get("IMPORT_FILE", "letterboxd-import.csv")


def format_date(date_dict):
    """Convert a {year, month, day} dict to YYYY-MM-DD string."""
    if not date_dict or not date_dict.get("year"):
        return ""
    return f"{date_dict['year']}-{date_dict['month']:02d}-{date_dict['day']:02d}"


def main():
    print(f"Fetching data for {USERNAME}...")

    user = User(USERNAME)
    films_data = user.get_films()
    movies = films_data.get("movies", {})
    print(f"  Found {len(movies)} watched films")

    try:
        diary_data = user.pages.diary.get_diary()
        diary_entries = diary_data if isinstance(diary_data, dict) else {}
        diary_slugs = {
            entry.get("slug")
            for entry in diary_entries.get("entries", {}).values()
            if entry.get("slug")
        }
        print(f"  Found {len(diary_slugs)} films with diary entries")
    except Exception as e:
        print(f"  Warning: could not fetch diary: {e}")
        diary_slugs = set()

    # Films that are watched but have no diary entry
    unlogged = {slug: info for slug, info in movies.items() if slug not in diary_slugs}
    print(f"  {len(unlogged)} films need diary entries")

    if not unlogged:
        print("Nothing to import!")
        return

    today = date.today().isoformat()

    # Letterboxd import CSV format
    rows = []
    for slug, info in unlogged.items():
        rating = info.get("rating")
        # Letterboxd import expects Rating10 (1-10 scale) or Rating (1-5 scale)
        row = {
            "Title": info["name"],
            "Year": info.get("year", ""),
            "WatchedDate": today,
            "Rating10": int(rating * 2) if rating else "",
        }
        rows.append(row)

    rows.sort(key=lambda r: r["Title"].lower())

    with open(OUTPUT_FILE, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["Title", "Year", "WatchedDate", "Rating10"])
        writer.writeheader()
        writer.writerows(rows)

    print(f"\nWrote {len(rows)} films to {OUTPUT_FILE}")
    print(f"Upload this file at: https://letterboxd.com/import/")


if __name__ == "__main__":
    main()
