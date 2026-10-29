#!/usr/bin/env python3
"""One-shot curl_cffi feasibility gate; never run the full scrape or write data."""
import json
import os
import platform
import resource
import signal
import sys
import time
import urllib.error
import urllib.request

from tools.green_smoke import NoRedirect, PageData, available_mib


RESPONSE_LIMIT = 2 * 1024 ** 2
WALL_SECONDS = 12
WRITE_ERROR = 0xFFFFFFFF
PROFILES = ("chrome", "safari", "firefox", "safari_ios")
PAGES = {
    "films": "https://letterboxd.com/coanda_effect/films/page/2/",
    "diary": "https://letterboxd.com/coanda_effect/diary/page/2/",
}


class Deadline(BaseException):
    pass


def deadline(signum, frame):
    raise Deadline()


def guard_resources():
    resource.setrlimit(resource.RLIMIT_AS, (256 * 1024 ** 2,) * 2)
    resource.setrlimit(resource.RLIMIT_CPU, (4, 5))
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    os.nice(19)
    for number in (signal.SIGALRM, signal.SIGXCPU, signal.SIGTERM):
        signal.signal(number, deadline)
    signal.alarm(WALL_SECONDS)


def configured_profile():
    try:
        with open("/data/options.json") as handle:
            options = json.loads(handle.read(4096))
    except FileNotFoundError:
        return "chrome"
    profile = options.get("profile", "chrome")
    if profile not in PROFILES:
        raise ValueError("unsupported_profile")
    return profile


def load_client(profile="chrome"):
    # Import the production class only after RLIMIT_AS covers its native modules.
    import curl_cffi
    from importlib.metadata import version
    from scrape import IMPERSONATE_PROFILES, ImpersonateSession

    if curl_cffi.__version__ != "0.17.0" or profile not in IMPERSONATE_PROFILES:
        raise ValueError("unsupported_client")
    ImpersonateSession.profile = profile
    return ImpersonateSession, {
        "curl_cffi": curl_cffi.__version__, "libcurl": curl_cffi.__curl_version__,
        "letterboxdpy": version("letterboxdpy"), "profile": ImpersonateSession.profile,
    }


def probe_core():
    started = time.monotonic()
    token = os.environ.get("SUPERVISOR_TOKEN")
    if not token:
        return {"status": "unavailable"}
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    request = urllib.request.Request("http://supervisor/core/api/", headers={
        "Authorization": "Bearer " + token,
    })
    try:
        with opener.open(request, timeout=0.6) as response:
            healthy = response.status == 200 and b"API running" in response.read(1024)
        result = {"status": "ok" if healthy else "unexpected_response"}
    except urllib.error.HTTPError as error:
        result = {"status": "unavailable", "http": error.code}
    except (OSError, urllib.error.URLError):
        result = {"status": "unresponsive"}
    result["milliseconds"] = round((time.monotonic() - started) * 1000)
    return result


def fetch_page(session, kind, url):
    started = time.monotonic()
    body = bytearray()
    overflow = False
    result = {"page": kind, "passed": False, "entries": 0, "http": None}

    def receive(chunk):
        nonlocal overflow
        if len(body) + len(chunk) > RESPONSE_LIMIT:
            overflow = True
            # curl_cffi treats a short byte count as success; use libcurl's abort.
            return WRITE_ERROR
        body.extend(chunk)
        return len(chunk)

    try:
        response = session.get(url, timeout=3, allow_redirects=False, max_redirects=0,
                               content_callback=receive)
        result["http"] = response.status_code
        if overflow:
            result["reason"] = "response_size_limit"
        elif response.status_code != 200:
            result["reason"] = "redirect_rejected" if 300 <= response.status_code < 400 else "http_error"
        elif any(marker in body.lower() for marker in
                 (b"<title>just a moment", b"cf-chl-", b"/cdn-cgi/challenge-platform/")):
            result["reason"] = "challenge_response"
        else:
            parser = PageData()
            parser.feed(body.decode("utf-8", errors="replace"))
            result["entries"] = parser.films if kind == "films" else parser.diary
            result["passed"] = result["entries"] > 0
            if not result["passed"]:
                result["reason"] = "no_expected_page_data"
    except Exception:
        result["reason"] = "response_size_limit" if overflow else "client_error_or_timeout"
    result["milliseconds"] = round((time.monotonic() - started) * 1000)
    result["response_bytes"] = len(body)
    return result


def proc_memory():
    result = {}
    try:
        with open("/proc/self/status") as handle:
            for line in handle:
                key, _, value = line.partition(":")
                if key in ("VmSize", "VmPeak"):
                    result[key + "_mib"] = round(int(value.split()[0]) / 1024, 1)
    except (OSError, ValueError):
        pass
    return result


def os_release():
    try:
        with open("/etc/os-release") as handle:
            for line in handle:
                if line.startswith("PRETTY_NAME="):
                    return line.partition("=")[2].strip().strip('"')
    except OSError:
        pass
    return platform.system()


def offline_self_test(session, profile):
    fixtures = {
        PAGES["films"]: b'<ul class="grid"><li data-item-slug="fixture"></li></ul>',
        PAGES["diary"]: b'<table><tr class="diary-entry-row"></tr></table>',
    }
    original_request = session.request
    calls = []

    def fixture_request(method, url, **kwargs):
        if method != "GET" or kwargs.get("impersonate") != profile:
            raise ValueError("production_get_not_used")
        body = fixtures[url]
        if kwargs["content_callback"](body) != len(body):
            raise ValueError("fixture_callback_failed")
        calls.append(url)
        return type("FixtureResponse", (), {"status_code": 200})()

    try:
        # Initialize the native handle under the same limits, without any network.
        session.curl.version()
        session.request = fixture_request
        pages = [fetch_page(session, kind, url) for kind, url in PAGES.items()]
        if calls != list(PAGES.values()) or not all(page["passed"] for page in pages):
            raise ValueError("offline_page_validation_failed")
        return pages
    finally:
        session.request = original_request


def main(offline=False, profile=None):
    started = time.monotonic()
    report = {
        "test": "letterboxd-green-exact-client-smoke", "mode": "offline" if offline else "live",
        "platform": platform.system(), "machine": platform.machine(),
        "os": os_release(), "python": platform.python_version(),
        "pages": [], "ha_api_samples": [], "network_result": "inconclusive",
        "ha_api_result": "not_verified", "deployment_gate": "blocked", "exit_code": 1,
        "limits": {"address_space_mib": 256, "cpu_seconds": [4, 5],
                   "wall_seconds": WALL_SECONDS, "nice": 19, "request_seconds": 3,
                   "response_bytes": RESPONSE_LIMIT, "host_memory_floor_mib": 512},
    }
    session = None
    try:
        if report["platform"] != "Linux" or report["machine"] != "aarch64":
            report["stopped"] = "requires_linux_aarch64"
            return report
        guard_resources()
        report["libc"] = list(platform.libc_ver())
        report["host_available_memory_mib_before"] = available_mib()
        available = report["host_available_memory_mib_before"]
        if available is None or available < 512:
            report["stopped"] = "host_memory_not_verified" if available is None else "low_host_memory"
            return report
        selected_profile = configured_profile() if profile is None else profile
        if selected_profile not in PROFILES:
            raise ValueError("unsupported_profile")
        factory, report["client"] = load_client(selected_profile)
        session = factory(retry=0, trust_env=False)
        if offline:
            report["pages"] = offline_self_test(session, selected_profile)
            report.update(offline_result="passed", exit_code=0)
            return report
        for kind, url in PAGES.items():
            sample = probe_core()
            report["ha_api_samples"].append(sample)
            if sample["status"] != "ok":
                report["stopped"] = "ha_api_guard"
                return report
            report["pages"].append(fetch_page(session, kind, url))
            time.sleep(0.25)
        report["ha_api_samples"].append(probe_core())
        if len(report["pages"]) == 2 and all(page["passed"] for page in report["pages"]):
            report["network_result"] = "passed"
        if len(report["ha_api_samples"]) == 3 and all(sample["status"] == "ok" for sample in report["ha_api_samples"]):
            report["ha_api_result"] = "samples_ok"
        else:
            report["stopped"] = "ha_api_guard"
        if report["network_result"] == "passed" and report["ha_api_result"] == "samples_ok":
            report.update(deployment_gate="passed", exit_code=0)
    except Deadline:
        report["stopped"] = "time_or_cpu_limit"
    except Exception as error:
        report["stopped"] = type(error).__name__
    finally:
        if session is not None:
            session.close()
        signal.alarm(0)
        usage = resource.getrusage(resource.RUSAGE_SELF)
        report["elapsed_seconds"] = round(time.monotonic() - started, 3)
        report["process_cpu_seconds"] = round(usage.ru_utime + usage.ru_stime, 3)
        report["process_peak_memory_mib"] = round(usage.ru_maxrss / (1024 ** 2 if sys.platform == "darwin" else 1024), 1)
        report["host_available_memory_mib_after"] = available_mib()
        report.update(proc_memory())
        print(json.dumps(report, sort_keys=True), flush=True)
    return report


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--offline-self-test", action="store_true")
    parser.add_argument("--profile", choices=PROFILES)
    args = parser.parse_args()
    result = main(offline=args.offline_self_test, profile=args.profile)
    sys.exit(result["exit_code"])
