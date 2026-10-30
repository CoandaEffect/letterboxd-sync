# Native Green watcher

These templates run inside Home Assistant. No Mac polling or LLM is involved.
They only classify status; they never approve a candidate, publish an export,
resume the App, reset its clock, or restart anything.

Create Scrape through its config flow. Resource:
`https://raw.githubusercontent.com/CoandaEffect/letterboxd-sync/green-status/health.json`.
Use selector `body`, index `0`, and `scrape-value.jinja` as its value template.
This selector depends on the deployed Scrape parser wrapping plain JSON in an
HTML body; verify the resulting state before enabling notifications. Default
600-second polling is sufficient for a heartbeat published every 15 minutes.

The packed state is JSON with short keys to stay under HA's 255-character limit:
`v` schema, `h` heartbeat epoch, `p` phase, `m` monitoring, `b` baseline complete,
`a` last attempt epoch, `s` last success epoch, `d` next due epoch, `i` incident code.
Missing timestamps become zero. Invalid input becomes `{}`.

Create a Template sensor helper with `status-state.jinja` as its state template.
The default references are `sensor.letterboxd_green_health` and
`automation.letterboxd_sync_health_echo`; substitute the actual registered IDs.
The `now()` dependency reevaluates stale checks each minute. The echo automation
must be dedicated to the App's once-per-minute health probes; manually triggering
it can mask App silence. Five minutes without an echo is `local_silence`.
Public heartbeat age over 45 minutes is `heartbeat_stale`; over five minutes in
the future is invalid. A held first candidate is inferred from baseline complete,
idle phase, a previous attempt, no successful sync, and no incident. The public
schema has no explicit candidate flag, so report this as requiring review, never
as proof that publication is safe.

Create a second Template sensor helper named `Letterboxd Green watcher notice`
with `notice-state.jinja`. It reads the status sensor, the persistent
`input_text.letterboxd_green_watcher_last_notice` helper (max 255, no initial
value), and `input_boolean.letterboxd_green_first_success_notified` (no initial
value). Both helpers restore their previous values after HA starts. The notice
sensor returns a desired notice key or `quiet` when already delivered. Unknown
success-latch state never qualifies as an off latch.

`automation.json` is the native config API payload. It assumes the existing
`script.andrew_phone_notification` accepts a `notification` object. The automation
also updates a single persistent notification in HA. It runs in `single` mode,
with state, HA-start, and five-minute retry triggers. Validate the exact config
against the deployed HA API before enabling it.

Notification behavior:

- Check on classifier changes, HA start, and a periodic native time trigger.
  A time trigger catches a condition already present when the automation loads.
- Alert on review required, failed, paused, incident, invalid health, stale
  heartbeat, local silence, and inactive monitoring. Ordinary baseline/syncing/idle
  stays quiet. The issue key is a status class; a different incident code within
  a continuous `incident` state does not create another notice.
- Save the delivered notice key only after the phone and persistent-notification
  actions succeed. Failure leaves it eligible for the five-minute retry. An
  unavailable notice helper blocks the action. HA start has no grace delay, so
  missing local echoes can produce one startup silence notice.
- After an issue, a healthy baseline/idle/syncing/success state emits one
  `recovery` notice and stores that key. A later issue alerts again. Candidate
  review alone does not qualify as an issue recovery.
- Use a separate persistent `input_boolean` for first-success delivery. On
  `success_seen` and latch off, first success takes priority over recovery. Notify
  once, then turn the latch on. Future syncs must not re-arm it. Reset this latch
  only for an explicitly new trial. After delivery, save the snapshotted notice
  key before checking that saved value to set the latch. A classification change
  during delivery cannot change which notice the latch records.
- This is at-least-once delivery: a crash between sending and saving the latch
  may duplicate a notification. Saving before sending could lose one instead.
- A native watcher on Green cannot send while Green/Core/network is down.
  `heartbeat_stale` also covers GitHub publication/fetch trouble, not just App
  death. Distinguish these limitations from App failure in notification text.

Rollback: disable and remove the watcher alerts automation first. Remove its
notice/status template helpers, last-notice text helper, first-success boolean,
and dedicated Scrape resource through their config APIs. Keep the existing MQTT
echo and Letterboxd App; this watcher does not own them. Removing the watcher
does not approve a candidate or alter the trial clock.

Offline rendered-template regression check:

```sh
uv pip install --python .venv/bin/python -r requirements-test.txt
.venv/bin/python -m unittest discover -s tests -p test_native_green_watcher.py -v
```

References: [Scrape](https://www.home-assistant.io/integrations/scrape/),
[Template](https://www.home-assistant.io/integrations/template/),
[input_text restore behavior](https://www.home-assistant.io/integrations/input_text/),
[input_boolean restore behavior](https://www.home-assistant.io/integrations/input_boolean/).
