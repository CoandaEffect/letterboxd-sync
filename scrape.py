#!/usr/bin/env python3
"""TEMPORARY diagnostic: probe Letterboxd reachability from this runner."""

from curl_cffi import requests

URLS = {
    "rss": "https://letterboxd.com/coanda_effect/rss/",
    "profile": "https://letterboxd.com/coanda_effect/",
    "films": "https://letterboxd.com/coanda_effect/films/",
}


def main():
    for profile in ["chrome", "safari", "firefox", "safari_ios"]:
        for name, url in URLS.items():
            try:
                r = requests.get(url, impersonate=profile, timeout=20)
                server = r.headers.get("server", "?")
                mitigated = r.headers.get("cf-mitigated", "-")
                print(
                    f"{profile:12s} {name:8s} -> {r.status_code} "
                    f"server={server} cf-mitigated={mitigated} len={len(r.content)}"
                )
            except Exception as e:
                print(f"{profile:12s} {name:8s} -> EXC {type(e).__name__}: {e}")


if __name__ == "__main__":
    main()
