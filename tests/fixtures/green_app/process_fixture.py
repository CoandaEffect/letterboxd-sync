"""Local-only subprocess fixtures; never invoked by the deployed App."""
import json
import os
from pathlib import Path
import resource
import signal
import subprocess
import sys
import time

mode, directory = sys.argv[1:]
directory = Path(directory)
if mode == "memory":
    resource.setrlimit(resource.RLIMIT_AS, (64 * 1024 ** 2,) * 2)
    try:
        allocation = bytearray(128 * 1024 ** 2)
    except MemoryError:
        (directory / "result.json").write_text(json.dumps({"ok": False, "error": "MemoryError"}))
        sys.exit(1)
    sys.exit(2)
result_exits = {"successful_exit": 0, "abnormal_exit": 2, "ordinary_error": 1,
                "abnormal_error": 2, "signaled_error": -signal.SIGKILL}
if mode in result_exits:
    result = {"ok": mode in ("successful_exit", "abnormal_exit"), "attempt_id": "fixture-attempt"}
    if not result["ok"]:
        result["error"] = "ValueError"
    (directory / "result.json").write_text(json.dumps(result))
    (directory / (mode + ".ready")).write_text("ready")
    code = result_exits[mode]
    if code < 0:
        os.kill(os.getpid(), -code)
    sys.exit(code)
signal.signal(signal.SIGTERM, signal.SIG_IGN)
if mode in ("parent", "orphan"):
    child = subprocess.Popen([sys.executable, __file__, "child", str(directory)])
    (directory / "child.pid").write_text(str(child.pid))
(directory / (mode + ".ready")).write_text("ready")
if mode == "orphan":
    time.sleep(.1)
    sys.exit(0)
while True:
    time.sleep(.1)
