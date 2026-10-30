# Green rollout task list

- [x] Read the supplied handoff and inspect the starting checkout.
- [x] Fetch current remote branches without changing the working tree.
- [x] Import the reviewed smoke script and match its handoff SHA-256.
- [x] Run all 12 existing offline scraper tests and check ARM64 wheel availability.
- [x] Run the bounded stdlib smoke test on the actual Home Assistant Green.
- [x] Resolve the inconclusive stdlib 403 with a bounded production-client test.
- [x] Verify the production client on ARM64 under the approved limits.
- [x] Implement and test the App, protection, publication, and watchdog.
- [x] Install the prebuilt App inactive with protection enabled.
- [x] Configure the dedicated repository credential securely.
- [x] Exercise phone/email alerts and start the four-hour idle baseline.
- [ ] Validate and publish the first full Green export.
- [ ] Retire the hosted schedule after acceptance gates pass.
- [x] Configure five-day observation, daily reports, and review reminder.

## Active observation

The actual Green started its observation on **2026-10-29 at
23:33:58.733682 UTC (5:33:58 p.m. MDT)**, after healthy probes, a successful
repository read, and HA acceptance of the phone test. The four-hour idle
baseline ends **2026-10-30 at 03:33:58 UTC (October 29, 9:33:58 p.m. MDT)**.
Five elapsed days ends **2026-11-03 at 23:33:58 UTC (4:33:58 p.m. MST)**;
the local hour changes because daylight saving time ends during the soak.

App `10132b86_letterboxd_sync` 0.1.1 is active and protected, with start on
boot enabled. Supervisor watchdog and automatic updates remain off.
The first full scrape waits for complete baseline coverage, and its candidate
is held for review with `approve_first_publish=false`. No full Green export
has been accepted yet. The existing hosted automatic triggers remain until
the first validated Green success, including a valid unchanged result.

The independent GitHub watchdog is enabled by
`GREEN_WATCHDOG_ENABLED=true`; its normal check passed in run 40973930895.
The first automatic watchdog run remains unobserved as of 00:38 UTC. Its
workflow is active on the default branch and the repository is enabled.
Checks are being offset to minutes 7, 22, 37, and 52, preserving 15-minute
spacing while avoiding the top-of-hour load described in
[GitHub's schedule documentation](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#schedule).
Automatic scheduling remains an acceptance observation; a successful manual
run is not proof that the cron trigger fired.
The controlled failure from run 40966542158 reached the Inbox with its exact
run link. Phone test trace `99947c03452673c57bfd5aeae7afc8e4` finished successfully
at 23:30:47 UTC, including the mobile notification action. This proves HA
service acceptance, not user-observed phone display. The App then accepted its
own preflight at 23:32:58 UTC and started the clock on the next healthy minute.

A recurring Codex follow-up checks baseline/candidate acceptance every 15
minutes and stays quiet when unchanged. It is instructed to review the first
candidate, verify the first successful export, then retire the hosted automatic
triggers while preserving manual recovery. A separate one-time review reminder
is scheduled for **November 3 at 4:34 p.m. America/Denver**. A healthy App keeps
running after day five. The review report target is
`reports/green-soak-2026-11-03.md`.
Codex follow-ups require this Mac and the desktop app to remain available;
the Green's own monitoring and GitHub watchdog run independently.

At 23:39:58 UTC, the first seven baseline slots were valid, with no incident,
no notification-delivery failure, and no worker running. The native UI showed
version 0.1.1 running, start on boot enabled, and watchdog/auto-update off;
the lead viewed both Info and Log captures. Remaining baseline slots are
future observations, not reported as failed measurements.

The dedicated repository credential was backed up to a private 1Password item
and exact readback verified. It was transferred into this App through the
existing configured MCP connection without secret-valued arguments or output.
The originally supplied Downloads file was no longer present before that
transfer; no extra plaintext copy was created. The reusable `onepassword-cli`
skill is installed and validated with 11 passing helper tests. Private object
IDs, configuration receipts, and rollback details stay in `.rollout-evidence`.

## Current verified state

2026-10-29: Started from main at 9c715d6, fetched remote main
0187627575bf8d816535b0b7c4c6647c11659dee, and created codex/ha-green-app.
Imported published smoke commit ee5b89c as 90b5f1c. The script SHA-256 is
8a5a889f8742acd60f816942a456aab04fa280c256bd3f22f952947b50148487,
matching the supplied archive manifest. Both handoff copies also match.

All 12 offline tests passed with an isolated uv CPython 3.12.12 environment.
Binary-only dependency resolution passed for Python 3.12 on ARM64 glibc and
musl targets. This establishes package availability, not target execution or
compliance with the approved 256 MiB worker address-space limit.

The bounded stdlib smoke ran once on the actual Green, after explicit
action-time confirmation. Architecture was aarch64. Both expected pages
returned HTTP 403; network_result was inconclusive. All three HA API probes
passed. File editor reported return code 1. Full measurements and transcription
provenance are retained in the ignored .rollout-evidence directory.

The authenticated native Home Assistant app resolved the browser sign-in
blocker. Per user preference, native Home Assistant is the default UI route
and Terra 6.1 owns all computer use. No further login or manual command is
needed. The user authorized continuing through the five-day soak start if the
network gate passes.

At the original handoff, no full App or monitoring was active. The staged
diagnostic resolved the network/client gate before full implementation.
The Downloads handoff is source context; current observations govern execution.

The bounded client diagnostic passed all 28 tests on native ARM64 in CI,
including guarded native loading and real libcurl loopback fixtures. The
offline startup peaked at 61.1 MiB virtual memory. Image 0.1.0 was verified
publicly pullable, with matching source/version digest, then installed on the
Green. Its boot is manual, watchdog/auto-update off, protection on.

The first exact-client Green start hit the four-second CPU cap during native
imports, before any HA probes or external page fetches. It exited with the
deployment gate blocked; this is not evidence of a production-client network
denial. No limits were increased. The 0.1.1 image precompiles Python bytecode
in CI and probes HA before native loading, then retests with unchanged limits.
It has 31 local tests: 28 passed, with three image/native checks reserved for
CI. Publication remains gated on all checks passing on native ARM64.

Version 0.1.1 passed all 31 native ARM64 checks and was installed. The next
Green run fit the unchanged limits and received HTTP 200 on both pages, with
all HA probes healthy. The diagnostic incorrectly rejected normal Cloudflare
JavaScript-detection scripts before parsing content. Version 0.1.2 corrects
that false positive using Cloudflare's documented challenge header plus an
interstitial-title check, and still requires real film/diary rows. It has 33
local tests, with three image/native checks reserved for CI.

Version 0.1.2 passed all 33 native ARM64 checks. Its fresh actual-Green run
passed: 72 films and 50 diary entries parsed from the expected page-two URLs,
both HTTP 200; all three HA API probes healthy; exit code 0. Peak virtual
memory was 59.9 MiB, peak resident memory 44.1 MiB, CPU 3.383 seconds, wall
time 3.92 seconds. The same limits were retained. The diagnostic is stopped,
manual boot, watchdog off. Terra verified the native UI and the lead viewed
the latest result screenshot. Full App implementation is now authorized to
proceed. No four-hour baseline or five-day soak has started yet.

The diagnostic uses one selected production browser profile per start, no
automatic retry. Full App work and the soak remain behind a passing gate.

## Next gate

Version 0.1.0 passed all 102 tests on native ARM64, including actual resource
enforcement and packaged native imports. Build 40966271593 published image
`ghcr.io/coandaeffect/letterboxd-sync-green`, verified anonymously pullable with
digest `sha256:3242b20c1d82612822b3be1c206e3c208819cc801ad3ab359daf8d53905bdea6`
and source `0708b01242993172c8d846ea7ea8fe70bc33ac0e`.
PR 10 merged as `2ce10fcb5196816ff7be05ddbf584e43999f843d`.

Full App `10132b86_letterboxd_sync` 0.1.1 is installed and running inactive:
first-publication approval false, no credential at verification, manual boot,
watchdog and auto-update off, protection enabled, no host network/PID/Docker
access. Terra inspected the native Info and Configuration views; the lead viewed
both captures before the version update. All 109 tests pass on native ARM64
in build 40967156167. Image digest is
`sha256:a78c72ae51a6e57fffcab4716157d135e99aaabc2f215ca8db888c3f3b2be6cc`,
source `efb51b52e8a242dd3bf45c26536a6ccd45cdfec6`, verified publicly pullable.
Inactive startup makes no GitHub calls or notifications. Nested operational
status fields are allowlisted; credentials and raw state are excluded.

Actual Green startup at 22:38:04 UTC: HA 39.9 ms, MQTT round-trip 13.2 ms,
1748 MiB available host memory, App RSS 24.1 MiB. CPU warmup is explicitly
missing until a second sample; it is not reported as zero. No incident, worker,
scheduler, baseline, or trial clock is active. A secure credential-entry handoff
is pending with the user; leave the native Configuration view untouched while
they enter it. Never inspect or print the token.

Controlled missing-heartbeat fixture run 40966542158 failed as designed.
Its exact run link arrived in Gmail with the Inbox label at 22:32 UTC.
No email, filter, notification preference, or public health record was modified.
Scheduled watchdog checks remain disabled until the repository activation flag
is enabled. Phone acceptance and the dedicated GitHub credential are pending.

These setup gates subsequently passed; the active observation above is the
current state. The next gate is the first full candidate after the idle baseline.
Review its validation summary, worker peaks, and host responsiveness before
enabling first publication. The user explicitly authorized continuing through
the soak start; the accepted implementation plan includes the staged acceptance
and removal of duplicate unattended hosted publishing after Green success.

The dedicated non-actuating MQTT echo automation is installed and read back:
`automation.letterboxd_sync_health_echo`, unique ID `1793312579423`.
It echoes only between `letterboxd_sync/health/request` and
`letterboxd_sync/health/response`, QoS 0, retain false. It controls no devices.

## Preflight sources

- https://pypi.org/project/letterboxdpy/6.6.1/
- https://pypi.org/project/curl-cffi/0.17.0/
- https://man7.org/linux/man-pages/man2/getrlimit.2.html
