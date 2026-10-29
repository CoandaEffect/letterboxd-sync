"""No inbound server; options and review state live in /data."""
import argparse
from datetime import datetime
import fcntl
import json
import os
from pathlib import Path
import signal
import time

from .adapters import GitHub, Local
from .core import Config, SCHEDULE, StateStore, UTC, public_health
from .runtime import Coordinator


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--data-dir", default="/data")
    parser.add_argument("--options", default="/data/options.json")
    parser.add_argument("--self-check", action="store_true")
    parser.add_argument("--status", action="store_true")
    arguments = parser.parse_args()
    if arguments.self_check:
        Config().validate()
        import paho.mqtt.client as mqtt
        assert mqtt.CallbackAPIVersion.VERSION2
        print(json.dumps({"ok": True, "offline": True, "schedule": SCHEDULE,
                          "worker_address_space_mib": 256, "worker_wall_seconds": 600,
                          "worker_cpu_seconds": 120, "trial_started": False}))
        return 0
    directory = Path(arguments.data_dir)
    if arguments.status:
        state = StateStore(directory).load()
        result = public_health(state, datetime.now(UTC), bool(state["trial_started_at"]))
        result["candidate_summary"] = state.get("candidate_summary")
        result["worker_result"] = state.get("worker_result")
        result["last_sample"] = state.get("last_sample")
        result["notification_test_accepted"] = state["notification_test_accepted"]
        result["baseline_coverage"] = state.get("baseline_coverage")
        result["baseline_p95"] = state.get("baseline_p95")
        result["notification_failure"] = state.get("notification_failure", False)
        print(json.dumps(result, sort_keys=True))
        return 0
    directory.mkdir(parents=True, exist_ok=True)
    lock = (directory / "coordinator.lock").open("a")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        raise SystemExit("coordinator_overlap_refused")
    config = Config.load(arguments.options)
    coordinator = Coordinator(config, directory, GitHub(config), Local(config))
    stopped = False

    def stop(signum, frame):
        nonlocal stopped
        stopped = True

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    next_sample = time.monotonic()
    try:
        while not stopped:
            if time.monotonic() >= next_sample:
                updated = Config.load(arguments.options)
                if updated != config:
                    if coordinator.local.mqtt:
                        coordinator.local.mqtt.close()
                    config = updated
                    coordinator.config = updated
                    coordinator.github = GitHub(updated)
                    coordinator.local = Local(updated)
                    if not coordinator.state["trial_started_at"]:
                        coordinator.state["preflight_accepted_at"] = None
                        coordinator.state["notification_test_accepted"] = False
                now = datetime.now(UTC)
                sample = coordinator.local.sample(now, coordinator.worker.pid)
                coordinator.tick(now, sample)
                next_sample += 60
                if time.monotonic() >= next_sample:
                    next_sample = time.monotonic() + 60
            time.sleep(min(1, max(.01, next_sample - time.monotonic())))
    finally:
        coordinator.shutdown()
        if coordinator.local.mqtt:
            coordinator.local.mqtt.close()
        lock.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
