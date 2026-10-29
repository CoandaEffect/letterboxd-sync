# Green rollout task list

- [x] Read the supplied handoff and inspect the starting checkout.
- [x] Fetch current remote branches without changing the working tree.
- [x] Import the reviewed smoke script and match its handoff SHA-256.
- [x] Run all 12 existing offline scraper tests and check ARM64 wheel availability.
- [x] Run the bounded stdlib smoke test on the actual Home Assistant Green.
- [ ] Resolve the inconclusive stdlib 403 with a bounded production-client test.
- [ ] Verify the production client on ARM64 under the approved limits.
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

The diagnostic uses one selected production browser profile per start, no
automatic retry. Full App work and the soak remain behind a passing gate.

## Next gate

Run the bounded production-client diagnostic on the Green. Inspect its JSON,
not only the exit code. Required evidence: aarch64 architecture, both
film/diary page checks passing, and ha_api_result equal to samples_ok.

A stdlib denial is inconclusive for curl_cffi and requires a bounded test of
the production client. A successful smoke is not proof of full-scrape memory
fit, publication, alerts, or long-term automation responsiveness. Full App work
remains behind this gate.

## Preflight sources

- https://pypi.org/project/letterboxdpy/6.6.1/
- https://pypi.org/project/curl-cffi/0.17.0/
- https://man7.org/linux/man-pages/man2/getrlimit.2.html
