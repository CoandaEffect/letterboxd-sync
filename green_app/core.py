"""Pure state, scheduling, baseline and export safety rules."""
import csv
from dataclasses import dataclass, field, fields
from datetime import datetime, timedelta, timezone
import hashlib
import io
import json
import math
import os
from pathlib import Path, PurePosixPath
import re
import tempfile

UTC = timezone.utc
SCHEDULE = "0 6 */2 * *"
FIELDNAMES = ["Name", "Year", "Rating", "Liked", "Watch Date", "Runtime", "Rewatched", "Letterboxd URI"]


def instant(value):
    return datetime.fromisoformat(value.replace("Z", "+00:00")) if value else None


@dataclass
class Config:
    username: str = "coanda_effect"
    github_repository: str = "CoandaEffect/letterboxd-sync"
    github_token: str = field(default="", repr=False)
    github_branch: str = "main"
    csv_path: str = "letterboxd-watched.csv"
    schedule: str = SCHEDULE
    active: bool = False
    resume_nonce: str = ""
    approve_first_publish: bool = False
    summary_timezone: str = "America/Denver"
    summary_hour: int = 18
    notification_service: str = "script.andrew_phone_notification"
    notification_payload: dict = field(default_factory=lambda: {"notification": {"title": "{title}", "message": "{message}", "data": {"tag": "letterboxd-sync"}}})

    @classmethod
    def load(cls, path):
        value = json.loads(Path(path).read_text())
        config = cls(**{key: value[key] for key in {f.name for f in fields(cls)} if key in value})
        config.validate()
        return config

    def validate(self):
        if not re.fullmatch(r"[A-Za-z0-9_-]+", self.username):
            raise ValueError("invalid_username")
        if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", self.github_repository):
            raise ValueError("invalid_repository")
        path = PurePosixPath(self.csv_path)
        if path.is_absolute() or ".." in path.parts or not path.parts or "\\" in self.csv_path:
            raise ValueError("invalid_csv_path")
        if self.schedule != SCHEDULE or not 0 <= self.summary_hour <= 23:
            raise ValueError("unsupported_schedule_or_summary_hour")
        if not re.fullmatch(r"[a-z_]+\.[a-z0-9_]+", self.notification_service):
            raise ValueError("invalid_notification_service")
        if not isinstance(self.notification_payload, dict):
            raise ValueError("invalid_notification_payload")
        from zoneinfo import ZoneInfo
        ZoneInfo(self.summary_timezone)


def next_schedule(now):
    candidate = now.astimezone(UTC).replace(hour=6, minute=0, second=0, microsecond=0)
    while candidate <= now or candidate.day % 2 == 0:
        candidate += timedelta(days=1)
    return candidate


def cpu_percent(previous, current):
    if previous is None or len(current) < 8:
        return None
    delta = [b - a for a, b in zip(previous[:8], current[:8])]
    if any(value < 0 for value in delta) or sum(delta) <= 0:
        return None
    return 100 * (sum(delta) - delta[3] - delta[4]) / sum(delta)


def healthy(sample):
    return all(isinstance(sample.get(key), (int, float)) and math.isfinite(sample[key])
               for key in ("cpu_percent", "available_mib", "ha_ms", "mqtt_ms"))


class Baseline:
    def __init__(self, started, samples=None):
        self.started = started
        self.samples = samples if samples is not None else []

    def add(self, sample):
        at = instant(sample["at"])
        if self.started <= at < self.started + timedelta(hours=4):
            self.samples.append(sample)

    def complete(self, now):
        if now < self.started + timedelta(hours=4):
            return False
        return self.coverage()["valid_minutes"] == 240

    def coverage(self):
        minutes = set()
        for sample in self.samples:
            offset = (instant(sample["at"]) - self.started).total_seconds()
            minute = round(offset / 60)
            # Ten seconds admits probe/scheduler jitter without filling a missed minute.
            if healthy(sample) and abs(offset - minute * 60) <= 10:
                minutes.add(minute)
        missing = [minute for minute in range(240) if minute not in minutes]
        return {"expected_minutes": 240, "valid_minutes": 240 - len(missing), "missing_minutes": missing}

    def p95(self, key):
        values = sorted(s[key] for s in self.samples if healthy(s))
        return values[max(0, math.ceil(.95 * len(values)) - 1)] if values else None


class Guard:
    def __init__(self, baseline, counts=None):
        self.baseline = baseline
        self.counts = counts if counts is not None else {}

    def check(self, sample):
        rules = [
            ("host_memory", sample.get("available_mib") is not None and sample["available_mib"] < 512, 1),
            ("host_cpu", sample.get("cpu_percent") is not None and sample["cpu_percent"] > 80, 5),
            ("ha_latency", sample.get("ha_ms") is not None and sample["ha_ms"] > max(500, 3 * (self.baseline.get("ha_ms") or 0)), 3),
            ("mqtt_latency", sample.get("mqtt_ms") is not None and sample["mqtt_ms"] > max(250, 3 * (self.baseline.get("mqtt_ms") or 0)), 3),
            ("monitoring_missing", not healthy(sample), 3),
        ]
        reason = None
        for name, failed, required in rules:
            self.counts[name] = self.counts.get(name, 0) + 1 if failed else 0
            if failed and self.counts[name] >= required and reason is None:
                reason = name
        return reason


class StateStore:
    def __init__(self, directory):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.path = self.directory / "green-state.json"

    def load(self):
        state = {"trial_started_at": None, "trial_ends_at": None, "baseline_ends_at": None,
                 "baseline_complete": False, "baseline_samples": [], "pause": None,
                 "incidents": {}, "last_attempt_at": None, "last_success_at": None,
                 "next_due_at": None, "phase": "startup", "resume_nonce": "",
                 "guard_counts": {}, "candidate_pending": False, "first_published": False,
                 "outcomes": [], "notification_test_accepted": False}
        if self.path.exists():
            try:
                existing = json.loads(self.path.read_text())
                if not isinstance(existing, dict):
                    raise ValueError("state_is_not_object")
                state.update(existing)
            except (ValueError, OSError) as error:
                raise ValueError("persistent_state_unreadable_refusing_clock_reset") from error
        return state

    def save(self, state):
        atomic_bytes(self.path, json.dumps(state, sort_keys=True).encode())


def atomic_bytes(path, content):
    path = Path(path)
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as handle:
        temporary = Path(handle.name)
        try:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        except BaseException:
            temporary.unlink(missing_ok=True)
            raise
    try:
        os.replace(temporary, path)
        directory_fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        temporary.unlink(missing_ok=True)


def csv_rows(content):
    reader = csv.DictReader(io.StringIO(content.decode("utf-8")))
    if reader.fieldnames != FIELDNAMES:
        raise ValueError("csv_columns_changed")
    rows = list(reader)
    if any(None in row or any(value is None for value in row.values()) for row in rows):
        raise ValueError("malformed_csv_row")
    return rows


def validate_csv(candidate, reference):
    rows, previous = csv_rows(candidate), csv_rows(reference)
    indexed = {row["Letterboxd URI"]: row for row in rows}
    if not rows or len(indexed) != len(rows) or any(not uri for uri in indexed):
        raise ValueError("empty_or_duplicate_export")
    if any(row["Letterboxd URI"] not in indexed for row in previous):
        raise ValueError("previous_films_missing")
    if any(row["Watch Date"] and not indexed[row["Letterboxd URI"]]["Watch Date"] for row in previous):
        raise ValueError("previous_watch_dates_missing")
    return {"films": len(rows), "previous_films": len(previous),
            "new_films": len(rows) - len(previous), "dated_films": sum(bool(r["Watch Date"]) for r in rows),
            "lost_dates": 0, "sha256": hashlib.sha256(candidate).hexdigest()}


def public_health(state, now, monitoring):
    phase = "paused" if state.get("pause") else state.get("phase", "startup")
    incident = state.get("pause") or state.get("current_incident")
    return {"schema_version": 1, "heartbeat_at": now.isoformat(), "phase": phase,
            "monitoring_active": bool(monitoring),
            **{key: state.get(key) for key in ("trial_started_at", "trial_ends_at", "baseline_ends_at",
                                               "last_attempt_at", "last_success_at", "next_due_at")},
            "baseline_complete": bool(state.get("baseline_complete")),
            "scheduler_enabled": bool(state.get("scheduler_enabled", False) and not state.get("pause")),
            "incident": {"code": incident} if incident else None}
