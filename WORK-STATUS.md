# Green rollout task list

Live acceptance state and receipts: local private ledger
`.rollout-evidence/deployment-ledger.md`. The tracked notes below are implementation
history; use the private ledger before making deployment changes.

- [x] Read the supplied handoff and inspect the starting checkout.
- [x] Fetch current remote branches without changing the working tree.
- [x] Import the reviewed smoke script and match its handoff SHA-256.
- [x] Run all 12 existing offline scraper tests and check ARM64 wheel availability.
- [x] Run the bounded stdlib smoke test on the actual Home Assistant Green.
- [x] Resolve the inconclusive stdlib 403 with a bounded production-client test.
- [x] Verify the production client on ARM64 under the approved limits.
- [x] Implement and test the App, protection, publication, and watchdog.
- [x] Install the prebuilt App inactive with protection enabled.
- [ ] Configure the dedicated repository credential securely.
- [ ] Exercise phone/email alerts and start the four-hour idle baseline.
- [ ] Validate and publish the first full Green export.
- [ ] Retire the hosted schedule after acceptance gates pass.
- [ ] Configure five-day observation, daily reports, and review reminder.

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

Next: complete secure credential entry, check probes and phone acceptance,
then start the observation and record its actual timestamps.
The user explicitly authorized continuing through that start without another
rollout approval. Full-scrape fit is checked after the four-hour idle baseline;
first CSV publication remains gated on reviewing that candidate. No baseline,
five-day clock, day-five reminder, or unattended Green publisher is active yet.

The dedicated non-actuating MQTT echo automation is installed and read back:
`automation.letterboxd_sync_health_echo`, unique ID `1793312579423`.
It echoes only between `letterboxd_sync/health/request` and
`letterboxd_sync/health/response`, QoS 0, retain false. It controls no devices.

## Preflight sources

- https://pypi.org/project/letterboxdpy/6.6.1/
- https://pypi.org/project/curl-cffi/0.17.0/
- https://man7.org/linux/man-pages/man2/getrlimit.2.html
