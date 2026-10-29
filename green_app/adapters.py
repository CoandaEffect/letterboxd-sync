"""Bounded, credential-isolated GitHub, Supervisor and MQTT adapters.

API contracts: contributors.home-assistant.io/apps/supervisor-api/services/,
docs.github.com/en/rest/repos/contents, eclipse.dev/paho/files/paho.mqtt.python/html/client.html.
"""
import base64
import json
import os
from pathlib import Path
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid

from .core import cpu_percent


class APIError(RuntimeError):
    def __init__(self, code):
        self.code = code
        super().__init__(f"api_http_{code}")


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, message, headers, url):
        return None


class JSONAPI:
    def __init__(self, base, token, timeout):
        self.base, self.token, self.timeout = base, token, timeout
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())

    def request(self, path, method="GET", value=None):
        if not path.startswith("/") or path.startswith("//"):
            raise ValueError("api_path_invalid")
        request = urllib.request.Request(self.base + path,
                    data=json.dumps(value).encode() if value is not None else None,
                    headers={"Authorization": "Bearer " + self.token,
                             "Content-Type": "application/json", "Accept": "application/vnd.github+json",
                             "User-Agent": "letterboxd-green/1", "X-GitHub-Api-Version": "2022-11-28"},
                    method=method)
        try:
            with self.opener.open(request, timeout=self.timeout) as response:
                content = response.read(2 * 1024 * 1024 + 1)
                if len(content) > 2 * 1024 * 1024:
                    raise ValueError("api_response_too_large")
                return json.loads(content) if content else None
        except urllib.error.HTTPError as error:
            raise APIError(error.code) from None


class GitHub:
    def __init__(self, config):
        self.config = config
        self.api = JSONAPI("https://api.github.com", config.github_token, 10)
        self.prefix = "/repos/" + config.github_repository

    def contents(self, path, branch):
        return self.api.request(self.prefix + "/contents/" + urllib.parse.quote(path, safe="/") +
                                "?ref=" + urllib.parse.quote(branch, safe=""))

    def fetch_export(self):
        result = self.contents(self.config.csv_path, self.config.github_branch)
        if result.get("encoding") != "base64":
            raise ValueError("unsupported_github_content_encoding")
        return base64.b64decode(result["content"]), result["sha"]

    def write_export(self, content, sha):
        self.put(self.config.csv_path, self.config.github_branch, content, sha,
                 "Update Letterboxd watched films from Green")

    def put(self, path, branch, content, sha, message):
        payload = {"message": message, "branch": branch,
                   "content": base64.b64encode(content).decode()}
        if sha:
            payload["sha"] = sha
        return self.api.request(self.prefix + "/contents/" + urllib.parse.quote(path, safe="/"),
                                "PUT", payload)

    def heartbeat(self, record):
        try:
            current = self.contents("health.json", "green-status")
            sha = current["sha"]
        except APIError as error:
            if error.code != 404:
                raise
            ref_path = self.prefix + "/git/ref/heads/" + urllib.parse.quote(self.config.github_branch, safe="")
            reference = self.api.request(ref_path)
            try:
                self.api.request(self.prefix + "/git/refs", "POST", {
                    "ref": "refs/heads/green-status", "sha": reference["object"]["sha"]})
            except APIError as conflict:
                if conflict.code not in (409, 422):
                    raise
            try:
                sha = self.contents("health.json", "green-status")["sha"]
            except APIError as missing:
                if missing.code != 404:
                    raise
                sha = None
        self.put("health.json", "green-status", json.dumps(record, sort_keys=True).encode(), sha,
                 "Update Green sync health")


def replace_leaves(value, title, message):
    if isinstance(value, dict):
        return {key: replace_leaves(item, title, message) for key, item in value.items()}
    if isinstance(value, list):
        return [replace_leaves(item, title, message) for item in value]
    if isinstance(value, str):
        return value.replace("{title}", title).replace("{message}", message)
    return value


class MQTTProbe:
    REQUEST = "letterboxd_sync/health/request"
    RESPONSE = "letterboxd_sync/health/response"

    def __init__(self, service):
        import paho.mqtt.client as mqtt
        self.mqtt = mqtt
        self.ready, self.received = threading.Event(), threading.Event()
        self.lock = threading.Lock()
        self.correlation = None
        self.client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2,
                                  client_id="letterboxd-green-" + uuid.uuid4().hex,
                                  clean_session=True)
        self.client.connect_timeout = 1
        self.client.username_pw_set(service["username"], service["password"])
        if service.get("ssl"):
            self.client.tls_set()
        self.client.on_connect = self._connect
        self.client.on_subscribe = self._subscribed
        self.client.on_disconnect = lambda *args: self.ready.clear()
        self.client.on_message = self._message
        self.client.connect(service["host"], int(service["port"]), keepalive=30)
        self.client.loop_start()

    def _connect(self, client, userdata, flags, reason, properties):
        if reason == 0:
            client.subscribe(self.RESPONSE, qos=0)

    def _subscribed(self, client, userdata, mid, reasons, properties):
        if reasons and all(not reason.is_failure for reason in reasons):
            self.ready.set()

    def _message(self, client, userdata, message):
        if (not message.retain and message.topic == self.RESPONSE and
                message.payload.decode("utf-8", errors="replace") == self.correlation):
            self.received.set()

    def probe(self):
        with self.lock:
            if not self.ready.wait(1):
                return None
            self.received.clear()
            self.correlation = uuid.uuid4().hex
            started = time.monotonic()
            published = self.client.publish(self.REQUEST, self.correlation, qos=0, retain=False)
            result = ((time.monotonic() - started) * 1000
                      if published.rc == self.mqtt.MQTT_ERR_SUCCESS and self.received.wait(1) else None)
            self.correlation = None
            return result

    def close(self):
        self.client.disconnect()
        self.client.loop_stop()


class Local:
    def __init__(self, config, token=None):
        self.config = config
        self.api = JSONAPI("http://supervisor", token or os.environ.get("SUPERVISOR_TOKEN", ""), 1)
        self.previous_cpu = None
        self.mqtt = None

    def notify(self, title, message):
        domain, service = self.config.notification_service.split(".", 1)
        payload = replace_leaves(self.config.notification_payload, title, message)
        try:
            self.api.request(f"/core/api/services/{domain}/{service}", "POST", payload)
            return True
        except (OSError, ValueError, APIError):
            return False

    def sample(self, now, worker_pid=None):
        result = {"at": now.isoformat(), "cpu_percent": None, "available_mib": None,
                  "ha_ms": None, "mqtt_ms": None, "app_memory": proc_memory(os.getpid()),
                  "worker_memory": proc_memory(worker_pid) if worker_pid else None}
        try:
            counters = [int(value) for value in Path("/proc/stat").read_text().splitlines()[0].split()[1:]]
            result["cpu_percent"] = cpu_percent(self.previous_cpu, counters)
            self.previous_cpu = counters
            for line in Path("/proc/meminfo").read_text().splitlines():
                if line.startswith("MemAvailable:"):
                    result["available_mib"] = int(line.split()[1]) / 1024
        except (OSError, ValueError, IndexError):
            self.previous_cpu = None
        try:
            started = time.monotonic()
            response = self.api.request("/core/api/")
            if isinstance(response, dict) and response.get("message") == "API running.":
                result["ha_ms"] = (time.monotonic() - started) * 1000
        except (OSError, ValueError, APIError):
            pass
        try:
            if self.mqtt is None:
                service = self.api.request("/services/mqtt")
                if not isinstance(service, dict) or service.get("result") != "ok":
                    raise ValueError("mqtt_service_unavailable")
                self.mqtt = MQTTProbe(service["data"])
            result["mqtt_ms"] = self.mqtt.probe()
        except (OSError, ValueError, KeyError, APIError):
            pass
        return result


def proc_memory(pid):
    values = {}
    try:
        for line in Path(f"/proc/{pid}/status").read_text().splitlines():
            key, _, value = line.partition(":")
            if key in ("VmRSS", "VmPeak", "VmSize"):
                values[key + "_mib"] = round(int(value.split()[0]) / 1024, 2)
    except (OSError, ValueError):
        pass
    return values or None
