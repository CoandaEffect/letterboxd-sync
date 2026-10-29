"""CI-only child: enforce production limits before native imports; use loopback."""
import json
from pathlib import Path
import sys
from urllib.parse import urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tools import green_exact_smoke as smoke


def main():
    base = sys.argv[1]
    parsed = urlsplit(base)
    if parsed.scheme != "http" or parsed.hostname != "127.0.0.1" or not parsed.port:
        raise ValueError("loopback_fixture_required")
    smoke.guard_resources()
    factory, versions = smoke.load_client()
    session = factory(retry=0, trust_env=False)
    try:
        pages = {path: smoke.fetch_page(session, kind, base + path)
                 for path, kind in (("/films", "films"), ("/diary", "diary"),
                                    ("/oversized", "films"), ("/redirect", "films"),
                                    ("/forbidden", "films"), ("/challenge", "films"))}
        assert pages["/films"]["passed"] and pages["/diary"]["passed"]
        assert pages["/oversized"]["reason"] == "response_size_limit"
        assert pages["/oversized"]["response_bytes"] <= smoke.RESPONSE_LIMIT
        assert pages["/redirect"]["reason"] == "redirect_rejected"
        assert pages["/forbidden"]["reason"] == "http_error"
        assert pages["/challenge"]["reason"] == "challenge_response"
        print(json.dumps({"pages": pages, "client": versions, **smoke.proc_memory()}))
    finally:
        session.close()


if __name__ == "__main__":
    main()
