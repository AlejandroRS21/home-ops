# Daemon Scheduler Specification

## Purpose

The `homeops daemon` — a persistent process that runs the pipeline (scrape → score → alert) on a configurable schedule with catch-up recovery, daily alert quotas, HITL bypass, and systemd supervision. Stdlib only.

## Requirements

### Requirement: Schedule Configuration

The system MUST parse an `alert_schedule` section from `user_profile.yml`:

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `mode` | enum(daily,interval) | `daily` | Execution mode |
| `daily_time` | string(HH:MM) | `09:00` | Wall-clock time in daily mode |
| `interval_hours` | float | — | Period in interval mode |
| `timezone` | IANA zone | `Europe/Madrid` | Schedule timezone |
| `max_alerts_per_day` | int | `5` | Daily alert cap |

#### Scenario: Defaults on missing section

- GIVEN no `alert_schedule` in `user_profile.yml`
- WHEN the daemon loads config
- THEN it MUST use mode=daily, daily_time=09:00, timezone=Europe/Madrid, max_alerts_per_day=5

#### Scenario: Bad timezone rejected

- GIVEN `alert_schedule.timezone` is `"Invalid/Zone"`
- WHEN the daemon validates config
- THEN it MUST exit with a clear error

### Requirement: Daemon Loop

The system SHOULD run pipeline on schedule per mode:

| Mode | Behavior |
|------|----------|
| `daily` | Run once at `daily_time` in configured `timezone` |
| `interval` | Run every `interval_hours` from daemon start |

#### Scenario: Daily mode runs at wall-clock time

- GIVEN mode=daily, daily_time=14:30
- WHEN the loop detects 14:30 in configured timezone
- THEN it MUST execute the pipeline

#### Scenario: Interval mode runs every N hours

- GIVEN mode=interval, interval_hours=6
- WHEN daemon starts at 08:00
- THEN it MUST run at 08:00, 14:00, 20:00, 02:00

#### Scenario: Overlapping run skipped

- GIVEN a pipeline run is in progress
- WHEN the next tick fires
- THEN the daemon MUST skip and log a warning

### Requirement: Catch-Up Recovery

On daemon start, if the last recorded run (from `scraping_runs` DuckDB table) is older than the expected window, or no run exists, the system MUST execute immediately.

#### Scenario: Missed window triggers immediate run

- GIVEN mode=daily, daily_time=09:00, last_run is 2026-06-17
- WHEN daemon starts at 2026-06-18 10:00
- THEN it MUST run the pipeline immediately

#### Scenario: No prior runs triggers immediate run

- GIVEN `scraping_runs` has no records
- WHEN the daemon starts
- THEN it MUST run the pipeline immediately

### Requirement: Daily Alert Limit

The system MUST track alerts per day in DuckDB `alerts_sent` table. When `max_alerts_per_day` is reached, remaining alerts MUST get status `queued` and SHOULD send next day.

#### Scenario: Quota reached queues overflow

- GIVEN max_alerts_per_day=5, 5 alerts already sent today
- WHEN the pipeline generates 2 more alerts
- THEN those 2 MUST be `queued` and skipped

#### Scenario: Queued alerts sent next day

- GIVEN 2 queued alerts from yesterday
- WHEN the pipeline runs today (new calendar day, quota available)
- THEN those 2 alerts SHOULD be sent

### Requirement: Human-in-the-Loop Bypass

The notifier MUST skip listings with status `pending_approval`. `homeops approve <id>` SHOULD set it to `approved`. Approved listings MUST be processed on the next pipeline run.

#### Scenario: Pending listings excluded

- GIVEN a listing has status `pending_approval`
- WHEN the alert step runs
- THEN that listing MUST NOT be sent

#### Scenario: Approved listing processed on next run

- GIVEN `homeops approve listing-42` sets status to `approved`
- WHEN the pipeline runs next
- THEN listing-42 MUST be alerted

### Requirement: Systemd Integration

The system MUST generate `systemd/homeops.service` with `Restart=on-failure`, `RestartSec=30`.

#### Scenario: Process failure triggers restart

- GIVEN the daemon exits with non-zero code
- WHEN systemd detects the failure
- THEN it MUST restart after 30 seconds

### Requirement: Zero External Dependencies

The daemon MUST use only stdlib (`asyncio`, `datetime`, `zoneinfo`, `time`). No new third-party packages.

#### Scenario: Dependency verification

- GIVEN the project dependency manifest
- WHEN checked for daemon imports
- THEN no new packages MUST be required
