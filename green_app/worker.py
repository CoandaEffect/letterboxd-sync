"""Resource limits precede every native scraper import."""
import json
import os
from pathlib import Path
import resource
import signal
import sys
import time


def guard_resources():
    resource.setrlimit(resource.RLIMIT_AS, (256 * 1024 ** 2,) * 2)
    resource.setrlimit(resource.RLIMIT_CPU, (120, 125))
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    os.nice(19)
    signal.alarm(600)


def self_check():
    guard_resources()
    import scrape
    with scrape.ImpersonateSession() as session:
        session.curl.version()
    from .adapters import proc_memory
    print(json.dumps({"ok": True, "offline": True, "native_imports": True,
                      "memory": proc_memory(os.getpid()), "cpu_seconds": time.process_time()}))
    return 0


def main(directory, username):
    guard_resources()
    directory = Path(directory)
    started = time.monotonic()
    try:
        import scrape
        scrape.USERNAME = username
        scrape.OUTPUT_FILE = str(directory / "reference.csv")
        previous = scrape.read_previous_rows()
        rows = scrape.scrape_with_retries(previous)
        scrape.validate_rows(rows, previous)
        rows.sort(key=lambda row: row["Name"].lower())
        scrape.OUTPUT_FILE = str(directory / "candidate.csv")
        scrape.write_rows(rows)
        from .adapters import proc_memory
        result = {"ok": True, "duration_seconds": time.monotonic() - started,
                  "memory": proc_memory(os.getpid()), "cpu_seconds": time.process_time()}
    except BaseException as error:
        result = {"ok": False, "error": type(error).__name__,
                  "duration_seconds": time.monotonic() - started}
    from .core import atomic_bytes
    atomic_bytes(directory / "result.json", json.dumps(result).encode())
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    sys.exit(self_check() if sys.argv[1:] == ["--self-check"] else main(sys.argv[1], sys.argv[2]))
