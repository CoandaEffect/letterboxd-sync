"""Single worker, persistent safety latch and minute monitoring loop."""
from datetime import datetime, timedelta
import hashlib
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import threading
import time
from uuid import uuid4
from zoneinfo import ZoneInfo

from .core import (Baseline, Guard, StateStore, UTC, atomic_bytes, healthy,
                   instant, next_schedule, public_health, validate_csv)


RECOVERABLE_PAUSES = {"monitoring_missing", "worker_interrupted"}


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
        self.cleanup_verified = True
        self.attempt_id = None
        # The scraper can write result.json but cannot replace the coordinator's deadline evidence.
        self.timeout_path = self.directory.parent / "worker-timeout.json"

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

    @staticmethod
    def wait_for_group_exit(pid):
        deadline = time.monotonic() + 2
        while True:
            try:
                os.killpg(pid, 0)
            except ProcessLookupError:
                return True
            except PermissionError:
                return False
            if time.monotonic() >= deadline:
                return False
            time.sleep(.05)

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
            return self.wait_for_group_exit(identity["pid"])
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

    def start(self, reference, username, attempt_id):
        if self.running:
            raise RuntimeError("worker_overlap_refused")
        if self.process is not None:
            self.stop()
        if not self.cleanup_verified:
            raise RuntimeError("worker_cleanup_unverified")
        self.attempt_id = attempt_id
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
            [sys.executable, "-m", "green_app.worker", str(self.directory), username, attempt_id],
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
                try:
                    if self.attempt_id:
                        record = {"attempt_id": self.attempt_id, "error": "worker_wall_timeout"}
                        atomic_bytes(self.timeout_path, json.dumps(record).encode())
                finally:
                    self.stop()

    def stop(self):
        with self.lock:
            if self.timer:
                self.timer.cancel()
                self.timer = None
            if self.process is None:
                return None
            self.cleanup_verified = False
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
            self.cleanup_verified = self.wait_for_group_exit(self.process.pid)
            self.process = None
            if self.log:
                self.log.close()
            return code

    def completed(self):
        with self.lock:
            return self._completed()

    def interrupt(self):
        with self.lock:
            result = self._completed()
            if self.process is None:
                return result
            code = self.stop()
            return result or self.read_result(code, interrupted=code in (-signal.SIGTERM, -signal.SIGKILL))

    def _completed(self):
        if self.process is not None and time.monotonic() - self.started >= 600:
            self.deadline(self.process)
        if self.pending_result is not None:
            result, self.pending_result = self.pending_result, None
            return result
        if self.process is None or self.running:
            return None
        return self.read_result(self.stop())

    def read_attempt_result(self, attempt_id):
        try:
            result = json.loads((self.directory / "result.json").read_text())
        except (OSError, ValueError):
            return None
        if (not isinstance(result, dict) or type(result.get("ok")) is not bool
                or result.get("attempt_id") != attempt_id
                or not result["ok"] and not isinstance(result.get("error"), str)):
            return None
        result.pop("attempt_id", None)
        return result

    def read_result(self, code, interrupted=False):
        result = self.read_attempt_result(self.attempt_id)
        if result is None:
            return None if interrupted else {"ok": False, "error": "worker_resource_or_process_failure"}
        if code != 0:
            result["ok"] = False
        return result

    def recovered_result(self, attempt_id):
        if not isinstance(attempt_id, str) or not attempt_id:
            return None
        try:
            record = json.loads(self.timeout_path.read_text())
        except FileNotFoundError:
            return self.read_attempt_result(attempt_id)
        except (OSError, ValueError):
            record = None
        if (not isinstance(record, dict) or not isinstance(record.get("attempt_id"), str)
                or not record["attempt_id"] or record.get("error") != "worker_wall_timeout"):
            return {"ok": False, "error": "worker_timeout_record_unreadable"}
        if record["attempt_id"] == attempt_id:
            return {"ok": False, "error": record["error"]}
        return self.read_attempt_result(attempt_id)


class Coordinator:
    def __init__(self, config, directory, github, local, worker=None):
        self.config, self.directory, self.github, self.local = config, Path(directory), github, local
        self.store = StateStore(self.directory)
        self.state = self.store.load()
        self.worker = worker or Worker(directory)
        self.guard = Guard(self.state.get("baseline_p95", {}), self.state["guard_counts"])
        self.recovery_samples = []
        cleanup_pending = self.state.get("worker_cleanup_verified") is False
        if self.state["phase"] == "syncing" or self.state.get("worker_inflight") or cleanup_pending:
            safe = self.worker.recover(self.state.get("worker_identity"), self.state.get("worker_context"))
            result = self.worker.recovered_result(self.state.get("worker_attempt_id")) if safe else None
            failure = self.worker_safety_failure(result)
            if failure:
                self.state["worker_result"] = result
            code = failure or ("worker_interrupted" if safe else "worker_cleanup_unverified")
            prior = self.state.get("pause")
            reason = code
            if prior and prior not in RECOVERABLE_PAUSES:
                reason = code if prior == "worker_cleanup_unverified" and failure else prior
            elif prior and safe and not failure:
                reason = prior
            if reason != prior or cleanup_pending:
                self.state["pause_resume_nonce"] = config.resume_nonce
            self.state.update(phase="paused", current_incident=reason, pause=reason,
                              scheduler_enabled=False, worker_cleanup_verified=bool(safe),
                              worker_inflight=False, last_outcome="failed", candidate_pending=False)
            now = datetime.now(UTC)
            if not safe or reason == code:
                self.incident(code, now)
            if cleanup_pending and safe and reason != "worker_cleanup_unverified":
                self.recover("worker_cleanup_unverified", now)
            self.store.save(self.state)
        self.update_incident_state()

    def event(self, code, now, **values):
        record = {"at": now.isoformat(), "code": code, **values}
        print(json.dumps(record, sort_keys=True), flush=True)
        with (self.directory / "events.jsonl").open("a") as handle:
            handle.write(json.dumps(record, sort_keys=True) + "\n")

    def log_status(self, now, force=False):
        last = instant(self.state.get("status_logged_at"))
        if last and (now == last or not force and now - last < timedelta(minutes=15)):
            return

        def select(value, keys):
            return {key: value[key] for key in keys if key in value} if isinstance(value, dict) else None

        def memory(value):
            return select(value, ("VmRSS_mib", "VmPeak_mib", "VmSize_mib"))

        state = self.state
        status = public_health(state, now, bool(state["trial_started_at"]))
        status["candidate_summary"] = select(state.get("candidate_summary"), (
            "at", "films", "previous_films", "new_films", "dated_films", "lost_dates", "sha256", "resource_check"))
        worker = state.get("worker_result")
        status["worker_result"] = select(worker, ("ok", "error", "duration_seconds", "cpu_seconds"))
        if status["worker_result"] is not None:
            status["worker_result"]["memory"] = memory(worker.get("memory"))
        measured = state.get("last_sample")
        status["last_sample"] = select(measured, ("at", "cpu_percent", "available_mib", "ha_ms", "mqtt_ms"))
        if status["last_sample"] is not None:
            for key in ("app_memory", "worker_memory"):
                status["last_sample"][key] = memory(measured.get(key))
        status["baseline_coverage"] = select(state.get("baseline_coverage"), (
            "expected_minutes", "valid_minutes", "missing_minutes"))
        status["baseline_p95"] = select(state.get("baseline_p95"), ("ha_ms", "mqtt_ms"))
        status["notification_test_accepted"] = bool(state["notification_test_accepted"])
        status["notification_failure"] = bool(state.get("notification_failure", False))
        state["status_logged_at"] = now.isoformat()
        self.store.save(state)
        self.event("status", now, status=status)

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
        prior = self.state.get("pause")
        if prior and (prior not in RECOVERABLE_PAUSES or reason in RECOVERABLE_PAUSES):
            return
        self.recovery_samples.clear()
        self.state.update(pause=reason, phase="paused", scheduler_enabled=False,
                          pause_resume_nonce=self.config.resume_nonce)
        self.store.save(self.state)
        if not prior:
            reason = self.interrupt_worker(reason)
            self.state["pause"] = reason
            self.store.save(self.state)
        self.event("protective_pause", now, reason=reason)
        self.incident(reason, now)

    def interrupt_worker(self, reason):
        result = self.worker.interrupt()
        self.record_worker_exit(result)
        if reason in RECOVERABLE_PAUSES:
            return self.worker_safety_failure(result) or (
                reason if self.state["worker_cleanup_verified"] else "worker_cleanup_unverified")
        return reason

    def record_worker_exit(self, result):
        self.state.update(worker_inflight=False, worker_cleanup_verified=self.worker.cleanup_verified)
        if result is not None:
            self.state["worker_result"] = result

    def resource_failed(self, sample):
        return any(failed for _, failed, _ in self.guard.resource_rules(sample))

    def ready(self, sample):
        return healthy(sample) and not self.resource_failed(sample)

    def resume_if_ready(self, now, sample):
        reason = self.state["pause"]
        cleanup = self.state.get("worker_cleanup_verified")
        if not reason or cleanup is False or self.state.get("worker_inflight") or not self.ready(sample):
            self.recovery_samples.clear()
            return
        nonce = self.config.resume_nonce
        explicit = nonce and nonce != self.state.get("pause_resume_nonce", "")
        if not explicit:
            if (not self.config.active or reason not in RECOVERABLE_PAUSES
                    or reason == "worker_interrupted" and cleanup is not True):
                self.recovery_samples.clear()
                return
            if self.recovery_samples and not 50 <= (now - self.recovery_samples[-1]).total_seconds() <= 70:
                self.recovery_samples.clear()
            self.recovery_samples.append(now)
            if len(self.recovery_samples) < 3:
                return
        self.state.update(pause=None, phase="idle")
        if explicit:
            self.state["resume_nonce"] = nonce
        self.recovery_samples.clear()
        self.guard.counts.clear()
        self.recover(reason, now)
        if reason != "monitoring_missing":
            self.recover("monitoring_missing", now)
        if reason != "worker_interrupted" and cleanup is True:
            self.recover("worker_interrupted", now)
        self.update_incident_state()
        self.event("explicit_resume" if explicit else "automatic_resume", now, reason=reason)

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
        try:
            reference, sha = self.github.fetch_export()
            validate_csv(reference, reference)
            attempt_id = uuid4().hex
            self.state.update(reference_sha=sha, phase="syncing", worker_inflight=True,
                              worker_attempt_id=attempt_id, worker_identity=None, worker_context=self.worker.context(),
                              worker_cleanup_verified=False)
            self.store.save(self.state)
            self.worker.start(reference, self.config.username, attempt_id)
            self.state["worker_identity"] = self.worker.identity()
            self.store.save(self.state)
            self.event("sync_started", now)
        except Exception:
            if self.state.get("worker_inflight"):
                reason = self.interrupt_worker("worker_interrupted")
                if reason != "worker_interrupted":
                    self.pause(reason, now)
            self.failed_sync("sync_start_failed", now)

    def manual_request(self, now):
        nonce = self.config.sync_request_nonce
        if not nonce:
            return None
        fingerprint = hashlib.sha256(nonce.encode()).hexdigest()
        if fingerprint in self.state["consumed_sync_requests"]:
            return None
        if fingerprint != self.state["sync_request_observed"]:
            self.state["sync_request_observed"] = fingerprint
            self.store.save(self.state)
            self.event("manual_sync_requested", now)
            if len(self.state["consumed_sync_requests"]) >= 128:
                self.event("manual_sync_request_limit_reached", now)
        if len(self.state["consumed_sync_requests"]) >= 128:
            return None
        return fingerprint

    def accept_manual_request(self, fingerprint, now):
        # Persist consumption before any external call so a restart cannot replay it.
        self.state["consumed_sync_requests"].append(fingerprint)
        self.store.save(self.state)
        self.event("manual_sync_accepted", now)
        self.start_sync(now)

    def failed_sync(self, code, now):
        self.state.update(last_outcome="failed", phase="paused" if self.state["pause"] else "failed",
                          candidate_pending=False)
        self.state["outcomes"].append({"at": now.isoformat(), "outcome": "failed", "reason": code})
        self.state["outcomes"] = self.state["outcomes"][-32:]
        self.incident(code, now)
        self.store.save(self.state)

    @staticmethod
    def worker_safety_failure(result):
        error = result.get("error") if result and not result.get("ok") else None
        if error == "MemoryError":
            return "worker_memory_failure"
        if error in ("worker_resource_or_process_failure", "worker_wall_timeout", "worker_timeout_record_unreadable"):
            return error
        return None

    def finish_worker(self, now, sample, result):
        self.record_worker_exit(result)
        code = self.worker_safety_failure(result)
        if not self.state["worker_cleanup_verified"]:
            code = code or "worker_cleanup_unverified"
        if code:
            self.pause(code, now)
            self.failed_sync(code, now)
        elif not result.get("ok"):
            self.failed_sync("sync_failed", now)
        elif self.ready(sample):
            self.finish_candidate(now)
        else:
            reason = "post_sync_health_failed" if self.resource_failed(sample) else "monitoring_missing"
            self.pause(reason, now)

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
                self.state.update(candidate_pending=True, phase="idle")
                self.event("first_candidate_review_required", now, **{k: v for k, v in summary.items() if k != "at"})
                self.log_status(now, force=True)
                self.store.save(self.state)
                return
            if candidate != current:
                self.github.write_export(candidate, sha)
            self.state.update(first_published=True, candidate_pending=False,
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
        """Keep unconfigured startup local; configured inactive startup may publish health."""
        self.state["last_sample"] = sample
        self.state["scheduler_enabled"] = False
        force_status = False
        manual_request = self.manual_request(now)
        if not self.config.active and not self.state["trial_started_at"] and not self.config.github_token:
            self.log_status(now)
            self.store.save(self.state)
            return
        reason = None
        if self.config.active or self.state["trial_started_at"]:
            reason = self.guard.check(sample)
            if reason and reason != "monitoring_missing":
                self.pause(reason, now)
        if self.state["trial_started_at"]:
            if not self.config.active and self.worker.running:
                self.pause("activation_disabled_during_sync", now)
            if self.state.get("worker_inflight") and not self.state["pause"]:
                result = self.worker.completed()
                if result is not None:
                    force_status = True
                    self.finish_worker(now, sample, result)
        if reason == "monitoring_missing":
            self.pause(reason, now)
        self.resume_if_ready(now, sample)
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
            if self.state["scheduler_enabled"] and self.ready(sample):
                if self.state["candidate_pending"]:
                    if self.config.approve_first_publish:
                        self.finish_candidate(now)
                elif not self.worker.running and not self.state.get("worker_inflight"):
                    retry = instant(self.state.get("attempt_retry_at"))
                    if not retry or now >= retry:
                        if manual_request and self.state["first_published"]:
                            self.accept_manual_request(manual_request, now)
                        elif now >= instant(self.state["next_due_at"]):
                            self.start_sync(now)
            due = instant(self.state["next_due_at"])
            if self.state["baseline_complete"] and due and now > due + timedelta(hours=6):
                self.incident("sync_stale", now)
            if healthy(sample) and self.state["pause"] != "monitoring_missing":
                self.recover("monitoring_missing", now)
            self.daily_summary(now, sample)
        for code, incident in list(self.state["incidents"].items()):
            if incident.get("active") and not incident.get("notified"):
                self.incident(code, now)
            elif incident.get("recovery_pending"):
                self.recover(code, now)
        self.heartbeat(now)
        self.log_status(now, force=force_status)
        self.store.save(self.state)

    def shutdown(self):
        if self.worker.process is not None or self.state.get("worker_inflight"):
            prior = self.state["pause"]
            reason = self.interrupt_worker(prior or "worker_interrupted")
            if reason != prior:
                self.state["pause_resume_nonce"] = self.config.resume_nonce
            self.state.update(phase="paused", pause=reason, scheduler_enabled=False, candidate_pending=False,
                              last_outcome="failed", current_incident=reason)
            self.store.save(self.state)
