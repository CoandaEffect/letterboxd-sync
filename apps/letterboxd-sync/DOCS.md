# Setup and operation

## Installation

Add this App repository in Home Assistant:

```
https://github.com/CoandaEffect/letterboxd-sync#codex/ha-green-app
```

Install **Letterboxd Sync** on a Home Assistant Green. The release image is
prebuilt and tested on native ARM64 in CI. Keep protection enabled. The App
needs the Home Assistant API and Supervisor-provided MQTT service discovery;
it has no inbound port, ingress, host networking, host PID access, or Docker API.

Provision a fine-grained GitHub personal access token restricted to this one
repository, with **Contents: read and write** and an expiry covering the soak.
Enter it directly in the App's password field. Never paste it in an issue,
chat, shell command, source file, or log. The scraper worker cannot read the
App options or inherit the coordinator's API credentials.

Leave `active` and `approve_first_publish` false while setup is incomplete.
The default repository is `CoandaEffect/letterboxd-sync`, export branch `main`,
and path `letterboxd-watched.csv`. The only supported schedule is `0 6 */2 * *`:
06:00 UTC on odd-numbered calendar dates, including consecutive odd dates at
some month boundaries. This is not a fixed 48-hour interval.

## Health probe and alerts

The dedicated Home Assistant automation must echo each MQTT request payload
from `letterboxd_sync/health/request` to `letterboxd_sync/health/response`, with
QoS 0 and retain false. It has no device actions. The App allows one outstanding
random probe and matches only its own response. No retained probe is accepted.

Phone alerts use `script.andrew_phone_notification`, passing the title and
message inside its `notification` object. A successful service response proves
Home Assistant accepted the request; it does not prove the phone displayed it.
Failed delivery remains visible in state and is retried at bounded intervals.

The independent **Green App health watchdog** reads only
`green-status/health.json` from GitHub every 15 minutes. It checks missing or
older-than-45-minute heartbeats, paused/failed states, and a successful export
more than six hours overdue. GitHub schedules and email delivery may be delayed.
The status branch contains a small allowlisted health record; it contains no
credentials, internal endpoints, resource samples, or household states.

Keep repository variable `GREEN_WATCHDOG_ENABLED` unset or false during setup.
Manual workflow dispatch remains available for `missing-heartbeat` and
`overdue-success` fixture checks; those deliberately fail without changing the
public health record. Verify the resulting GitHub failure email reaches the
Inbox, then set the variable to `true` when monitoring starts.

## Start the observation

With credentials, MQTT echo, and alert delivery ready, set `active: true`, save,
and start the App. It requires healthy probes and an accepted test notification;
the five-day clock starts on the next healthy minute. Its persisted timestamps
are authoritative. Configure start on boot once this state is confirmed.

The first four hours require 240 unique healthy minute slots with ten seconds
of timing tolerance. Missing or failed samples are not replaced with zeroes.
The baseline records coverage and real p95 response times. Scraping starts only
after a complete baseline. The first full candidate is held without publication
until its row/date coverage, worker memory fit, and host responsiveness are
reviewed. Then set `approve_first_publish: true` to release it. The coordinator
refetches the destination SHA and refuses a concurrent CSV change.

Keep the old hosted sync schedule until the first validated Green publication
and the phone/email alert checks pass. Then retire the hosted schedule to leave
one unattended publisher. A valid unchanged export advances freshness without
creating an unnecessary CSV commit.

## Request a fresh export

After the first candidate has been reviewed and successfully released, set
`sync_request_nonce` to a new nonsecret token and save the App options. Use up
to 64 letters, digits, underscores, or hyphens; a UUID is suitable. The next
healthy monitoring tick starts one full scrape through the normal validation,
destination-SHA check, and publication path. A heartbeat alone does not fulfill
this request. A successful run sets the next due time to the normal schedule.

The request waits while inactive, before the baseline and first publication
finish, during a protective pause, while a candidate awaits review, during an
existing worker, or during the existing attempt backoff. It never resumes a
pause or starts a second worker. Only the currently configured request waits;
changing the token replaces an unconsumed request. Accepted tokens are consumed
durably before any worker starts, including failed starts, and cannot be reused
after restart. Use a new token for another attempt. Clear the field to cancel an
unconsumed request.

Logs record `manual_sync_requested` and `manual_sync_accepted` without the token.
The App retains at most 128 consumed fingerprints, never evicts them, and then
refuses further manual requests with `manual_sync_request_limit_reached` in the
log. Scheduled syncs continue normally.

## Protection and recovery

Monitoring samples once per minute. Exports pause on:

- available host memory below 512 MiB;
- host CPU above 80% for five consecutive samples;
- HA response time above max(500 ms, three times baseline p95) for three samples;
- MQTT round-trip above max(250 ms, three times baseline p95) for three samples;
- three consecutive required-probe failures, worker errors, or unsafe recovery.

The worker is terminated as a process group. There is no overlapping export.
An interrupted worker after restart latches a pause instead of silently retrying.
Set a new `resume_nonce` value after investigating; healthy probes are required
to resume. Restarting or resuming does not reset the trial clock. If cleanup
cannot be verified, restart the App before resuming.

Daily summaries are scheduled for 18:00 America/Denver by default and include
baseline comparisons, resource peaks, outcomes, and missing samples. An incident
is deduplicated, and recovery is reported once after delivery acceptance.

## Evidence and rollback

Durable private state and raw minute samples live under `/data`. Run
`python -m green_app --status` inside the App for a safe status summary; it
includes the candidate summary, last sample, baseline coverage, and notification
delivery state. It never prints the repository token. Offline `--self-check`
does not start a trial or fetch Letterboxd.

The App also writes an allowlisted `status` JSON event to its normal HA logs
at startup, every 15 minutes, and when worker results or the first candidate
are ready. This includes measured worker peaks and baseline coverage, without
needing shell access. Unconfigured inactive startup samples local health and
logs status but makes no GitHub calls or notifications.

To stop this rollout, stop the App and disable start on boot. Disable the
watchdog repository variable, and remove only the dedicated health echo
automation and App if desired. Preserve `/data` for diagnosis before uninstalling.
Do not restore a full household backup for this isolated change. Removing the
App does not change the existing CSV or automatically restore the hosted cron.
