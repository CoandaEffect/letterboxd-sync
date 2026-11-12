# Letterboxd Sync for Home Assistant Green

A prebuilt ARM64 App runs the existing watched-film exporter while measuring
the Green's spare memory, CPU use, Home Assistant response time, and MQTT
round-trip time. It starts inactive. It installs no packages on the Green.

The production-client diagnostic passed on an actual Green on October 29,
2026. That establishes the network/client gate; a full export and the five-day
observation have separate acceptance checks. See [WORK-STATUS.md](../../WORK-STATUS.md)
for the original rollout history and [DOCS.md](DOCS.md) for current setup and operation.

The coordinator holds the repository credential. A separate low-priority
worker receives only CSV input and output paths, runs as an unprivileged UID,
and has a 256 MiB address-space limit, a 120-second CPU limit, and an independent
600-second wall deadline followed by bounded process-group termination.
The CPU limit measures consumed CPU time; it is not a host CPU percentage quota.

The first four hours collect an idle baseline before any full scrape. A first
candidate export is validated and held for review. Later successful runs use
the same odd-calendar-day schedule at 06:00 UTC as the hosted workflow. A valid
unchanged export counts as a successful run.

The App continues running after the five-day observation period, which includes
the idle baseline. When active, it clears pauses for missing monitoring or
worker interruptions with verified cleanup after three consecutive healthy
minute samples. Latched safety pauses require investigation and an explicit
healthy resume. Monitoring and alert retries continue during a pause.
