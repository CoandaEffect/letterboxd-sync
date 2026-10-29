#!/usr/bin/env python3
"""One-shot, read-only network feasibility check in HA File editor's Python.

No dependencies, file writes, installs, retries, background work, or HA actions.
This is NOT the full curl_cffi scraper or a sustained home-control latency test.
"""
import json
import os
import platform
import resource
import signal
import sys
import time
import urllib.error
import urllib.request
from html.parser import HTMLParser


class Deadline(BaseException):
    pass


def deadline(signum, frame):
    raise Deadline()


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class PageData(HTMLParser):
    def __init__(self):
        super().__init__()
        self.films = 0
        self.diary = 0
        self.ul_depth = 0
        self.film_grid_depth = None

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        classes = attrs.get("class", "").split()
        if tag == "ul":
            self.ul_depth += 1
            if {"grid", "poster-list"}.intersection(classes):
                self.film_grid_depth = self.ul_depth
        if self.film_grid_depth is not None and attrs.get("data-item-slug"):
            self.films += 1
        if tag == "tr" and "diary-entry-row" in classes:
            self.diary += 1

    def handle_endtag(self, tag):
        if tag == "ul":
            if self.ul_depth == self.film_grid_depth:
                self.film_grid_depth = None
            self.ul_depth -= 1


def available_mib():
    try:
        with open("/proc/meminfo") as handle:
            for line in handle:
                if line.startswith("MemAvailable:"):
                    return round(int(line.split()[1]) / 1024)
    except OSError:
        pass
    return None


def probe_core():
    token = os.environ.get("SUPERVISOR_TOKEN") or os.environ.get("HC_HASS_API_PASSWORD")
    if not token:
        return {"status": "unavailable"}
    # Dedicated local opener: never reuse this credential for external requests.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    request = urllib.request.Request("http://supervisor/core/api/",
                                     headers={"Authorization": "Bearer " + token})
    start = time.monotonic()
    try:
        with opener.open(request, timeout=0.6) as response:
            healthy = response.status == 200 and b"API running" in response.read(1024)
        return {"status": "ok" if healthy else "unexpected_response",
                "milliseconds": round((time.monotonic() - start) * 1000)}
    except urllib.error.HTTPError as error:
        return {"status": "unavailable", "http": error.code}
    except (OSError, urllib.error.URLError):
        return {"status": "unresponsive"}


def fetch_page(kind, path):
    start = time.monotonic()
    result = {"page": kind, "path": path, "passed": False}
    request = urllib.request.Request("https://letterboxd.com" + path, headers={
        "User-Agent": "Mozilla/5.0 (X11; Linux aarch64) AppleWebKit/537.36 "
                      "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
        "Accept": "text/html", "Accept-Encoding": "identity",
    })
    try:
        with urllib.request.build_opener(NoRedirect()).open(request, timeout=3) as response:
            result["http"] = response.status
            body = response.read(2 * 1024 * 1024 + 1)
        if len(body) > 2 * 1024 * 1024:
            result["reason"] = "response_size_limit"
        else:
            parser = PageData()
            parser.feed(body.decode("utf-8", errors="replace"))
            count = parser.films if kind == "films" else parser.diary
            result["entries"] = count
            result["passed"] = result["http"] == 200 and count > 0
            if not result["passed"]:
                result["reason"] = "no_expected_page_data"
    except urllib.error.HTTPError as error:
        result.update(http=error.code, reason="http_error")
    except (OSError, urllib.error.URLError):
        result["reason"] = "connection_error_or_timeout"
    result["milliseconds"] = round((time.monotonic() - start) * 1000)
    return result


def main():
    started = time.monotonic()
    report = {"test": "letterboxd-green-network-smoke", "architecture": platform.machine(),
              "pages": [], "ha_api_samples": [], "network_result": "inconclusive"}
    signal.signal(signal.SIGALRM, deadline)
    signal.signal(signal.SIGXCPU, deadline)
    signal.alarm(11)
    try:
        os.nice(19)
        resource.setrlimit(resource.RLIMIT_CPU, (4, 5))
        resource.setrlimit(resource.RLIMIT_AS, (128 * 1024 * 1024, 128 * 1024 * 1024))
        report["host_available_memory_mib_before"] = available_mib()
        if report["host_available_memory_mib_before"] is not None and report["host_available_memory_mib_before"] < 512:
            report["stopped"] = "low_host_memory"
            return report
        for kind, path in (("films", "/coanda_effect/films/page/2/"),
                           ("diary", "/coanda_effect/diary/page/2/")):
            sample = probe_core()
            report["ha_api_samples"].append(sample)
            if sample["status"] in ("unresponsive", "unexpected_response"):
                report["stopped"] = "ha_api_guard"
                return report
            report["pages"].append(fetch_page(kind, path))
            time.sleep(0.25)
        report["ha_api_samples"].append(probe_core())
        if all(page["passed"] for page in report["pages"]):
            report["network_result"] = "passed"
        report["ha_api_result"] = "samples_ok" if all(s["status"] == "ok" for s in report["ha_api_samples"]) else "not_verified"
    except Deadline:
        report["stopped"] = "time_or_cpu_limit"
    except (OSError, ValueError, MemoryError) as error:
        report["stopped"] = type(error).__name__
    finally:
        signal.alarm(0)
        usage = resource.getrusage(resource.RUSAGE_SELF)
        report["elapsed_seconds"] = round(time.monotonic() - started, 2)
        report["process_cpu_seconds"] = round(usage.ru_utime + usage.ru_stime, 3)
        report["process_peak_memory_mib"] = round(usage.ru_maxrss / 1024, 1)
        report["host_available_memory_mib_after"] = available_mib()
        print(json.dumps(report, indent=2), flush=True)
    return report


if __name__ == "__main__":
    result = main()
    sys.exit(0 if result["network_result"] == "passed" else 1)
