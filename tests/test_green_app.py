import csv
from datetime import datetime, timedelta, timezone
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from green_app.core import (Baseline, Config, Guard, StateStore, cpu_percent,
                            next_schedule, public_health, validate_csv)
from green_app.runtime import Coordinator, Worker

UTC = timezone.utc
NOW = datetime(2026, 10, 29, 12, tzinfo=UTC)
CSV = b'Name,Year,Rating,Liked,Watch Date,Runtime,Rewatched,Letterboxd URI\nFilm,2026,,,2026-10-01,90,,https://letterboxd.com/film/film/\n'


def sample(at=NOW, **changes):
    return {"at": at.isoformat(), "cpu_percent": 10, "available_mib": 1700,
            "ha_ms": 20, "mqtt_ms": 10, **changes}


class CoreTests(unittest.TestCase):
    def test_cpu_warmup_and_guest_not_double_counted(self):
        self.assertIsNone(cpu_percent(None, [100] * 10))
        self.assertEqual(cpu_percent([0] * 10, [10, 0, 0, 90, 0, 0, 0, 0, 1000, 1000]), 10)
        self.assertIsNone(cpu_percent([1] * 8, [0] * 8))

    def test_schedule_odd_calendar_dates_and_month_boundary(self):
        self.assertEqual(next_schedule(datetime(2026, 10, 31, 6, tzinfo=UTC)),
                         datetime(2026, 11, 1, 6, tzinfo=UTC))
        self.assertEqual(next_schedule(datetime(2026, 11, 1, 5, tzinfo=UTC)).day, 1)

    def test_valid_baseline_needs_full_coverage_and_no_missing_probes(self):
        baseline = Baseline(NOW)
        for minute in range(240):
            baseline.add(sample(NOW + timedelta(minutes=minute)))
        self.assertTrue(baseline.complete(NOW + timedelta(hours=4)))
        self.assertEqual(baseline.p95("ha_ms"), 20)
        baseline.samples.pop(100)
        self.assertFalse(baseline.complete(NOW + timedelta(hours=4)))
        self.assertEqual(baseline.coverage()["missing_minutes"], [100])
        baseline.add(sample(NOW + timedelta(minutes=100), mqtt_ms=None))
        self.assertFalse(baseline.complete(NOW + timedelta(hours=4)))

    def test_guard_latches_thresholds_and_resets_consecutive_counts(self):
        for key, value, count, expected in [("cpu_percent", 81, 5, "host_cpu"),
                ("ha_ms", 501, 3, "ha_latency"), ("mqtt_ms", 251, 3, "mqtt_latency"),
                ("available_mib", 511, 1, "host_memory"),
                ("ha_ms", None, 3, "monitoring_missing")]:
            guard = Guard({"ha_ms": 20, "mqtt_ms": 10})
            for _ in range(count - 1):
                self.assertIsNone(guard.check(sample(**{key: value})))
            self.assertEqual(guard.check(sample(**{key: value})), expected)
        guard = Guard({})
        guard.check(sample(cpu_percent=90))
        guard.check(sample())
        self.assertEqual(guard.counts["host_cpu"], 0)

    def test_validation_preserves_export_against_partial_and_lost_dates(self):
        self.assertEqual(validate_csv(CSV, CSV)["films"], 1)
        for candidate in [b"", CSV.replace(b"2026-10-01", b""),
                          CSV.replace(b"/film/film/", b"/film/other/")]:
            with self.assertRaises(ValueError):
                validate_csv(candidate, CSV)

    def test_baseline_tolerates_normal_sample_jitter_but_rejects_real_gap(self):
        baseline = Baseline(NOW)
        for minute in range(240):
            jitter = .001 if minute % 2 else -.001
            at = NOW + timedelta(minutes=minute, seconds=jitter if minute else 0)
            baseline.add(sample(at))
        self.assertTrue(baseline.complete(NOW + timedelta(hours=4)))
        baseline.samples[100]["at"] = (NOW + timedelta(minutes=100, seconds=20)).isoformat()
        self.assertFalse(baseline.complete(NOW + timedelta(hours=4)))

    def test_persistent_state_retains_clock_pause_and_incident_delivery(self):
        with tempfile.TemporaryDirectory() as directory:
            store = StateStore(Path(directory))
            state = store.load()
            state.update(trial_started_at=NOW.isoformat(), pause="host_memory",
                         incidents={"host_memory": {"notified": True}})
            store.save(state)
            self.assertEqual(store.load(), state)

    def test_corrupt_existing_state_fails_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            (Path(directory) / "green-state.json").write_text("{")
            with self.assertRaises(ValueError):
                StateStore(Path(directory)).load()

    def test_public_status_is_allowlisted(self):
        state = {"github_token": "secret", "sample": sample(), "pause": "host_memory"}
        health = public_health(state, NOW, True)
        self.assertEqual(health["incident"], {"code": "host_memory"})
        self.assertNotIn("secret", json.dumps(health))
        self.assertEqual(health["schema_version"], 1)


class RuntimeTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name)
        self.config = Config(github_repository="owner/repo", github_token="secret")
        self.github = Mock()
        self.github.fetch_export.return_value = (CSV, "same")
        self.local = Mock()
        self.worker = Mock()
        self.worker.running = False
        self.worker.context.return_value = None
        self.worker.identity.return_value = None
        self.coordinator = Coordinator(self.config, self.path, self.github, self.local, self.worker)

    def test_inactive_never_starts_clock_or_worker(self):
        self.coordinator.tick(NOW, sample())
        self.assertIsNone(self.coordinator.state["trial_started_at"])
        self.worker.start.assert_not_called()

    def test_default_unconfigured_inactive_startup_only_records_local_sample(self):
        self.config.github_token = ""
        missing = sample(ha_ms=None, mqtt_ms=None)
        for minute in range(3):
            self.coordinator.tick(NOW + timedelta(minutes=minute), missing)
        self.assertEqual(self.github.mock_calls, [])
        self.local.notify.assert_not_called()
        self.worker.start.assert_not_called()
        self.assertIsNone(self.coordinator.state["trial_started_at"])
        self.assertFalse(self.coordinator.state["scheduler_enabled"])
        self.assertEqual(self.coordinator.state["incidents"], {})
        self.assertEqual(self.coordinator.store.load()["last_sample"], missing)

    def test_active_without_github_token_keeps_failure_visibility(self):
        self.config.github_token = ""
        self.config.active = True
        self.github.fetch_export.side_effect = OSError("unauthorized")
        self.github.heartbeat.side_effect = OSError("unauthorized")
        self.coordinator.tick(NOW, sample())
        self.github.heartbeat.assert_called_once()
        self.local.notify.assert_called()
        self.assertIn("heartbeat_publish_failed", self.coordinator.state["incidents"])
        self.assertIsNone(self.coordinator.state["trial_started_at"])

    def test_existing_trial_without_token_keeps_failure_visibility_when_inactive(self):
        self.config.github_token = ""
        self.github.heartbeat.side_effect = OSError("unauthorized")
        self.coordinator.state.update(trial_started_at=NOW.isoformat(),
            trial_ends_at=(NOW + timedelta(days=5)).isoformat(),
            baseline_ends_at=(NOW + timedelta(hours=4)).isoformat(),
            next_due_at=(NOW + timedelta(hours=4)).isoformat())
        self.coordinator.tick(NOW, sample())
        self.github.heartbeat.assert_called_once()
        self.local.notify.assert_called()
        self.assertIn("heartbeat_publish_failed", self.coordinator.state["incidents"])
        self.assertEqual(self.coordinator.state["trial_started_at"], NOW.isoformat())

    def test_configured_inactive_startup_may_publish_health(self):
        self.coordinator.tick(NOW, sample())
        self.github.heartbeat.assert_called_once()
        self.local.notify.assert_not_called()

    def test_private_status_log_allowlists_nested_fields_without_secret_canary(self):
        canary = "SECRET_CANARY_DO_NOT_LOG"
        self.config.github_token = ""
        self.coordinator.state.update(options={"token": canary}, environment={"SUPERVISOR_TOKEN": canary},
            baseline_samples=[{"secret": canary}], outcomes=[{"private": canary}],
            candidate_summary={"films": 365, "sha256": "a" * 64, "private": canary},
            worker_result={"ok": True, "duration_seconds": 12, "private": canary,
                           "memory": {"VmPeak_mib": 59.9, "private": canary}},
            baseline_coverage={"expected_minutes": 240, "valid_minutes": 1,
                               "missing_minutes": list(range(1, 240)), "private": canary},
            baseline_p95={"ha_ms": 400, "mqtt_ms": 20, "private": canary})
        measured = sample(private=canary, app_memory={"VmRSS_mib": 20, "private": canary})
        self.coordinator.tick(NOW, measured)
        text = (self.path / "events.jsonl").read_text()
        self.assertNotIn(canary, text)
        record = [json.loads(line) for line in text.splitlines() if json.loads(line)["code"] == "status"][0]
        status = record["status"]
        self.assertEqual(status["phase"], "startup")
        self.assertEqual(status["candidate_summary"]["films"], 365)
        self.assertEqual(status["worker_result"]["memory"]["VmPeak_mib"], 59.9)
        self.assertEqual(status["last_sample"]["app_memory"], {"VmRSS_mib": 20})
        self.assertNotIn("outcomes", status)
        self.github.heartbeat.assert_not_called()
        self.local.notify.assert_not_called()

    def test_private_status_log_cadence_survives_coordinator_restart(self):
        self.config.github_token = ""
        self.coordinator.tick(NOW, sample())
        replacement = Coordinator(self.config, self.path, self.github, self.local, self.worker)
        replacement.tick(NOW + timedelta(minutes=14), sample())
        replacement.tick(NOW + timedelta(minutes=15), sample())
        records = [json.loads(line) for line in (self.path / "events.jsonl").read_text().splitlines()]
        self.assertEqual([record["at"] for record in records if record["code"] == "status"],
                         [NOW.isoformat(), (NOW + timedelta(minutes=15)).isoformat()])

    def test_first_candidate_logs_immediate_status_even_between_periodic_logs(self):
        self.config.github_token = ""
        self.coordinator.tick(NOW, sample())
        self.coordinator.state["reference_sha"] = "same"
        self.coordinator.state["worker_result"] = {"ok": True, "memory": {"VmPeak_mib": 60}}
        (self.path / "worker").mkdir()
        (self.path / "worker" / "candidate.csv").write_bytes(CSV)
        self.coordinator.finish_candidate(NOW + timedelta(minutes=1))
        records = [json.loads(line) for line in (self.path / "events.jsonl").read_text().splitlines()]
        statuses = [record["status"] for record in records if record["code"] == "status"]
        self.assertEqual(len(statuses), 2)
        self.assertEqual(statuses[-1]["candidate_summary"]["films"], 1)
        self.assertEqual(statuses[-1]["worker_result"]["memory"]["VmPeak_mib"], 60)

    def test_clock_requires_warmed_metrics_and_notification_acceptance(self):
        self.config.active = True
        self.local.notify.return_value = False
        self.coordinator.tick(NOW, sample())
        self.assertIsNone(self.coordinator.state["trial_started_at"])
        self.local.notify.return_value = True
        self.coordinator.tick(NOW + timedelta(minutes=1), sample(cpu_percent=None))
        self.assertIsNone(self.coordinator.state["trial_started_at"])
        self.coordinator.tick(NOW + timedelta(minutes=2), sample(NOW + timedelta(minutes=2)))
        self.assertIsNone(self.coordinator.state["trial_started_at"])
        self.coordinator.tick(NOW + timedelta(minutes=3), sample(NOW + timedelta(minutes=3)))
        started = self.coordinator.state["trial_started_at"]
        self.assertIsNotNone(started)
        self.coordinator.tick(NOW + timedelta(minutes=4), sample())
        self.assertEqual(self.coordinator.state["trial_started_at"], started)
        self.worker.start.assert_not_called()

    def test_pause_and_delivery_are_deduplicated_and_persisted(self):
        self.local.notify.return_value = True
        self.coordinator.pause("host_memory", NOW)
        self.coordinator.pause("host_memory", NOW)
        self.local.notify.assert_called_once()
        self.worker.stop.assert_called_once()
        reloaded = StateStore(self.path).load()
        self.assertEqual(reloaded["pause"], "host_memory")
        self.assertTrue(reloaded["incidents"]["host_memory"]["notified"])

    def test_explicit_resume_requires_changed_nonce_and_healthy_sample(self):
        self.coordinator.pause("host_memory", NOW)
        self.config.resume_nonce = "reviewed-1"
        self.coordinator.tick(NOW, sample(available_mib=100))
        self.assertTrue(self.coordinator.state["pause"])
        self.coordinator.tick(NOW, sample())
        self.assertIsNone(self.coordinator.state["pause"])

    def test_publication_conflict_preserves_current_export(self):
        self.github.fetch_export.return_value = (CSV, "sha-after")
        self.coordinator.state["reference_sha"] = "sha-before"
        (self.path / "worker").mkdir()
        (self.path / "worker" / "candidate.csv").write_bytes(CSV)
        self.coordinator.finish_candidate(NOW)
        self.github.write_export.assert_not_called()
        self.assertEqual(self.coordinator.state["last_outcome"], "failed")

    def test_memory_failure_latches_pause_before_any_automatic_retry(self):
        self.coordinator.state.update(trial_started_at=NOW.isoformat(),
            trial_ends_at=(NOW + timedelta(days=5)).isoformat(),
            baseline_ends_at=(NOW + timedelta(hours=4)).isoformat(),
            next_due_at=(NOW + timedelta(hours=4)).isoformat(), worker_inflight=True)
        self.worker.completed.return_value = {"ok": False, "error": "MemoryError"}
        self.coordinator.tick(NOW, sample())
        self.assertEqual(self.coordinator.state["pause"], "worker_memory_failure")
        self.coordinator.tick(NOW + timedelta(hours=6), sample(NOW + timedelta(hours=6)))
        self.worker.start.assert_not_called()

    def test_unchanged_valid_success_refreshes_last_success(self):
        self.github.fetch_export.return_value = (CSV, "same")
        self.config.approve_first_publish = True
        self.coordinator.state["reference_sha"] = "same"
        (self.path / "worker").mkdir()
        (self.path / "worker" / "candidate.csv").write_bytes(CSV)
        self.coordinator.finish_candidate(NOW)
        self.assertEqual(self.coordinator.state["last_success_at"], NOW.isoformat())
        self.github.write_export.assert_not_called()
        self.assertEqual(self.coordinator.state["last_outcome"], "unchanged")

    def test_first_candidate_needs_explicit_approval(self):
        self.github.fetch_export.return_value = (CSV, "same")
        self.coordinator.state["reference_sha"] = "same"
        (self.path / "worker").mkdir()
        (self.path / "worker" / "candidate.csv").write_bytes(CSV)
        self.coordinator.finish_candidate(NOW)
        self.assertTrue(self.coordinator.state["candidate_pending"])
        self.assertIsNone(self.coordinator.state["last_success_at"])

    def test_worker_environment_contains_no_coordinator_secrets(self):
        with patch.dict("os.environ", {"SUPERVISOR_TOKEN": "ha", "GITHUB_TOKEN": "gh"}):
            environment = Worker.environment("coanda_effect")
        self.assertNotIn("SUPERVISOR_TOKEN", environment)
        self.assertNotIn("GITHUB_TOKEN", environment)
        self.assertEqual(environment["PYTHONPATH"], "/opt/packages")

    def test_alert_recurrence_is_new_incident_and_failed_delivery_retries_bounded(self):
        self.local.notify.return_value = True
        self.coordinator.incident("sync_failed", NOW)
        self.coordinator.recover("sync_failed", NOW)
        self.coordinator.incident("sync_failed", NOW + timedelta(minutes=10))
        self.assertEqual(self.local.notify.call_count, 3)
        self.local.notify.return_value = False
        self.coordinator.incident("other_failed", NOW)
        calls = self.local.notify.call_count
        self.coordinator.tick(NOW + timedelta(minutes=1), sample())
        self.assertEqual(self.local.notify.call_count, calls)
        self.coordinator.tick(NOW + timedelta(hours=1), sample())
        self.assertGreater(self.local.notify.call_count, calls)

    def test_started_baseline_reboot_does_not_restart_clock(self):
        self.config.active = True
        self.local.notify.return_value = True
        self.coordinator.tick(NOW, sample())
        self.coordinator.tick(NOW + timedelta(minutes=1), sample(NOW + timedelta(minutes=1)))
        original = self.coordinator.state["trial_started_at"]
        replacement = Coordinator(self.config, self.path, self.github, self.local, self.worker)
        replacement.tick(NOW + timedelta(minutes=20), sample(NOW + timedelta(minutes=20)))
        self.assertEqual(replacement.state["trial_started_at"], original)
        self.assertFalse(replacement.state["baseline_complete"])
        self.worker.start.assert_not_called()

    def test_interrupted_worker_restart_clears_inflight_and_requires_explicit_resume(self):
        self.coordinator.state.update(phase="syncing", worker_inflight=True,
            trial_started_at=NOW.isoformat(), trial_ends_at=(NOW + timedelta(days=5)).isoformat(),
            baseline_ends_at=(NOW + timedelta(hours=4)).isoformat(), next_due_at=NOW.isoformat(),
            baseline_complete=True)
        self.coordinator.store.save(self.coordinator.state)
        self.worker.recover.return_value = True
        replacement = Coordinator(self.config, self.path, self.github, self.local, self.worker)
        self.assertFalse(replacement.state["worker_inflight"])
        self.assertEqual(replacement.state["pause"], "worker_interrupted")
        replacement.tick(NOW, sample())
        self.worker.start.assert_not_called()
        self.config.active = True
        self.config.resume_nonce = "reviewed-after-restart"
        replacement.tick(NOW + timedelta(minutes=1), sample())
        self.assertIsNone(replacement.state["pause"])
        self.worker.start.assert_called_once()

    def test_unverified_orphan_cleanup_blocks_even_explicit_resume(self):
        self.coordinator.state.update(phase="syncing", worker_inflight=True)
        self.coordinator.store.save(self.coordinator.state)
        self.worker.recover.return_value = False
        replacement = Coordinator(self.config, self.path, self.github, self.local, self.worker)
        self.config.resume_nonce = "changed"
        replacement.tick(NOW, sample())
        self.assertEqual(replacement.state["pause"], "worker_cleanup_unverified")
        self.worker.start.assert_not_called()

    def test_recovery_delivery_is_persisted_and_retries_with_backoff(self):
        self.local.notify.return_value = True
        self.coordinator.incident("sync_failed", NOW)
        self.local.notify.return_value = False
        self.coordinator.recover("sync_failed", NOW)
        calls = self.local.notify.call_count
        self.coordinator.recover("sync_failed", NOW + timedelta(minutes=1))
        self.assertEqual(self.local.notify.call_count, calls)
        self.local.notify.return_value = True
        self.coordinator.tick(NOW + timedelta(hours=1), sample())
        self.assertTrue(self.coordinator.state["incidents"]["sync_failed"]["recovery_notified"])
        self.assertFalse(self.coordinator.state["notification_failure"])

    def test_daily_summary_failure_preserves_payload_for_bounded_retry(self):
        self.config.summary_hour = 0
        self.local.notify.side_effect = [False, True, True, True]
        self.coordinator.daily_summary(NOW, sample())
        first_payload = self.local.notify.call_args_list[0].args
        self.assertIn("pending_summary", self.coordinator.state)
        self.coordinator.daily_summary(NOW + timedelta(minutes=1), sample())
        self.assertEqual(self.local.notify.call_count, 2)
        self.coordinator.daily_summary(NOW + timedelta(hours=1), sample())
        self.assertEqual(self.local.notify.call_args_list[2].args, first_payload)
        self.assertNotIn("pending_summary", self.coordinator.state)

    def test_worker_failure_never_publishes_candidate(self):
        self.coordinator.state.update(trial_started_at=NOW.isoformat(),
            trial_ends_at=(NOW + timedelta(days=5)).isoformat(),
            baseline_ends_at=(NOW + timedelta(hours=4)).isoformat(),
            next_due_at=(NOW + timedelta(hours=4)).isoformat(), worker_inflight=True)
        self.worker.completed.return_value = {"ok": False, "error": "MemoryError"}
        self.coordinator.tick(NOW, sample())
        self.github.write_export.assert_not_called()
        self.assertEqual(self.coordinator.state["last_outcome"], "failed")

    def test_overlap_prevention_does_not_start_second_worker(self):
        self.worker.running = True
        self.coordinator.start_sync(NOW)
        self.worker.start.assert_not_called()

    def test_github_write_failure_does_not_mark_success(self):
        self.config.approve_first_publish = True
        self.github.write_export.side_effect = RuntimeError("api_http_401")
        self.coordinator.state["reference_sha"] = "same"
        (self.path / "worker").mkdir()
        candidate = CSV.replace(b"Film,2026", b"Film renamed,2026")
        (self.path / "worker" / "candidate.csv").write_bytes(candidate)
        self.coordinator.finish_candidate(NOW)
        self.assertIsNone(self.coordinator.state["last_success_at"])
        self.assertEqual(self.coordinator.state["last_outcome"], "failed")

    def test_baseline_then_dry_run_approval_and_calendar_schedule_end_to_end(self):
        self.config.active = True
        self.local.notify.return_value = True
        self.worker.completed.return_value = None
        with patch("builtins.print"):
            for minute in range(242):
                at = NOW + timedelta(minutes=minute)
                self.coordinator.tick(at, sample(at))
            self.assertTrue(self.coordinator.state["baseline_complete"])
            self.assertEqual(self.coordinator.state["baseline_samples"], [])
            self.assertEqual(self.coordinator.state["baseline_coverage"]["valid_minutes"], 240)
            self.worker.start.assert_called_once_with(CSV, self.config.username)
            self.assertIsNone(self.coordinator.state["last_success_at"])
            (self.path / "worker").mkdir()
            (self.path / "worker" / "candidate.csv").write_bytes(CSV)
            self.worker.completed.return_value = {"ok": True, "duration_seconds": 20}
            at = NOW + timedelta(minutes=242)
            self.coordinator.tick(at, sample(at))
            self.assertTrue(self.coordinator.state["candidate_pending"])
            self.assertIsNone(self.coordinator.state["last_success_at"])
            self.config.approve_first_publish = True
            at += timedelta(minutes=1)
            self.coordinator.tick(at, sample(at))
            self.assertEqual(self.coordinator.state["last_success_at"], at.isoformat())
            self.assertEqual(self.coordinator.state["next_due_at"], next_schedule(at).isoformat())
            self.worker.start.assert_called_once()

    def test_active_preflight_missing_monitoring_becomes_visible_pause(self):
        self.config.active = True
        for minute in range(3):
            at = NOW + timedelta(minutes=minute)
            self.coordinator.tick(at, sample(at, mqtt_ms=None))
        self.assertIsNone(self.coordinator.state["trial_started_at"])
        self.assertEqual(self.coordinator.state["pause"], "monitoring_missing")

    def test_baseline_gap_blocks_initial_sync_and_reports_missing_coverage(self):
        self.config.active = True
        self.local.notify.return_value = True
        with patch("builtins.print"):
            for minute in list(range(100)) + list(range(101, 245)):
                at = NOW + timedelta(minutes=minute)
                self.coordinator.tick(at, sample(at))
        self.assertFalse(self.coordinator.state["baseline_complete"])
        self.assertIn("baseline_coverage_missing", self.coordinator.state["incidents"])
        self.worker.start.assert_not_called()


if __name__ == "__main__":
    unittest.main()
