#!/usr/bin/env python3
"""Scrape a Letterboxd user's watched films and output as CSV."""

import csv
import os

from letterboxdpy.user import User

USERNAME = os.environ.get("LETTERBOXD_USERNAME", "coanda_effect")
OUTPUT_FILE = os.environ.get("OUTPUT_FILE", "letterboxd-watched.csv")


def main():
    print(f"Scraping films for {USERNAME}...")

    user = User(USERNAME)
    data = user.get_films()
    movies = data.get("movies", {})
    print(f"  Found {len(movies)} films")

    rows = sorted(
        [{"Name": info["name"], "Year": info.get("year", "")} for info in movies.values()],
        key=lambda r: r["Name"].lower(),
    )

    with open(OUTPUT_FILE, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["Name", "Year"])
        writer.writeheader()
        writer.writerows(rows)

    print(f"Wrote {len(rows)} films to {OUTPUT_FILE}")


if __name__ == "__main__":
    main()
