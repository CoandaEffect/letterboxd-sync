#!/usr/bin/env python3
"""Scrape a Letterboxd user's watched films and output as CSV."""

import csv
import os
import time

import requests
from bs4 import BeautifulSoup

USERNAME = os.environ.get("LETTERBOXD_USERNAME", "coanda_effect")
OUTPUT_FILE = os.environ.get("OUTPUT_FILE", "letterboxd-watched.csv")
BASE_URL = "https://letterboxd.com"
REQUEST_DELAY = 0.4
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-User": "?1",
}

SESSION = requests.Session()
SESSION.headers.update(HEADERS)


def fetch(url):
    """Fetch a URL with polite delay and return a BeautifulSoup object."""
    time.sleep(REQUEST_DELAY)
    resp = SESSION.get(url, timeout=30)
    resp.raise_for_status()
    return BeautifulSoup(resp.text, "html.parser")


def scrape_films_list(username):
    """Scrape /films/ pages. Returns dict of {name: {slug, year}}."""
    films = {}
    page = 1
    while True:
        url = f"{BASE_URL}/{username}/films/page/{page}/"
        soup = fetch(url)
        posters = soup.select("li.poster-container")
        if not posters:
            break
        for li in posters:
            poster = li.select_one("div.film-poster")
            if not poster:
                continue
            slug = poster.get("data-film-slug", "")
            year = poster.get("data-film-release-year", "")
            img = li.select_one("img")
            name = img.get("alt", "").strip() if img else ""
            if name:
                films[name] = {"slug": slug, "year": year}
        page += 1
    return films


def scrape_diary(username):
    """Scrape /films/diary/ pages. Returns dict of {name: {slug, year}}."""
    films = {}
    page = 1
    while True:
        url = f"{BASE_URL}/{username}/films/diary/page/{page}/"
        soup = fetch(url)
        rows = soup.select("tr.diary-entry-row")
        if not rows:
            break
        for row in rows:
            name_cell = row.select_one("td.td-film-details a")
            name = name_cell.get_text(strip=True) if name_cell else ""
            year_cell = row.select_one("td.td-released span")
            year = year_cell.get_text(strip=True) if year_cell else ""
            slug = ""
            if name_cell:
                href = name_cell.get("href", "")
                # href looks like /film/slug/
                parts = [p for p in href.split("/") if p]
                if len(parts) >= 2:
                    slug = parts[1]
            if name:
                if name not in films or (not films[name].get("year") and year):
                    films[name] = {"slug": slug, "year": year}
        page += 1
    return films


def lookup_year(slug):
    """Fetch the individual film page to find the release year."""
    try:
        url = f"{BASE_URL}/film/{slug}/"
        soup = fetch(url)
        year_link = soup.select_one("a[href*='/films/year/']")
        if year_link:
            return year_link.get_text(strip=True)
        title_tag = soup.select_one("title")
        if title_tag:
            text = title_tag.get_text()
            # Title format is typically "Film Name (Year) ..."
            import re
            m = re.search(r"\((\d{4})\)", text)
            if m:
                return m.group(1)
    except Exception:
        pass
    return ""


def main():
    print(f"Scraping films for {USERNAME}...")

    # Source 1: films list
    films = scrape_films_list(USERNAME)
    print(f"  Films list: {len(films)} films")

    # Source 2: diary
    diary = scrape_diary(USERNAME)
    print(f"  Diary: {len(diary)} entries")

    # Merge diary into films (films list takes priority, diary fills gaps)
    for name, info in diary.items():
        if name not in films:
            films[name] = info
        elif not films[name].get("year") and info.get("year"):
            films[name]["year"] = info["year"]

    print(f"  Merged: {len(films)} unique films")

    # Look up missing years
    missing = [n for n, i in films.items() if not i.get("year") and i.get("slug")]
    if missing:
        print(f"  Looking up years for {len(missing)} films...")
        for name in missing:
            year = lookup_year(films[name]["slug"])
            if year:
                films[name]["year"] = year

    # Sort and write CSV
    rows = sorted(
        [{"Name": name, "Year": info.get("year", "")} for name, info in films.items()],
        key=lambda r: r["Name"].lower(),
    )

    with open(OUTPUT_FILE, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["Name", "Year"])
        writer.writeheader()
        writer.writerows(rows)

    print(f"Wrote {len(rows)} films to {OUTPUT_FILE}")


if __name__ == "__main__":
    main()
