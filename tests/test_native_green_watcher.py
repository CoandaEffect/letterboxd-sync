"""Render the native watcher templates against bounded public-health fixtures."""

from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import unittest
from types import SimpleNamespace

try:
    from jinja2 import Environment, StrictUndefined
except ImportError:
    if os.environ.get("EXACT_SMOKE_CONTAINER_TEST") != "1":
        raise
    Environment = StrictUndefined = None


ROOT = Path(__file__).resolve().parents[1] / "config" / "green-watcher"
NOW = datetime(2026, 1, 1, 12, tzinfo=timezone.utc)


class FakeStates:
    def __init__(self, values, changed):
        self.values = values
        self.automation = SimpleNamespace(letterboxd_sync_health_echo=SimpleNamespace(last_changed=changed))

    def __call__(self, entity):
        return self.values.get(entity)


def epoch(value, default=None):
    if isinstance(value, (float, int)):
        return value
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp()
    except ValueError:
        return default


@unittest.skipIf(os.environ.get("EXACT_SMOKE_CONTAINER_TEST") == "1",
                 "HA templates are verified by host CI, not shipped in the App image")
class NativeWatcherTests(unittest.TestCase):
    def setUp(self):
        self.env = Environment(undefined=StrictUndefined)
        self.env.filters["from_json"] = lambda value, default=None: self.decode(value, default)
        self.env.filters["to_json"] = json.dumps
        self.env.globals.update(as_timestamp=epoch, now=lambda: NOW)

    @staticmethod
    def decode(value, default):
        try:
            return json.loads(value)
        except (ValueError, TypeError):
            return default

    def render(self, name, **context):
        return self.env.from_string((ROOT / name).read_text()).render(**context).strip()

    def fixture(self, **changes):
        data = dict(schema_version=1, heartbeat_at=NOW.isoformat(), phase="idle",
                    monitoring_active=True, baseline_complete=True,
                    last_attempt_at=(NOW-timedelta(minutes=15)).isoformat(),
                    last_success_at=None, next_due_at=NOW.isoformat(), incident=None)
        data.update(changes)
        return data

    def status(self, data=None, raw=None, echo=None):
        packed = raw if raw is not None else self.render("scrape-value.jinja", value=json.dumps(data or self.fixture()))
        return self.render("status-state.jinja", states=lambda entity: packed,
                           state_attr=lambda entity, name: NOW if echo is None else echo)

    def test_review_candidate_has_no_automatic_action(self):
        self.assertEqual(self.status(), "review_required")

    def test_compact_state_fits_maximum_incident(self):
        data = self.fixture(phase="syncing", last_success_at=NOW.isoformat(), incident={"code": "a"*64})
        packed = self.render("scrape-value.jinja", value=json.dumps(data))
        self.assertLessEqual(len(packed), 255)
        self.assertEqual(json.loads(packed)["i"], "a"*64)

    def test_baseline_syncing_and_success(self):
        self.assertEqual(self.status(self.fixture(phase="baseline", baseline_complete=False)), "baseline")
        self.assertEqual(self.status(self.fixture(phase="syncing")), "syncing")
        self.assertEqual(self.status(self.fixture(last_success_at=NOW.isoformat())), "success_seen")

    def test_failure_and_incident_preempt_review(self):
        for phase in ("paused", "failed"):
            self.assertEqual(self.status(self.fixture(phase=phase)), phase)
        self.assertEqual(self.status(self.fixture(incident={"code": "probe_failed"})), "incident")

    def test_stale_exact_boundary_and_future_clock(self):
        at = NOW - timedelta(minutes=45)
        self.assertEqual(self.status(self.fixture(heartbeat_at=at.isoformat())), "review_required")
        self.assertEqual(self.status(self.fixture(heartbeat_at=(at-timedelta(seconds=1)).isoformat())), "heartbeat_stale")
        self.assertEqual(self.status(self.fixture(heartbeat_at=(NOW+timedelta(minutes=6)).isoformat())), "invalid_health")

    def test_local_echo_detects_app_silence_while_public_record_fresh(self):
        self.assertEqual(self.status(echo=NOW-timedelta(minutes=6)), "local_silence")

    def test_unavailable_malformed_and_wrong_schema_fail_closed(self):
        for raw in ("unavailable", "unknown", "{", "[]", '{}'):
            self.assertEqual(self.status(raw=raw), "invalid_health")
        self.assertEqual(self.status(self.fixture(schema_version=2)), "invalid_health")
        self.assertEqual(self.status(self.fixture(heartbeat_at=None)), "invalid_health")

    def test_inactive_has_no_success_or_review(self):
        self.assertEqual(self.status(self.fixture(monitoring_active=False)), "inactive")

    def test_malformed_field_types_do_not_look_like_review(self):
        for changes in ({"schema_version": True}, {"monitoring_active": 1}, {"baseline_complete": "yes"},
                        {"last_success_at": "broken"}, {"last_attempt_at": "broken"},
                        {"incident": "broken"}):
            self.assertEqual(self.status(self.fixture(**changes)), "invalid_health")

    def test_success_without_attempt_is_invalid(self):
        self.assertEqual(self.status(self.fixture(last_attempt_at=None,
                                                 last_success_at=NOW.isoformat())), "invalid_health")

    def notice(self, status, last="", latch="off", echo_age=900, changed_age=900):
        values = {"sensor.letterboxd_green_watcher_status": status,
                  "input_text.letterboxd_green_watcher_last_notice": last,
                  "input_boolean.letterboxd_green_first_success_notified": latch}
        return self.render("notice-state.jinja", states=FakeStates(values, NOW-timedelta(seconds=changed_age)),
                           state_attr=lambda *args: None if echo_age is None else NOW-timedelta(seconds=echo_age))

    def test_notice_issues_and_review_deduplicate_restored_last_notice(self):
        for status in ("invalid_health", "heartbeat_stale", "local_silence", "paused",
                       "failed", "incident", "inactive", "review_required"):
            self.assertEqual(self.notice(status), status)
            self.assertEqual(self.notice(status, last=status), "quiet")

    def test_notice_ordinary_health_and_unavailable_start_quiet(self):
        for status in ("baseline", "idle", "syncing", "startup", "unknown", "unavailable"):
            self.assertEqual(self.notice(status), "quiet")
        self.assertEqual(self.notice("baseline", last="unknown"), "quiet")
        self.assertEqual(self.notice("review_required", last="unknown"), "review_required")

    def test_notice_first_success_once_and_before_recovery(self):
        self.assertEqual(self.notice("success_seen"), "first_success")
        self.assertEqual(self.notice("success_seen", last="failed"), "first_success")
        self.assertEqual(self.notice("success_seen", last="first_success", latch="on"), "quiet")
        self.assertEqual(self.notice("success_seen", last="failed", latch="on"), "recovery")
        self.assertEqual(self.notice("success_seen", last="failed", latch="unknown"), "recovery")

    def test_notice_recovery_only_after_issue_not_after_candidate_review(self):
        for status in ("baseline", "idle", "syncing", "success_seen"):
            self.assertEqual(self.notice(status, last="local_silence", latch="on"), "recovery")
            self.assertEqual(self.notice(status, last="recovery", latch="on"), "quiet")
            self.assertEqual(self.notice(status, last="review_required", latch="on"), "quiet")

    def test_notification_automation_never_controls_app(self):
        config = json.loads((ROOT / "automation.json").read_text())
        self.assertEqual(config["mode"], "single")
        self.assertEqual(config["actions"][-2]["action"], "input_text.set_value")
        self.assertEqual(config["actions"][-1]["if"], [{
            "condition": "state",
            "entity_id": "input_text.letterboxd_green_watcher_last_notice",
            "state": "first_success"}])
        actions = json.dumps(config["actions"])
        for forbidden in ("restart", "resume", "approve", "publish", "addon", "supervisor"):
            self.assertNotIn(forbidden, actions)

    def policy(self, title="Letterboxd Green", tag="letterboxd_green_watcher", notice="",
               message="", registry=None, digest="", packed=None, echo_age=900, offset=0, changed_age=900):
        self.env.globals["now"] = lambda: NOW + timedelta(seconds=offset)
        values = {"input_text.letterboxd_green_urgent_notices": json.dumps(registry or {}),
                  "input_text.letterboxd_green_last_digest": digest,
                  "sensor.letterboxd_green_health": json.dumps(packed or {}),
                  "sensor.letterboxd_green_watcher_status": "baseline"}
        return json.loads(self.render("policy-decision.jinja", states=FakeStates(values, NOW-timedelta(seconds=changed_age)),
                           state_attr=lambda *args: None if echo_age is None else NOW-timedelta(seconds=echo_age),
                           notification={"title": title, "message": message,
                                         "data": {"tag": tag, "letterboxd_notice": notice}}))

    def test_policy_minor_milestones_and_recovery_are_phone_quiet(self):
        for notice in ("review_required", "first_success", "recovery", "invalid_health", "inactive", "failed", "incident"):
            self.assertEqual(self.policy(notice=notice)["kind"], "quiet")
        for title, message in (("Letterboxd Green recovery", "host memory recovered"),
                               ("Letterboxd Green alert", "sync failed"),
                               ("Letterboxd Green notification test", "test")):
            self.assertEqual(self.policy(title=title, tag="letterboxd-sync", message=message)["kind"], "quiet")

    def test_policy_daily_once_per_local_date(self):
        title = "Letterboxd Green daily summary"
        self.assertEqual(self.policy(title=title, tag="letterboxd-sync")["kind"], "daily")
        self.assertEqual(self.policy(title=title, tag="letterboxd-sync", digest="2026-01-01")["kind"], "quiet")
        self.assertEqual(self.policy(title=title, tag="letterboxd-sync", digest="2025-12-31")["day"], "2026-01-01")

    def test_policy_major_dedup_and_cross_source_category(self):
        first = self.policy(title="Letterboxd Green alert", tag="letterboxd-sync", message="host memory")
        self.assertEqual(first["kind"], "urgent")
        registry = json.loads(first["registry"])
        self.assertEqual(self.policy(notice="paused", packed={"i": "host_memory"}, registry=registry)["kind"], "quiet")
        self.assertEqual(self.policy(notice="paused", packed={"i": "host_memory"}, registry=registry, offset=86400)["kind"], "urgent")

    def test_policy_silence_waits_ten_minutes_and_unknown_pause_is_major(self):
        self.assertEqual(self.policy(notice="local_silence", echo_age=599)["kind"], "quiet")
        self.assertEqual(self.policy(notice="local_silence", echo_age=600)["kind"], "urgent")
        self.assertEqual(self.policy(notice="paused")["kind"], "urgent")
        self.assertEqual(self.policy(notice="heartbeat_stale")["kind"], "urgent")
        self.assertEqual(self.notice("local_silence", echo_age=599), "quiet")
        self.assertEqual(self.notice("local_silence", echo_age=600), "local_silence")
        self.assertEqual(self.notice("local_silence", echo_age=None, changed_age=599), "quiet")
        self.assertEqual(self.notice("local_silence", echo_age=None, changed_age=600), "local_silence")
        self.assertEqual(self.policy(notice="local_silence", echo_age=None, changed_age=599)["kind"], "quiet")
        self.assertEqual(self.policy(notice="local_silence", echo_age=None, changed_age=600)["kind"], "urgent")

    def test_policy_exact_tags_only_and_wrapper_passthrough(self):
        for tag in ("other", "letterboxd-sync-other", "", None):
            self.assertEqual(self.policy(tag=tag, notice="paused")["kind"], "quiet")
        route = json.loads((ROOT / "wrapper-routing.json").read_text())
        predicate = route["choose"][0]["conditions"][0]["value_template"]
        for tag, expected in (("letterboxd-sync", "True"), ("letterboxd_green_watcher", "True"),
                              ("letterboxd_green_watcher_test", "True"), ("other", "False")):
            self.assertEqual(self.env.from_string(predicate).render(notification={"data": {"tag": tag}}), expected)
        self.assertEqual(route["default"], "KEEP_ORIGINAL_WRAPPER_SEQUENCE")

    def test_policy_script_embeds_same_decision_and_delivers_before_marking(self):
        script = json.loads((ROOT / "notification-policy.json").read_text())
        self.assertEqual(script["mode"], "queued")
        self.assertEqual(script["sequence"][0]["variables"]["policy_json"],
                         (ROOT / "policy-decision.jinja").read_text().strip())
        self.assertEqual(script["sequence"][3]["action"], "script.letterboxd_green_phone_delivery")
        self.assertIn("choose", script["sequence"][4])

    def test_policy_registry_stays_under_helper_state_limit(self):
        registry = {key: int(NOW.timestamp()) for key in ("mem", "cpu", "ha", "mq", "mon", "wrk", "bs", "sy", "hb", "ps")}
        decision = self.policy(notice="paused", registry=registry)
        self.assertLessEqual(len(decision["registry"]), 255)
        self.assertEqual(decision["kind"], "quiet")


if __name__ == "__main__":
    unittest.main()
