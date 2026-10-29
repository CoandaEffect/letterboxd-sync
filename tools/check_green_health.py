"""Offline, fail-closed evaluation of the public Green App health record."""

import argparse
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import re


MAX_BYTES = 65536
TIME_FIELDS = ("heartbeat_at", "trial_started_at", "trial_ends_at", "baseline_ends_at",
               "last_attempt_at", "last_success_at", "next_due_at")
FIELDS = set(TIME_FIELDS) | {"schema_version", "phase", "monitoring_active",
                            "baseline_complete", "scheduler_enabled", "incident"}
PHASES = {"startup", "baseline", "idle", "syncing", "paused", "failed"}
MESSAGES = {
    "healthy": "Green heartbeat and scheduled sync are within limits.",
    "baseline": "Green is within its bounded initial baseline grace period.",
    "inactive": "Green trial has not been activated; monitoring is inactive.",
    "invalid_record": "Green health record is missing required or valid measurements.",
    "record_unreadable": "Green health record could not be read.",
    "heartbeat_stale": "Green heartbeat is more than 45 minutes old.",
    "heartbeat_in_future": "Green heartbeat is more than 5 minutes in the future.",
    "sync_overdue": "Green scheduled successful sync is more than 6 hours overdue.",
    "initial_success_overdue": "Green initial successful sync is more than 6 hours overdue.",
    "baseline_stalled": "Green baseline has exceeded its end plus 6 hours.",
    "app_paused": "Green is paused and requires attention.",
    "app_failed": "Green reports a failure and requires attention.",
}


def timestamp(value):
    if not isinstance(value, str) or not re.fullmatch(
            r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:Z|\+00:00)", value):
        raise ValueError("invalid UTC timestamp")
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def result(reason, incident=None):
    healthy = None if reason == "inactive" else reason in {"healthy", "baseline"}
    report = {"healthy": healthy,
              "status": "inactive" if healthy is None else "healthy" if healthy else "unhealthy",
              "reason": reason, "message": MESSAGES[reason]}
    if incident is not None:
        report["incident_code"] = incident
    return report


def next_scheduled(after):
    candidate = after.replace(hour=6, minute=0, second=0, microsecond=0)
    while candidate <= after or candidate.day % 2 == 0:
        candidate += timedelta(days=1)
    return candidate


def evaluate(data, now):
    try:
        if not isinstance(data, dict) or set(data) != FIELDS:
            raise ValueError()
        if type(data["schema_version"]) is not int or data["schema_version"] != 1:
            raise ValueError()
        if data["phase"] not in PHASES:
            raise ValueError()
        for name in ("monitoring_active", "baseline_complete", "scheduler_enabled"):
            if type(data[name]) is not bool:
                raise ValueError()
        times = {key: timestamp(data[key]) if data[key] is not None else None
                 for key in TIME_FIELDS}
        if times["heartbeat_at"] is None or now.tzinfo is None:
            raise ValueError()
        incident = data["incident"]
        if incident is not None and (not isinstance(incident, dict) or set(incident) != {"code"}
                                     or not isinstance(incident["code"], str)
                                     or not re.fullmatch(r"[a-z][a-z0-9_]{0,63}", incident["code"])):
            raise ValueError()
        if data["phase"] in {"paused", "failed"}:
            return result("app_" + data["phase"], incident["code"] if incident else None)
        if not data["monitoring_active"]:
            if (data["phase"] != "startup" or data["baseline_complete"] or data["scheduler_enabled"]
                    or any(times[key] is not None for key in TIME_FIELDS if key != "heartbeat_at")
                    or incident is not None):
                raise ValueError()
        else:
            start, end, baseline = (times[key] for key in
                                    ("trial_started_at", "trial_ends_at", "baseline_ends_at"))
            due = times["next_due_at"]
            if (None in (start, end, baseline, due) or not start < baseline <= end
                    or baseline - start != timedelta(hours=4) or end - start != timedelta(days=5)
                    or start > now + timedelta(minutes=5) or due < baseline):
                raise ValueError()
            for key in ("last_attempt_at", "last_success_at"):
                if times[key] is not None and not start <= times[key] <= now + timedelta(minutes=5):
                    raise ValueError()
            if not data["baseline_complete"]:
                if data["phase"] != "baseline" or data["scheduler_enabled"] or due != baseline:
                    raise ValueError()
            elif not data["scheduler_enabled"] or data["phase"] not in {"idle", "syncing"}:
                raise ValueError()
            if times["last_success_at"] is None and due != baseline:
                raise ValueError()
            if times["last_success_at"] is not None and due != next_scheduled(times["last_success_at"]):
                raise ValueError()
    except (ValueError, TypeError, KeyError, OverflowError):
        return result("invalid_record")

    age = now - times["heartbeat_at"]
    if age < -timedelta(minutes=5):
        return result("heartbeat_in_future")
    if age > timedelta(minutes=45):
        return result("heartbeat_stale")
    if not data["monitoring_active"]:
        return result("inactive")
    if not data["baseline_complete"]:
        return result("baseline_stalled" if now - baseline > timedelta(hours=6) else "baseline")
    if now - due > timedelta(hours=6):
        return result("initial_success_overdue" if times["last_success_at"] is None else "sync_overdue")
    return result("healthy")


def unique_object(pairs):
    data = {}
    for key, value in pairs:
        if key in data:
            raise ValueError("duplicate key")
        data[key] = value
    return data


def reject_constant(value):
    raise ValueError("nonfinite number")


def failure_fixture(name, now):
    due = now.replace(hour=6, minute=0, second=0, microsecond=0)
    while due > now - timedelta(hours=7) or due.day % 2 == 0:
        due -= timedelta(days=1)
    baseline = due - timedelta(days=1)
    start = baseline - timedelta(hours=4)
    fmt = lambda value: value.isoformat().replace("+00:00", "Z")
    data = dict(schema_version=1, heartbeat_at=fmt(now), phase="idle", monitoring_active=True,
                trial_started_at=fmt(start), trial_ends_at=fmt(start + timedelta(days=5)),
                baseline_ends_at=fmt(baseline), baseline_complete=True, scheduler_enabled=True,
                last_attempt_at=fmt(baseline), last_success_at=fmt(baseline),
                next_due_at=fmt(due), incident=None)
    if name == "missing-heartbeat":
        del data["heartbeat_at"]
    return data


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("health_file", nargs="?")
    parser.add_argument("--now", help="UTC ISO timestamp for deterministic checks")
    parser.add_argument("--test-case", choices=("missing-heartbeat", "overdue-success"),
                        help="Evaluate an in-memory failure fixture; never writes public health")
    args = parser.parse_args(argv)
    try:
        now = timestamp(args.now) if args.now else datetime.now(timezone.utc)
        if args.test_case:
            if args.health_file:
                raise ValueError()
            data = failure_fixture(args.test_case, now)
        else:
            if not args.health_file:
                raise ValueError()
            with Path(args.health_file).open("rb") as stream:
                raw = stream.read(MAX_BYTES + 1)
            if len(raw) > MAX_BYTES:
                raise ValueError()
            data = json.loads(raw.decode("utf-8"), object_pairs_hook=unique_object,
                              parse_constant=reject_constant)
        report = evaluate(data, now)
    except OSError:
        report = result("record_unreadable")
    except (ValueError, TypeError, UnicodeError, RecursionError):
        report = result("invalid_record")
    print(json.dumps(report, sort_keys=True))
    return 0 if report["healthy"] is True or report["status"] == "inactive" else 1


if __name__ == "__main__":
    raise SystemExit(main())
