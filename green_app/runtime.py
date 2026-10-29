"""Single worker, persistent safety latch and minute monitoring loop."""
from datetime import datetime, timedelta
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import threading
import time
from zoneinfo import ZoneInfo

from .core import (Baseline, Guard, StateStore, UTC, atomic_bytes, healthy,
                   instant, next_schedule, public_health, validate_csv)


class Worker:
    def __init__(self, directory):
        self.directory = Path(directory) / "worker"
        self.directory.mkdir(parents=True, exist_ok=True)
        self.process = None
        self.started = None
        self.log = None
        self.lock = threading.RLock()
        self.timer = None
        self.pending_result = None

    @staticmethod
    def environment(username):
        return {"PATH": os.defpath, "LANG": "C.UTF-8", "PYTHONUNBUFFERED": "1",
                "PYTHONDONTWRITEBYTECODE": "1", "PYTHONPATH": "/opt/packages",
                "LETTERBOXD_USERNAME": username}

    @staticmethod
    def context():
        try:
            return {"boot_id": Path("/proc/sys/kernel/random/boot_id").read_text().strip(),
                    "pid_namespace": os.readlink("/proc/self/ns/pid")}
        except OSError:
            return None

    @staticmethod
    def process_identity(pid):
        try:
            fields = Path(f"/proc/{pid}/stat").read_text().rpartition(") ")[2].split()
            return {"pid": pid, "start_ticks": fields[19], "group": int(fields[2]), "session": int(fields[3])}
        except (OSError, ValueError, IndexError):
            return None

    def identity(self):
        return self.process_identity(self.pid) if self.pid else None

    def recover(self, identity, context):
        current_context = self.context()
        if context and current_context and context != current_context:
            return True
        if not identity or not context or not current_context:
            return False
        current = self.process_identity(identity["pid"])
        if current is None:
            try:
                os.killpg(identity["pid"], 0)
                return False
            except ProcessLookupError:
                return True
            except PermissionError:
                return False
        if current["start_ticks"] != identity["start_ticks"]:
            return True
        if current["group"] != identity["pid"] or current["session"] != identity["pid"]:
            return False
        try:
            os.killpg(identity["pid"], signal.SIGTERM)
            deadline = time.monotonic() + 2
            while time.monotonic() < deadline:
                if self.process_identity(identity["pid"]) is None:
                    break
                time.sleep(.05)
            os.killpg(identity["pid"], signal.SIGKILL)
            return True
        except ProcessLookupError:
            return True
        except PermissionError:
            return False

    @property
    def running(self):
        process = self.process
        return process is not None and process.poll() is None

    @property
    def pid(self):
        process = self.process
        return process.pid if process is not None else None

    def start(self, reference, username):
        if self.running:
            raise RuntimeError("worker_overlap_refused")
        if self.process is not None:
            self.stop()
        self.pending_result = None
        for name in ("candidate.csv", "result.json"):
            (self.directory / name).unlink(missing_ok=True)
        atomic_bytes(self.directory / "reference.csv", reference)
        identity = {}
        if os.geteuid() == 0:
            # HA options and coordinator state stay unreadable to the scraper.
            os.chmod(self.directory.parent, 0o711)
            for file in self.directory.parent.iterdir():
                if file.is_file():
                    os.chmod(file, 0o600)
            os.chown(self.directory, 65534, 65534)
            os.chmod(self.directory, 0o700)
            os.chown(self.directory / "reference.csv", 65534, 65534)
            identity = {"user": 65534, "group": 65534, "extra_groups": []}
        self.log = (self.directory / "worker.log").open("wb")
        self.process = subprocess.Popen(
            [sys.executable, "-m", "green_app.worker", str(self.directory), username],
            env=self.environment(username), stdin=subprocess.DEVNULL, stdout=self.log,
            stderr=subprocess.STDOUT, start_new_session=True, close_fds=True, **identity)
        self.started = time.monotonic()
        self.timer = threading.Timer(600, self.deadline, args=(self.process,))
        self.timer.daemon = True
        self.timer.start()

    def deadline(self, process):
        with self.lock:
            if self.process is process:
                self.pending_result = {"ok": False, "error": "worker_wall_timeout"}
                self.stop()

    def stop(self):
        with self.lock:
            if self.timer:
                self.timer.cancel()
                self.timer = None
            if self.process is None:
                return None
            # The group may outlive its leader; the wall timer must stop both.
            try:
                os.killpg(self.process.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            try:
                self.process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                pass
            try:
                os.killpg(self.process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            code = self.process.wait(timeout=2)
            self.process = None
            if self.log:
                self.log.close()
            return code

    def completed(self):
        with self.lock:
            return self._completed()

    def _completed(self):
        if self.pending_result is not None:
            result, self.pending_result = self.pending_result, None
            return result
        if self.process is None:
            return None
        timeout = time.monotonic() - self.started >= 600
        if self.running and not timeout:
            return None
        code = self.stop()
        if timeout:
            return {"ok": False, "error": "worker_wall_timeout"}
        try:
            result = json.loads((self.directory / "result.json").read_text())
            if code != 0:
                result["ok"] = False
            return result
        except (OSError, ValueError):
            return {"ok": False, "error": "worker_resource_or_process_failure"}


class Coordinator:
    def __init__(self, config, directory, github, local, worker=None):
        self.config, self.directory, self.github, self.local = config, Path(directory), github, local
        self.store = StateStore(self.directory)
        self.state = self.store.load()
        self.worker = worker or Worker(directory)
        self.guard = Guard(self.state.get("baseline_p95", {}), self.state["guard_counts"])
        if self.state["phase"] == "syncing" or self.state.get("worker_cleanup_verified") is False:
            safe = self.worker.recover(self.state.get("worker_identity"), self.state.get("worker_context"))
            code = "worker_interrupted" if safe else "worker_cleanup_unverified"
            self.state.update(phase="paused", current_incident=code, pause=code,
                              pause_resume_nonce=config.resume_nonce, worker_cleanup_verified=bool(safe),
                              worker_inflight=False, last_outcome="failed", candidate_pending=False)
            self.incident(code, datetime.now(UTC))
            self.store.save(self.state)

    def event(self, code, now, **values):
        record = {"at": now.isoformat(), "code": code, **values}
        print(json.dumps(record, sort_keys=True), flush=True)
        with (self.directory / "events.jsonl").open("a") as handle:
            handle.write(json.dumps(record, sort_keys=True) + "\n")

    def incident(self, code, now):
        if code in self.state["incidents"] and not self.state["incidents"][code].get("active", True):
            self.state["incidents"][code] = {"started_at": now.isoformat(), "notified": False}
        incident = self.state["incidents"].setdefault(code, {"started_at": now.isoformat(), "notified": False})
        incident["active"] = True
        self.state["current_incident"] = self.state.get("pause") or code
        last = instant(incident.get("delivery_attempt_at"))
        if incident["notified"] or last and now - last < timedelta(hours=1):
            return
        incident["delivery_attempt_at"] = now.isoformat()
        self.store.save(self.state)
        accepted = self.local.notify("Letterboxd Green alert", code.replace("_", " "))
        incident["notified"] = bool(accepted)
        incident["delivery"] = "service_accepted" if accepted else "failed"
        self.event("incident", now, incident=code, notification_delivery=incident["delivery"])
        self.update_incident_state()
        self.store.save(self.state)

    def update_incident_state(self):
        incidents = self.state["incidents"]
        failed = any((item.get("active") and item.get("delivery") == "failed") or
                     (item.get("recovery_pending") and item.get("recovery_delivery") == "failed")
                     for item in incidents.values()) or bool(self.state.get("pending_summary"))
        self.state["notification_failure"] = failed
        self.state["current_incident"] = (self.state.get("pause") or
            ("notification_delivery_failed" if failed else None) or
            next((name for name, item in incidents.items() if item.get("active")), None))

    def recover(self, code, now):
        incident = self.state["incidents"].get(code)
        if incident is None or not incident.get("active") and not incident.get("recovery_pending"):
            return
        if incident.get("active"):
            incident["active"] = False
            incident["recovered_at"] = now.isoformat()
            self.event("incident_recovered", now, incident=code)
        if incident.get("notified") and not incident.get("recovery_notified"):
            incident["recovery_pending"] = True
            last = instant(incident.get("recovery_attempt_at"))
            if last and now - last < timedelta(hours=1):
                return
            incident["recovery_attempt_at"] = now.isoformat()
            self.store.save(self.state)
            accepted = self.local.notify("Letterboxd Green recovery", code.replace("_", " ") + " recovered")
            incident["recovery_delivery"] = "service_accepted" if accepted else "failed"
            if not accepted:
                self.update_incident_state()
                self.event("notification_recovery_failed", now, incident=code)
                self.store.save(self.state)
                return
            incident["recovery_notified"] = True
            incident["recovery_pending"] = False
        self.update_incident_state()
        self.store.save(self.state)

    def pause(self, reason, now):
        if self.state.get("pause"):
            return
        self.state.update(pause=reason, phase="paused", scheduler_enabled=False,
                          pause_resume_nonce=self.config.resume_nonce)
        self.store.save(self.state)
        self.worker.stop()
        self.state["worker_inflight"] = False
        self.event("protective_pause", now, reason=reason)
        self.incident(reason, now)

    def ready(self, sample):
        return (healthy(sample) and sample["available_mib"] >= 512 and sample["cpu_percent"] <= 80
                and sample["ha_ms"] <= max(500, 3 * (self.state.get("baseline_p95", {}).get("ha_ms") or 0))
                and sample["mqtt_ms"] <= max(250, 3 * (self.state.get("baseline_p95", {}).get("mqtt_ms") or 0)))

    def start_trial(self, now, sample):
        if not self.ready(sample):
            return
        if not self.state.get("preflight_accepted_at"):
            try:
                reference, _ = self.github.fetch_export()
                validate_csv(reference, reference)
            except Exception:
                self.incident("github_preflight_failed", now)
                return
            if not self.state["notification_test_accepted"]:
                accepted = self.local.notify("Letterboxd Green notification test",
                    "Service acceptance test before the five-day observation. Phone receipt is verified separately.")
                self.state["notification_test_accepted"] = bool(accepted)
                if not accepted:
                    self.incident("notification_delivery_failed", now)
                    return
            self.state["preflight_accepted_at"] = now.isoformat()
            self.recover("github_preflight_failed", now)
            self.recover("notification_delivery_failed", now)
            self.event("preflight_accepted_waiting_for_next_healthy_sample", now)
            self.store.save(self.state)
            return
        if now <= instant(self.state["preflight_accepted_at"]):
            return
        self.state.update(trial_started_at=now.isoformat(), trial_ends_at=(now + timedelta(days=5)).isoformat(),
                          baseline_ends_at=(now + timedelta(hours=4)).isoformat(),
                          next_due_at=(now + timedelta(hours=4)).isoformat(), phase="baseline")
        self.event("trial_started", now, trial_ends_at=self.state["trial_ends_at"],
                   baseline_ends_at=self.state["baseline_ends_at"])
        self.store.save(self.state)

    def start_sync(self, now):
        if self.worker.running or self.state.get("worker_inflight"):
            return
        self.state["last_attempt_at"] = now.isoformat()
        self.state["attempt_retry_at"] = (now + timedelta(minutes=15)).isoformat()
        self.store.save(self.state)
        started = False
        try:
            reference, sha = self.github.fetch_export()
            validate_csv(reference, reference)
            self.state.update(reference_sha=sha, phase="syncing", worker_inflight=True,
                              worker_identity=None, worker_context=self.worker.context())
            self.store.save(self.state)
            self.worker.start(reference, self.config.username)
            started = True
            self.state["worker_identity"] = self.worker.identity()
            self.store.save(self.state)
            self.event("sync_started", now)
        except Exception:
            if started:
                self.worker.stop()
            self.failed_sync("sync_start_failed", now)

    def failed_sync(self, code, now):
        self.state.update(last_outcome="failed", phase="failed", worker_inflight=False,
                          candidate_pending=False)
        self.state["outcomes"].append({"at": now.isoformat(), "outcome": "failed", "reason": code})
        self.state["outcomes"] = self.state["outcomes"][-32:]
        self.incident(code, now)
        self.store.save(self.state)

    def finish_candidate(self, now):
        try:
            candidate = (self.directory / "worker" / "candidate.csv").read_bytes()
            current, sha = self.github.fetch_export()
            summary = validate_csv(candidate, current)
            if sha != self.state.get("reference_sha"):
                self.failed_sync("concurrent_export_changed", now)
                return
            summary.update(at=now.isoformat(), resource_check="worker_completed_within_limits")
            self.state["candidate_summary"] = summary
            atomic_bytes(self.directory / "candidate-summary.json", json.dumps(summary, sort_keys=True).encode())
            if not self.state["first_published"] and not self.config.approve_first_publish:
                self.state.update(candidate_pending=True, phase="idle", worker_inflight=False)
                self.event("first_candidate_review_required", now, **{k: v for k, v in summary.items() if k != "at"})
                self.store.save(self.state)
                return
            if candidate != current:
                self.github.write_export(candidate, sha)
            self.state.update(first_published=True, candidate_pending=False, worker_inflight=False,
                last_success_at=now.isoformat(), next_due_at=next_schedule(now).isoformat(), phase="idle",
                last_outcome="unchanged" if candidate == current else "published")
            self.state["outcomes"].append({"at": now.isoformat(), "outcome": self.state["last_outcome"],
                                         "duration_seconds": self.state.get("worker_result", {}).get("duration_seconds")})
            self.state["outcomes"] = self.state["outcomes"][-32:]
            for code in list(self.state["incidents"]):
                if code in ("sync_start_failed", "sync_failed", "concurrent_export_changed", "sync_stale", "worker_interrupted"):
                    self.recover(code, now)
            self.event("sync_success", now, outcome=self.state["last_outcome"], films=summary["films"])
        except Exception:
            self.failed_sync("sync_failed", now)
        self.store.save(self.state)

    def daily_summary(self, now, sample):
        day = now.astimezone(ZoneInfo(self.config.summary_timezone))
        metrics = self.state.setdefault("daily_metrics", {})
        metrics["samples"] = metrics.get("samples", 0) + 1
        metrics["missing"] = metrics.get("missing", 0) + int(not healthy(sample))
        for key in ("cpu_percent", "ha_ms", "mqtt_ms"):
            value = sample.get(key)
            if value is not None:
                metrics[key + "_max"] = max(metrics.get(key + "_max", value), value)
        available = sample.get("available_mib")
        if available is not None:
            metrics["available_mib_min"] = min(metrics.get("available_mib_min", available), available)
        for name in ("app_memory", "worker_memory"):
            for key, value in (sample.get(name) or {}).items():
                metrics[name + "_" + key + "_max"] = max(metrics.get(name + "_" + key + "_max", value), value)
        pending = self.state.get("pending_summary")
        if not pending and (day.hour < self.config.summary_hour or self.state.get("summary_day") == day.date().isoformat()):
            return
        text = (f"Phase {self.state['phase']}. Baseline p95(ms): {self.state.get('baseline_p95', {})}. "
                f"Baseline resources: {self.state.get('baseline_resources', {})}. "
                f"Resources: {metrics}. Recent sync outcomes: {self.state['outcomes'][-3:]}. "
                f"Last success: {self.state['last_success_at']}. API/MQTT probes do not measure physical device latency.")
        if not pending:
            pending = {"day": day.date().isoformat(), "title": "Letterboxd Green daily summary", "message": text}
            self.state["pending_summary"] = pending
            self.state["daily_metrics"] = {}
        attempted = instant(pending.get("attempted_at"))
        if attempted and now - attempted < timedelta(hours=1):
            return
        pending["attempted_at"] = now.isoformat()
        self.store.save(self.state)
        if self.local.notify(pending["title"], pending["message"]):
            self.state["summary_day"] = pending["day"]
            del self.state["pending_summary"]
            self.recover("daily_summary_delivery_failed", now)
            self.update_incident_state()
            self.event("daily_summary_service_accepted", now)
        else:
            self.incident("daily_summary_delivery_failed", now)

    def heartbeat(self, now):
        last = instant(self.state.get("heartbeat_attempt_at"))
        if last and now - last < timedelta(minutes=15):
            return
        self.state["heartbeat_attempt_at"] = now.isoformat()
        try:
            self.github.heartbeat(public_health(self.state, now, bool(self.state["trial_started_at"])))
            self.state["heartbeat_published_at"] = now.isoformat()
            self.recover("heartbeat_publish_failed", now)
        except Exception:
            self.incident("heartbeat_publish_failed", now)

    def tick(self, now, sample):
        self.state["last_sample"] = sample
        self.state["scheduler_enabled"] = False
        if (self.state["pause"] and self.config.resume_nonce and
                self.state.get("worker_cleanup_verified") is not False and
                self.config.resume_nonce != self.state.get("pause_resume_nonce", "") and self.ready(sample)):
            reason = self.state["pause"]
            self.state.update(pause=None, phase="idle", resume_nonce=self.config.resume_nonce)
            self.guard.counts.clear()
            self.recover(reason, now)
            self.event("explicit_resume", now)
        if self.config.active or self.state["trial_started_at"]:
            reason = self.guard.check(sample)
            if reason:
                self.pause(reason, now)
        if self.config.active and not self.state["trial_started_at"] and not self.state["pause"]:
            self.start_trial(now, sample)
        if self.state["trial_started_at"]:
            with (self.directory / "samples.jsonl").open("a") as handle:
                handle.write(json.dumps(sample) + "\n")
            baseline = Baseline(instant(self.state["trial_started_at"]), self.state["baseline_samples"])
            baseline.add(sample)
            if not self.state["baseline_complete"]:
                self.state["baseline_coverage"] = baseline.coverage()
            if not self.state["baseline_complete"] and baseline.complete(now):
                self.state.update(baseline_complete=True,
                    baseline_p95={key: baseline.p95(key) for key in ("ha_ms", "mqtt_ms")}, phase="idle")
                self.state["baseline_resources"] = {
                    "cpu_p95": baseline.p95("cpu_percent"),
                    "minimum_available_mib": min(s["available_mib"] for s in baseline.samples if healthy(s))}
                self.state["baseline_samples"] = []
                self.guard.baseline = self.state["baseline_p95"]
                self.event("baseline_completed", now, p95=self.state["baseline_p95"])
            if not self.state["baseline_complete"] and not self.state["pause"]:
                self.state["phase"] = "baseline"
                if now > instant(self.state["baseline_ends_at"]) + timedelta(minutes=2):
                    self.incident("baseline_coverage_missing", now)
            self.state["scheduler_enabled"] = bool(self.config.active and self.state["baseline_complete"] and not self.state["pause"])
            if not self.config.active and self.worker.running:
                self.pause("activation_disabled_during_sync", now)
            if self.state.get("worker_inflight") and not self.state["pause"]:
                result = self.worker.completed()
                if result is not None:
                    self.state["worker_result"] = result
                    self.state["worker_inflight"] = False
                    if result.get("ok") and self.ready(sample):
                        self.finish_candidate(now)
                    elif result.get("ok"):
                        self.pause("post_sync_health_failed", now)
                    else:
                        error = result.get("error")
                        if error in ("MemoryError", "worker_resource_or_process_failure", "worker_wall_timeout"):
                            code = "worker_memory_failure" if error == "MemoryError" else error
                            self.failed_sync(code, now)
                            self.pause(code, now)
                        else:
                            self.failed_sync("sync_failed", now)
            if self.state["scheduler_enabled"] and self.ready(sample):
                if self.state["candidate_pending"]:
                    if self.config.approve_first_publish:
                        self.finish_candidate(now)
                elif not self.worker.running and not self.state.get("worker_inflight"):
                    retry = instant(self.state.get("attempt_retry_at"))
                    if now >= instant(self.state["next_due_at"]) and (not retry or now >= retry):
                        self.start_sync(now)
            due = instant(self.state["next_due_at"])
            if self.state["baseline_complete"] and due and now > due + timedelta(hours=6):
                self.incident("sync_stale", now)
            if healthy(sample):
                self.recover("monitoring_missing", now)
            self.daily_summary(now, sample)
        for code, incident in list(self.state["incidents"].items()):
            if incident.get("active") and not incident.get("notified"):
                self.incident(code, now)
            elif incident.get("recovery_pending"):
                self.recover(code, now)
        self.heartbeat(now)
        self.store.save(self.state)

    def shutdown(self):
        if self.worker.process is not None:
            self.worker.stop()
            self.state.update(phase="paused", pause="worker_interrupted", worker_inflight=False,
                              pause_resume_nonce=self.config.resume_nonce, worker_cleanup_verified=True,
                              last_outcome="failed", current_incident="worker_interrupted")
            self.store.save(self.state)
