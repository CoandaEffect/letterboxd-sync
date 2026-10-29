# Green rollout task list

- [x] Read the supplied handoff and inspect the starting checkout.
- [x] Fetch current remote branches without changing the working tree.
- [x] Import the reviewed smoke script and match its handoff SHA-256.
- [x] Run all 12 existing offline scraper tests and check ARM64 wheel availability.
- [ ] Run the bounded smoke test on the actual Home Assistant Green.
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

No actual-Green smoke result exists yet. The HA connector's File editor
read-only proxy returns HTTP 420, Policy not fulfilled. Chrome browser control
times out. Native Dia control works, but HA's normal login requires user
authentication. No command-execution request has been submitted.

No App is built or installed; no notifications, MQTT probe, baseline, five-day
trial, watchdog, or reminder are active. The existing hosted schedule remains
unchanged. The Downloads handoff is source context; current observations
govern execution.

## Next gate

After normal HA sign-in, open File editor's Execute shell command UI and run
the commit-pinned, bounded smoke command supplied in the handoff. Inspect the
JSON, not only the exit code. Required evidence: aarch64 architecture, both
film/diary page checks passing, and ha_api_result equal to samples_ok.

A stdlib denial is inconclusive for curl_cffi and requires a bounded test of
the production client. A successful smoke is not proof of full-scrape memory
fit, publication, alerts, or long-term automation responsiveness. Full App work
remains behind this gate.

## Preflight sources

- https://pypi.org/project/letterboxdpy/6.6.1/
- https://pypi.org/project/curl-cffi/0.17.0/
- https://man7.org/linux/man-pages/man2/getrlimit.2.html
