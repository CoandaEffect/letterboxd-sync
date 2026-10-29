import contextlib
import io
import importlib.util
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
import os
from pathlib import Path
import subprocess
import struct
import sys
import threading
import unittest
import urllib.error
from unittest.mock import MagicMock, Mock, mock_open, patch

from tools import green_exact_smoke as smoke


FILMS = b'<ul class="grid"><li data-item-slug="example-film"></li></ul>'
DIARY = b'<table><tr class="diary-entry-row"></tr></table>'
JSD = b'<script src="/cdn-cgi/challenge-platform/scripts/jsd/api.js" data-cf-chl-example="1"></script>'


class Session:
    def __init__(self, statuses=(200, 200), bodies=(FILMS, DIARY), headers=({}, {})):
        self.statuses = iter(statuses)
        self.bodies = iter(bodies)
        self.headers = iter(headers)
        self.calls = []
        self.closed = False

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        body = next(self.bodies)
        if kwargs["content_callback"](body) != len(body):
            raise RuntimeError("untrusted request detail")
        return Mock(status_code=next(self.statuses), headers=next(self.headers))

    def close(self):
        self.closed = True


class ExactSmokeTests(unittest.TestCase):
    def run_smoke(self, session=None, probes=None, available=1024, profile=None, load_error=None,
                  offline=False):
        session = session or Session()
        probes = probes or [{"status": "ok", "milliseconds": 1}] * 3
        with patch.object(smoke, "guard_resources") as guard, \
             patch.object(smoke.platform, "system", return_value="Linux"), \
             patch.object(smoke.platform, "machine", return_value="aarch64"), \
             patch.object(smoke, "available_mib", return_value=available), \
             patch.object(smoke, "load_client", return_value=(Mock(return_value=session), {}),
                          side_effect=load_error) as load, \
             patch.object(smoke, "probe_core", side_effect=probes), \
             patch.object(smoke.time, "sleep"), \
             patch.object(smoke.signal, "alarm"), \
             patch.object(smoke.signal, "signal"), \
             contextlib.redirect_stdout(io.StringIO()) as output:
            report = smoke.main(profile=profile, offline=offline)
        self.assertEqual(json.loads(output.getvalue()), report)
        return report, session, guard, load

    def test_two_valid_pages_and_three_healthy_probes_pass(self):
        report, session, _, load = self.run_smoke()
        self.assertEqual(report["deployment_gate"], "passed")
        self.assertEqual([page["entries"] for page in report["pages"]], [1, 1])
        self.assertEqual([call[0] for call in session.calls], list(smoke.PAGES.values()))
        load.return_value[0].assert_called_once_with(retry=0, trust_env=False)
        self.assertTrue(session.closed)

    def test_403_and_challenge_200_never_pass(self):
        for session in (Session(statuses=(403, 200)),
                        Session(bodies=(b"<title>Just a moment...</title>" + FILMS, DIARY)),
                        Session(statuses=(302, 200))):
            report, _, _, _ = self.run_smoke(session)
            self.assertEqual(report["deployment_gate"], "blocked")
            self.assertFalse(report["pages"][0]["passed"])

    def test_benign_cloudflare_javascript_still_requires_and_counts_expected_content(self):
        report, _, _, _ = self.run_smoke(Session(bodies=(FILMS + JSD, DIARY + JSD)))
        self.assertEqual(report["deployment_gate"], "passed")
        self.assertEqual([page["entries"] for page in report["pages"]], [1, 1])
        report, _, _, _ = self.run_smoke(Session(bodies=(JSD, JSD)))
        self.assertEqual(report["deployment_gate"], "blocked")
        self.assertEqual([page["reason"] for page in report["pages"]],
                         ["no_expected_page_data", "no_expected_page_data"])

    def test_challenge_header_blocks_even_with_expected_nodes_and_handles_header_case(self):
        for name, value in (("cf-mitigated", "challenge"), ("Cf-Mitigated", " ChAlLeNgE ")):
            with self.subTest(name=name):
                report, _, _, _ = self.run_smoke(Session(headers=({name: value}, {name: value})))
                self.assertEqual(report["deployment_gate"], "blocked")
                self.assertEqual([page["reason"] for page in report["pages"]],
                                 ["challenge_response", "challenge_response"])

    def test_request_is_bounded_has_no_credentials_and_is_not_retried(self):
        session = Session(bodies=(b"x" * (smoke.RESPONSE_LIMIT + 1), DIARY))
        report, session, _, load = self.run_smoke(session)
        self.assertEqual(len(session.calls), 2)
        self.assertEqual(report["pages"][0]["reason"], "response_size_limit")
        self.assertNotIn("untrusted request detail", json.dumps(report))
        load.assert_called_once_with("chrome")
        for _, kwargs in session.calls:
            self.assertEqual(kwargs["timeout"], 3)
            self.assertIs(kwargs["allow_redirects"], False)
            self.assertEqual(kwargs["max_redirects"], 0)
            self.assertNotIn("headers", kwargs)
            self.assertNotIn("auth", kwargs)

    def test_failed_or_missing_probe_blocks_and_stops_external_requests(self):
        for status in ("unavailable", "unresponsive", "unexpected_response"):
            report, session, _, load = self.run_smoke(probes=[{"status": status}])
            self.assertEqual(report["deployment_gate"], "blocked")
            self.assertEqual(report["stopped"], "ha_api_guard")
            self.assertEqual(session.calls, [])
            load.assert_not_called()
        report, session, _, _ = self.run_smoke(probes=[{"status": "ok"},
                                                     {"status": "ok"},
                                                     {"status": "unresponsive"}])
        self.assertEqual(len(session.calls), 2)
        self.assertEqual(report["deployment_gate"], "blocked")

    def test_external_failure_is_attempted_once_and_second_page_still_checked(self):
        session = Session()
        session.get = Mock(side_effect=RuntimeError("secret-token/internal-host"))
        report, session, _, _ = self.run_smoke(session)
        self.assertEqual(session.get.call_count, 2)
        self.assertEqual(report["deployment_gate"], "blocked")
        self.assertNotIn("secret-token", json.dumps(report))
        self.assertNotIn("internal-host", json.dumps(report))

    def test_low_or_unknown_host_memory_blocks_before_native_import(self):
        for available in (511, None):
            report, session, guard, load = self.run_smoke(available=available)
            guard.assert_called_once_with()
            load.assert_not_called()
            self.assertEqual(session.calls, [])
            self.assertEqual(report["deployment_gate"], "blocked")

    def test_resource_guard_precedes_native_import(self):
        order = []
        with patch.object(smoke, "guard_resources", side_effect=lambda: order.append("guard")), \
             patch.object(smoke.platform, "system", return_value="Linux"), \
             patch.object(smoke.platform, "machine", return_value="aarch64"), \
             patch.object(smoke, "available_mib", return_value=1024), \
             patch.object(smoke, "probe_core", side_effect=lambda: order.append("probe") or {"status": "ok"}), \
             patch.object(smoke, "load_client", side_effect=lambda profile: order.append("import")), \
             patch.object(smoke.signal, "signal"), \
             patch.object(smoke.signal, "alarm"), \
             contextlib.redirect_stdout(io.StringIO()):
            smoke.main()
        self.assertEqual(order, ["guard", "probe", "import"])

    def test_initial_ha_measurement_survives_native_import_deadline(self):
        report, _, _, _ = self.run_smoke(load_error=smoke.Deadline())
        self.assertEqual(report["ha_api_samples"], [{"status": "ok", "milliseconds": 1}])
        self.assertEqual(report["pages"], [])
        self.assertEqual(report["stopped"], "time_or_cpu_limit")
        self.assertEqual(report["deployment_gate"], "blocked")

    def test_offline_run_does_not_probe_ha(self):
        with patch.object(smoke, "offline_self_test", return_value=[]):
            report, _, _, _ = self.run_smoke(offline=True)
        self.assertEqual(report["ha_api_samples"], [])
        self.assertEqual(report["offline_result"], "passed")
        self.assertEqual(report["deployment_gate"], "blocked")

    def test_guard_sets_memory_cpu_nice_and_wall_deadline(self):
        with patch.object(smoke.os, "nice") as nice, \
             patch.object(smoke.resource, "setrlimit") as limit, \
             patch.object(smoke.signal, "signal"), \
             patch.object(smoke.signal, "alarm") as alarm:
            smoke.guard_resources()
        nice.assert_called_once_with(19)
        self.assertEqual(limit.call_args_list[0].args,
                         (smoke.resource.RLIMIT_AS, (256 * 1024 ** 2,) * 2))
        self.assertEqual(limit.call_args_list[1].args, (smoke.resource.RLIMIT_CPU, (4, 5)))
        alarm.assert_called_once_with(smoke.WALL_SECONDS)

    def test_deadline_blocks_gate_without_sensitive_exception(self):
        with patch.object(smoke, "fetch_page", side_effect=smoke.Deadline()):
            report, _, _, _ = self.run_smoke()
        self.assertEqual(report["stopped"], "time_or_cpu_limit")
        self.assertEqual(report["deployment_gate"], "blocked")

    def test_local_probe_uses_own_no_proxy_no_redirect_opener(self):
        response = Mock(status=200)
        response.read.return_value = b'{"message": "API running."}'
        opener = MagicMock()
        opener.open.return_value.__enter__.return_value = response
        with patch.dict(os.environ, {"SUPERVISOR_TOKEN": "private-canary"}), \
             patch.object(smoke.urllib.request, "build_opener", return_value=opener) as build:
            result = smoke.probe_core()
        handlers = build.call_args.args
        self.assertEqual(handlers[0].proxies, {})
        self.assertIsInstance(handlers[1], smoke.NoRedirect)
        self.assertIsNone(handlers[1].redirect_request(None, None, 302, "", {}, "https://example.org"))
        request = opener.open.call_args.args[0]
        self.assertEqual(request.get_header("Authorization"), "Bearer private-canary")
        self.assertEqual(request.get_method(), "GET")
        self.assertEqual(opener.open.call_args.kwargs, {"timeout": 0.6})
        self.assertEqual(result["status"], "ok")
        self.assertNotIn("private-canary", json.dumps(result))
        self.assertNotIn("supervisor", json.dumps(result))

    def test_local_redirect_is_failed_probe_without_exposing_target(self):
        opener = Mock()
        opener.open.side_effect = urllib.error.HTTPError("http://private/", 302, "redirect", {}, None)
        with patch.dict(os.environ, {"SUPERVISOR_TOKEN": "private-canary"}), \
             patch.object(smoke.urllib.request, "build_opener", return_value=opener):
            result = smoke.probe_core()
        self.assertEqual(result["status"], "unavailable")
        self.assertNotIn("private", json.dumps(result))

    def test_missing_token_does_not_attempt_probe(self):
        with patch.dict(os.environ, {}, clear=True), \
             patch.object(smoke.urllib.request, "build_opener") as build:
            result = smoke.probe_core()
            self.assertEqual(result["status"], "unavailable")
            self.assertIsNone(result.get("milliseconds"))
        build.assert_not_called()

    def test_only_one_validated_profile_is_selected_for_a_run(self):
        report, session, _, load = self.run_smoke(profile="safari")
        load.assert_called_once_with("safari")
        self.assertEqual(len(session.calls), 2)
        self.assertEqual(report["deployment_gate"], "passed")
        report, session, _, load = self.run_smoke(profile="unapproved-profile")
        load.assert_not_called()
        self.assertEqual(session.calls, [])
        self.assertEqual(report["deployment_gate"], "blocked")

    def test_app_options_only_allow_production_profiles_and_default_to_chrome(self):
        with patch("builtins.open", side_effect=FileNotFoundError):
            self.assertEqual(smoke.configured_profile(), "chrome")
        for profile in ("chrome", "safari", "firefox", "safari_ios"):
            with patch("builtins.open", mock_open(read_data=json.dumps({"profile": profile}))):
                self.assertEqual(smoke.configured_profile(), profile)
        with patch("builtins.open", mock_open(read_data='{"profile":"unapproved"}')):
            with self.assertRaises(ValueError):
                smoke.configured_profile()

    @unittest.skipUnless(sys.platform == "linux" and os.environ.get("EXACT_SMOKE_CONTAINER_TEST") == "1",
                         "native Linux ARM64 guard is verified in the container build")
    def test_native_offline_startup_enforces_256_mib_before_import(self):
        completed = subprocess.run(
            ["/app/run.sh", "--offline-self-test"],
            cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True, timeout=16,
            env={**os.environ, "HTTP_PROXY": "http://127.0.0.1:1", "SUPERVISOR_TOKEN": "private-canary"},
        )
        self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
        report = json.loads(completed.stdout)
        self.assertEqual(report["machine"], "aarch64")
        self.assertEqual(report["offline_result"], "passed")
        self.assertEqual(report["deployment_gate"], "blocked")
        self.assertEqual(report["network_result"], "inconclusive")
        self.assertEqual(report["client"]["curl_cffi"], "0.17.0")
        self.assertLessEqual(report["VmPeak_mib"], 256)
        self.assertEqual(report["ha_api_samples"], [])
        self.assertNotIn("private-canary", completed.stdout + completed.stderr)

    @unittest.skipUnless(sys.platform == "linux" and os.environ.get("EXACT_SMOKE_CONTAINER_TEST") == "1",
                         "precompiled runtime is verified in the built image")
    def test_image_has_valid_precompiled_stdlib_dependencies_and_app(self):
        stdlib = Path(os.__file__).parent
        sources = [stdlib / relative for relative in
                   ("json/__init__.py", "urllib/request.py", "platform.py", "argparse.py",
                    "html/parser.py", "importlib/metadata/__init__.py")]
        for directory in (Path("/opt/packages"), Path("/app")):
            self.assertTrue(directory.is_dir())
            sources.extend(directory.rglob("*.py"))
        for source in sources:
            with self.subTest(source=str(source)):
                cache = Path(importlib.util.cache_from_source(str(source)))
                self.assertTrue(cache.is_file(), "Missing build-time bytecode: " + str(source))
                with cache.open("rb") as handle:
                    header = handle.read(16)
                self.assertEqual(header[:4], importlib.util.MAGIC_NUMBER)
                flags = struct.unpack("<I", header[4:8])[0]
                if flags & 1:
                    self.assertEqual(header[8:16], importlib.util.source_hash(source.read_bytes()))
                else:
                    modified, size = struct.unpack("<II", header[8:16])
                    self.assertEqual(modified, int(source.stat().st_mtime) & 0xFFFFFFFF)
                    self.assertEqual(size, source.stat().st_size & 0xFFFFFFFF)
        print("Verified valid build-time bytecode for", len(sources), "runtime Python files")

    @unittest.skipUnless(sys.platform == "linux" and os.environ.get("EXACT_SMOKE_CONTAINER_TEST") == "1",
                         "native transport is verified against CI-only loopback fixtures")
    def test_native_transport_aborts_overflow_rejects_redirect_and_never_sends_ha_token(self):
        hits = []
        credentials = []

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                hits.append(self.path)
                credentials.append(self.headers.get("Authorization"))
                status, body = 200, FILMS
                if self.path == "/diary":
                    body = DIARY
                elif self.path == "/films-jsd":
                    body = FILMS + JSD
                elif self.path == "/diary-jsd":
                    body = DIARY + JSD
                elif self.path == "/oversized":
                    body = b"x" * (smoke.RESPONSE_LIMIT + 65536)
                elif self.path == "/redirect":
                    status = 302
                elif self.path == "/forbidden":
                    status = 403
                elif self.path == "/challenge":
                    body = b"<title>Just a moment...</title>" + FILMS
                self.send_response(status)
                self.send_header("Content-Length", str(len(body)))
                if self.path == "/challenge-header":
                    self.send_header("Cf-MiTiGaTeD", " ChAlLeNgE ")
                if status == 302:
                    self.send_header("Location", "/must-not-follow")
                self.end_headers()
                try:
                    self.wfile.write(body)
                except OSError:
                    pass

            def log_message(self, format, *args):
                pass

        with HTTPServer(("127.0.0.1", 0), Handler) as server:
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                completed = subprocess.run(
                    [sys.executable, str(Path(__file__).with_name("native_exact_smoke_check.py")),
                     "http://127.0.0.1:" + str(server.server_port)],
                    capture_output=True, text=True, timeout=16,
                    env={**os.environ, "HTTP_PROXY": "http://127.0.0.1:1", "NO_PROXY": "",
                         "SUPERVISOR_TOKEN": "private-canary"},
                )
            finally:
                server.shutdown()
                thread.join(timeout=2)
        self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
        report = json.loads(completed.stdout)
        self.assertLessEqual(report["VmPeak_mib"], 256)
        self.assertEqual(hits, ["/films", "/diary", "/films-jsd", "/diary-jsd", "/oversized",
                               "/redirect", "/forbidden", "/challenge", "/challenge-header"])
        self.assertEqual(credentials, [None] * 9)
        self.assertNotIn("private-canary", completed.stdout + completed.stderr)


if __name__ == "__main__":
    unittest.main()
