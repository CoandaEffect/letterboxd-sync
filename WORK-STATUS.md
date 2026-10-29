# Green rollout task list

- [x] Read the supplied handoff and inspect the starting checkout.
- [x] Fetch current remote branches without changing the working tree.
- [x] Import the reviewed smoke script and match its handoff SHA-256.
- [x] Run all 12 existing offline scraper tests and check ARM64 wheel availability.
- [x] Run the bounded stdlib smoke test on the actual Home Assistant Green.
- [x] Resolve the inconclusive stdlib 403 with a bounded production-client test.
- [x] Verify the production client on ARM64 under the approved limits.
- [ ] Implement and test the App, protection, publication, and watchdog.
- [ ] Install and configure using a dedicated repository credential.
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

No full App is built or installed; no notifications, MQTT probe, baseline,
five-day trial, watchdog, or reminder are active. Only the minimal prebuilt
ARM64 production-client diagnostic is installed to resolve the 403 result.
The existing hosted schedule remains
unchanged. The Downloads handoff is source context; current observations
govern execution.

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

Build and test the complete App on native ARM64, verify its public image,
and install it inactive. Independent runtime and package review is clear.
The current suite runs 102 tests locally: 97 pass and five Linux/native checks
are reserved for the image build. The release guard requires an authenticated
registry 404 before permitting a new version, and stops on other lookup errors.
The App remains inactive until its dedicated repository credential and alert
checks are ready. Full-scrape fit is checked after the four-hour idle baseline;
first CSV publication remains gated on reviewing that candidate.

The dedicated non-actuating MQTT echo automation is installed and read back:
`automation.letterboxd_sync_health_echo`, unique ID `1793312579423`.
It echoes only between `letterboxd_sync/health/request` and
`letterboxd_sync/health/response`, QoS 0, retain false. It controls no devices.

## Preflight sources

- https://pypi.org/project/letterboxdpy/6.6.1/
- https://pypi.org/project/curl-cffi/0.17.0/
- https://man7.org/linux/man-pages/man2/getrlimit.2.html
