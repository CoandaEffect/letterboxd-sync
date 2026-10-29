from datetime import datetime, timezone
import json
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import Mock, patch
import urllib.error
import urllib.request

from green_app.adapters import APIError, GitHub, JSONAPI, Local, MQTTProbe, NoRedirect, replace_leaves
from green_app.core import Config
from green_app.runtime import Coordinator, Worker


class AdapterTests(unittest.TestCase):
    def test_notification_preserves_nested_payload_and_literal_braces(self):
        config = Config()
        local = Local(config, token="local-only")
        local.api = Mock()
        self.assertTrue(local.notify("title", 'message with {literal} and "quotes"'))
        path, method, payload = local.api.request.call_args.args
        self.assertEqual(path, "/core/api/services/script/andrew_phone_notification")
        self.assertEqual(payload["notification"]["data"]["tag"], "letterboxd-sync")
        self.assertIn("{literal}", payload["notification"]["message"])

    def test_api_401_and_409_are_visible_without_response_body_or_token(self):
        for code in (401, 409):
            api = JSONAPI("https://api.github.com", "secret", 1)
            api.opener = Mock()
            api.opener.open.side_effect = urllib.error.HTTPError("url", code, "secret", {}, None)
            with self.assertRaises(APIError) as result:
                api.request("/repos/owner/repo/contents/file", "PUT", {})
            self.assertEqual(str(result.exception), f"api_http_{code}")
            self.assertNotIn("secret", str(result.exception))

    def test_auth_opener_rejects_redirect_and_external_path(self):
        self.assertIsNone(NoRedirect().redirect_request(None, None, 302, "", {}, "https://evil.invalid"))
        with self.assertRaises(ValueError):
            JSONAPI("http://supervisor", "secret", 1).request("//evil.invalid/")

    def test_status_branch_creation_and_write_are_separate_from_export(self):
        github = GitHub(Config())
        github.api = Mock()
        github.api.request.side_effect = [APIError(404), {"object": {"sha": "main-sha"}},
                                         {}, APIError(404), {}]
        github.heartbeat({"schema_version": 1})
        calls = github.api.request.call_args_list
        self.assertEqual(calls[2].args[2], {"ref": "refs/heads/green-status", "sha": "main-sha"})
        self.assertTrue(calls[-1].args[0].endswith("/contents/health.json"))
        self.assertEqual(calls[-1].args[2]["branch"], "green-status")
        self.assertNotIn("sha", calls[-1].args[2])

    def test_mqtt_only_accepts_current_nonretained_exact_correlation(self):
        probe = MQTTProbe.__new__(MQTTProbe)
        probe.correlation = "current"
        probe.received = Mock()
        for payload, retained in [(b"old", False), (b"current", True)]:
            probe._message(None, None, Mock(topic=MQTTProbe.RESPONSE, payload=payload, retain=retained))
        probe.received.set.assert_not_called()
        probe._message(None, None, Mock(topic=MQTTProbe.RESPONSE, payload=b"current", retain=False))
        probe.received.set.assert_called_once()

    def test_missing_local_probes_are_none_and_cpu_warmup_is_not_zero(self):
        local = Local(Config(), token="secret")
        local.api = Mock()
        local.api.request.side_effect = OSError("unavailable")
        with patch("green_app.adapters.Path.read_text", side_effect=[
                "cpu 10 0 0 90 0 0 0 0 5 0", "MemAvailable: 1024000 kB"]), \
                patch("green_app.adapters.proc_memory", return_value=None):
            sample = local.sample(datetime.now(timezone.utc))
        self.assertIsNone(sample["cpu_percent"])
        self.assertIsNone(sample["ha_ms"])
        self.assertIsNone(sample["mqtt_ms"])
        self.assertEqual(sample["available_mib"], 1000)


class WorkerProcessTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.worker = Worker(self.directory.name)
        self.addCleanup(self.worker.stop)
        self.fixture = Path(__file__).parent / "fixtures" / "green_app" / "process_fixture.py"

    def launch(self, mode):
        self.worker.process = subprocess.Popen([sys.executable, str(self.fixture), mode,
            str(self.worker.directory)], start_new_session=True,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.worker.started = time.monotonic()
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            if (self.worker.directory / (mode + ".ready")).exists():
                return
            if mode == "memory" and self.worker.process.poll() is not None:
                return
            time.sleep(.01)
        self.fail("fixture did not start")

    def test_timeout_kills_entire_process_group_with_term_then_kill(self):
        self.launch("parent")
        deadline = time.monotonic() + 2
        while not (self.worker.directory / "child.ready").exists() and time.monotonic() < deadline:
            time.sleep(.01)
        self.assertTrue((self.worker.directory / "child.ready").exists())
        self.worker.started -= 601
        import os
        with patch("green_app.runtime.os.killpg", wraps=os.killpg) as kill:
            result = self.worker.completed()
        self.assertEqual(result["error"], "worker_wall_timeout")
        self.assertEqual([call.args[1] for call in kill.call_args_list], [signal.SIGTERM, signal.SIGKILL])

    def test_running_worker_rejects_overlap(self):
        self.launch("child")
        with self.assertRaisesRegex(RuntimeError, "overlap"):
            self.worker.start(b"unused", "unused")

    def test_dead_leader_deadline_still_kills_surviving_descendant(self):
        self.launch("orphan")
        process = self.worker.process
        self.worker.process.wait(timeout=2)
        import os
        with patch("green_app.runtime.os.killpg", wraps=os.killpg) as kill:
            self.worker.deadline(process)
        self.assertEqual([call.args[1] for call in kill.call_args_list], [signal.SIGTERM, signal.SIGKILL])
        self.assertIsNone(self.worker.process)
        self.assertEqual(self.worker.completed()["error"], "worker_wall_timeout")

    def test_shutdown_cleans_group_when_leader_already_exited(self):
        self.launch("orphan")
        self.worker.process.wait(timeout=2)
        coordinator = Coordinator(Config(), Path(self.directory.name), Mock(), Mock(), self.worker)
        coordinator.shutdown()
        self.assertIsNone(self.worker.process)

    def test_recovery_does_not_kill_reused_pid_or_unknown_orphan_group(self):
        context = {"boot_id": "boot", "pid_namespace": "namespace"}
        identity = {"pid": 999, "start_ticks": "100", "group": 999, "session": 999}
        with patch.object(self.worker, "context", return_value=context), \
                patch.object(self.worker, "process_identity", return_value={**identity, "start_ticks": "200"}), \
                patch("green_app.runtime.os.killpg") as kill:
            self.assertTrue(self.worker.recover(identity, context))
            kill.assert_not_called()
        with patch.object(self.worker, "context", return_value=context), \
                patch.object(self.worker, "process_identity", return_value=None), \
                patch("green_app.runtime.os.killpg") as kill:
            self.assertFalse(self.worker.recover(identity, context))
            kill.assert_called_once_with(999, 0)

    def test_recovery_after_container_restart_does_not_signal_host_pid(self):
        with patch.object(self.worker, "context", return_value={"pid_namespace": "new"}), \
                patch("green_app.runtime.os.killpg") as kill:
            self.assertTrue(self.worker.recover({"pid": 999}, {"pid_namespace": "old"}))
            kill.assert_not_called()

    @unittest.skipUnless(sys.platform.startswith("linux"), "Linux address-space enforcement")
    def test_actual_address_space_failure_is_reported_without_candidate(self):
        self.launch("memory")
        result = self.worker.completed()
        self.assertFalse(result["ok"])
        self.assertEqual(result["error"], "MemoryError")
        self.assertFalse((self.worker.directory / "candidate.csv").exists())

    @unittest.skipUnless(sys.platform.startswith("linux") and Path("/opt/packages").is_dir(),
                         "Packaged Linux image dependency layout")
    def test_packaged_worker_imports_native_dependencies_with_scrubbed_environment(self):
        result = subprocess.run([sys.executable, "-m", "green_app.worker", "--self-check"],
            env=Worker.environment("coanda_effect"), capture_output=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr.decode())
        status = json.loads(result.stdout)
        self.assertTrue(status["native_imports"])
        self.assertLess(status["memory"]["VmPeak_mib"], 256)


if __name__ == "__main__":
    unittest.main()
