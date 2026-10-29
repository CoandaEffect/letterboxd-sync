import contextlib
from datetime import datetime, timedelta, timezone
import io
import json
from pathlib import Path
import tempfile
import unittest

from tools import check_green_health as watchdog


NOW = datetime(2026, 10, 29, 12, tzinfo=timezone.utc)


def stamp(value):
    return value.isoformat().replace("+00:00", "Z")


def record(**changes):
    data = {
        "schema_version": 1, "heartbeat_at": stamp(NOW), "phase": "idle",
        "monitoring_active": True, "trial_started_at": "2026-10-28T00:00:00Z",
        "trial_ends_at": "2026-11-02T00:00:00Z", "baseline_ends_at": "2026-10-28T04:00:00Z",
        "baseline_complete": True, "scheduler_enabled": True,
        "last_attempt_at": "2026-10-29T06:00:00Z", "last_success_at": "2026-10-29T06:00:00Z",
        "next_due_at": "2026-10-31T06:00:00Z", "incident": None,
    }
    data.update(changes)
    return data


class WatchdogTests(unittest.TestCase):
    def check(self, data, reason="healthy", healthy=True, now=NOW):
        result = watchdog.evaluate(data, now)
        self.assertEqual(result["reason"], reason, result)
        self.assertEqual(result["healthy"], healthy, result)
        return result

    def test_healthy_and_no_change_success(self):
        self.check(record())

    def test_heartbeat_exact_boundary_and_stale(self):
        self.check(record(heartbeat_at=stamp(NOW - timedelta(minutes=45))))
        self.check(record(heartbeat_at=stamp(NOW - timedelta(minutes=45, microseconds=1))),
                   "heartbeat_stale", False)

    def test_future_heartbeat_boundary(self):
        self.check(record(heartbeat_at=stamp(NOW + timedelta(minutes=5))))
        self.check(record(heartbeat_at=stamp(NOW + timedelta(minutes=5, microseconds=1))),
                   "heartbeat_in_future", False)

    def test_due_exact_boundary_then_overdue_even_after_attempt(self):
        data = record(next_due_at=stamp(NOW - timedelta(hours=6)),
                      last_success_at="2026-10-28T04:00:00Z", last_attempt_at=stamp(NOW))
        self.check(data)
        self.check(data, "sync_overdue", False, NOW + timedelta(microseconds=1))

    def test_baseline_and_persisted_restart_deadline(self):
        start = NOW - timedelta(hours=2)
        data = record(phase="baseline", baseline_complete=False, scheduler_enabled=False,
                      trial_started_at=stamp(start), trial_ends_at=stamp(start + timedelta(days=5)),
                      baseline_ends_at=stamp(start + timedelta(hours=4)),
                      last_attempt_at=None, last_success_at=None,
                      next_due_at=stamp(start + timedelta(hours=4)))
        self.check(data, "baseline")
        later = start + timedelta(hours=10)
        data["heartbeat_at"] = stamp(later)
        self.check(data, "baseline", now=later)
        self.check(data, "baseline_stalled", False, later + timedelta(microseconds=1))

    def test_initial_success_cannot_disappear(self):
        self.check(record(last_attempt_at=None, last_success_at=None,
                          next_due_at="2026-10-28T04:00:00Z"), "initial_success_overdue", False)

    def test_future_due_cannot_hide_missing_or_old_success(self):
        self.check(record(next_due_at="2026-12-31T06:00:00Z"), "invalid_record", False)
        self.check(record(last_success_at="2026-10-28T04:00:00Z",
                          next_due_at="2026-10-31T06:00:00Z"), "invalid_record", False)

    def test_calendar_schedule_handles_month_boundary_and_same_day(self):
        self.assertEqual(watchdog.next_scheduled(datetime(2026, 10, 31, 5, tzinfo=timezone.utc)),
                         datetime(2026, 10, 31, 6, tzinfo=timezone.utc))
        self.assertEqual(watchdog.next_scheduled(datetime(2026, 10, 31, 6, tzinfo=timezone.utc)),
                         datetime(2026, 11, 1, 6, tzinfo=timezone.utc))

    def test_paused_failed_and_incident_code_sanitized(self):
        for phase in ("paused", "failed"):
            result = self.check(record(phase=phase, incident={"code": "resource_pressure"}),
                                "app_" + phase, False)
            self.assertEqual(result["incident_code"], "resource_pressure")
        self.check(record(phase="paused", monitoring_active=False,
                          incident={"code": "PRIVATE\n::error::"}), "invalid_record", False)

    def test_only_startup_can_be_inactive(self):
        inactive = record(phase="startup", monitoring_active=False, trial_started_at=None,
                          trial_ends_at=None, baseline_ends_at=None, baseline_complete=False,
                          scheduler_enabled=False, last_attempt_at=None, last_success_at=None,
                          next_due_at=None)
        result = self.check(inactive, "inactive", healthy=None)
        self.assertEqual(result["status"], "inactive")
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "health.json"
            path.write_text(json.dumps(inactive))
            with contextlib.redirect_stdout(io.StringIO()) as output:
                code = watchdog.main([str(path), "--now", stamp(NOW)])
            self.assertEqual(code, 0)
            self.assertIsNone(json.loads(output.getvalue())["healthy"])
        self.check(record(monitoring_active=False), "invalid_record", False)

    def test_baseline_cannot_be_shorter_than_approved_four_hours(self):
        self.check(record(baseline_ends_at="2026-10-28T03:59:59Z"), "invalid_record", False)

    def test_missing_malformed_wrong_types_and_timezone(self):
        for key in record():
            data = record()
            del data[key]
            with self.subTest(missing=key):
                self.check(data, "invalid_record", False)
        for changes in ({"heartbeat_at": None}, {"heartbeat_at": "garbage"},
                        {"heartbeat_at": "2026-10-29T12:00:00"},
                        {"heartbeat_at": "2026-10-29T05:00:00-07:00"},
                        {"schema_version": True}, {"scheduler_enabled": 1},
                        {"incident": {"code": "x", "private": "secret"}},
                        {"phase": "secret"}, {"last_success_at": stamp(NOW + timedelta(hours=1))},
                        {"baseline_ends_at": "2026-12-01T00:00:00Z"},
                        {"next_due_at": None}, {"scheduler_enabled": False}):
            with self.subTest(changes=changes):
                self.check(record(**changes), "invalid_record", False)

    def test_input_errors_are_bounded_and_sanitized(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "health.json"
            for raw in ('{"secret":"do-not-echo"}', '{"a":1,"a":2}', 'NaN',
                        'x' * (65536 + 1), '[1]', '{', '\ud800'):
                path.write_bytes(raw.encode("utf-8", errors="surrogatepass"))
                with contextlib.redirect_stdout(io.StringIO()) as output:
                    code = watchdog.main([str(path), "--now", stamp(NOW)])
                self.assertNotEqual(code, 0)
                self.assertNotIn("do-not-echo", output.getvalue())
                json.loads(output.getvalue())
            with contextlib.redirect_stdout(io.StringIO()) as output:
                code = watchdog.main([str(path) + "missing"])
            self.assertNotEqual(code, 0)
            self.assertEqual(json.loads(output.getvalue())["reason"], "record_unreadable")

    def test_cli_healthy_and_controlled_failure_fixtures(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "health.json"
            path.write_text(json.dumps(record()))
            with contextlib.redirect_stdout(io.StringIO()) as output:
                code = watchdog.main([str(path), "--now", stamp(NOW)])
            self.assertEqual(code, 0)
            self.assertTrue(json.loads(output.getvalue())["healthy"])
            before = path.read_bytes()
            for fixture, reason in (("missing-heartbeat", "invalid_record"),
                                    ("overdue-success", "sync_overdue")):
                with contextlib.redirect_stdout(io.StringIO()) as output:
                    code = watchdog.main(["--test-case", fixture, "--now", stamp(NOW)])
                self.assertEqual(code, 1)
                self.assertEqual(json.loads(output.getvalue())["reason"], reason)
            self.assertEqual(path.read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
