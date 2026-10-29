# Bounded production-client smoke test

The diagnostic runs once when started. Keep start-on-boot, watchdog, and
automatic updates disabled. It needs no GitHub credential or exposed port.
Dependencies are included in the CI-built image; do not build an image or
install dependencies on the Green.

The profile option selects one production browser fingerprint: chrome
(default), safari, firefox, or safari_ios. Each start uses only that selection;
there is no automatic profile rotation or retry.

The Home Assistant Core API permission is used only for local responsiveness
probes. The diagnostic never sends that credential to Letterboxd. It does not
control devices or change HA configuration.

After the App stops, read its log. Passing requires ARM64 execution, successful
HTTP responses with recognizable data on both page-two URLs, and successful
HA API probes. Read the JSON and the exit status together. A challenge page
with HTTP 200 is a failure. Missing health measurements do not count as zero
latency or as a deployment pass.

The 256 MiB address-space limit includes native libraries and parser memory.
An allocation failure is a failed diagnostic; do not silently increase the
limit. Low available host memory or an unresponsive HA API stops page fetching.

Passing this test establishes bounded client/network feasibility. It does not
establish full-scrape resource use, export integrity, publishing, alert
delivery, four-hour baseline coverage, or five-day reliability. Those gates
still precede unattended syncing.

To roll back this diagnostic, stop and uninstall only this App. Preserve its
safe log evidence first. Do not restart Core, MQTT, Matter, Thread, or other
Apps as an automatic recovery step.
