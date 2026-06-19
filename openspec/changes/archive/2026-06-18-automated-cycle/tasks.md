# Tasks: automated-cycle — Daemon Scheduler

## Review Workload Forecast

| Field | Value |
|-------|-------|
| Estimated changed lines | 300–400 |
| 400-line budget risk | Low |
| Chained PRs recommended | No |
| Suggested split | Single PR |
| Delivery strategy | ask-always |
| Chain strategy | pending |

Decision needed before apply: No
Chained PRs recommended: No
Chain strategy: pending
400-line budget risk: Low

## Phase 1: Foundation (Model + Config + DB)

- [x] 1.1 Schema: Add `ScheduleConfig` model with mode/daily_time/interval_hours/timezone/max_alerts_per_day + validation in `src/home_ops/models/schema.py`
- [x] 1.2 Schema: Wire `ScheduleConfig` into `Config.alert_schedule` field
- [x] 1.3 Loader: Map `alert_schedule` YAML section to `ScheduleConfig` with defaults in `src/home_ops/config/loader.py`
- [x] 1.4 Data storage: Add `scraping_runs` table (id, started_at, finished_at, listings_found, listings_new, alerts_sent, status) in `src/home_ops/models/data_storage.py`
- [x] 1.5 Data storage: Add `daily_alert_log` table (id, listing_hash, sent_at, status) with sent/queued enum
- [x] 1.6 Systemd: Create `systemd/homeops.service` with `Restart=on-failure`, `RestartSec=30`

## Phase 2: Core Logic (Daemon Command)

- [x] 2.1 Pure fn: `_next_run_time(schedule, last_run)` — compute next scheduled datetime from config (daily/interval modes, zoneinfo-aware)
- [x] 2.2 Pure fn: `_get_daily_alert_count(conn, date)` — query today's sent count from `daily_alert_log`
- [x] 2.3 Daemon loop: `_run_daemon_cycle(config, run_fn)` — schedule check, quota gate, skip on overlapping run, injectable run_fn
- [x] 2.4 Daemon: Wire `homeops daemon` Typer command with catch-up start logic and `time.sleep(60)` loop
- [x] 2.5 Catch-up: On daemon start, query `scraping_runs` for last record; run immediately if missing or outside expected window
- [x] 2.6 Quota gate: `_get_daily_alert_count` queries daily_alert_log for today's sent count (status='sent'), available for quota enforcement in the alert path

## Phase 3: Tests

- [x] 3.1 Test: `ScheduleConfig` defaults and validation (bad timezone, invalid mode, missing fields)
- [x] 3.2 Test: `alert_schedule` YAML mapping in loader (full section, partial, missing — defaults applied)
- [x] 3.3 Test: `scraping_runs` and `daily_alert_log` table creation via `init_db` (in-memory DuckDB)
- [x] 3.4 Test: `_next_run_time` pure fn — daily mode (wall clock), interval mode (N-hour period), DST edge cases
- [x] 3.5 Test: `_run_daemon_cycle` with mock run_fn — runs on schedule, skips overlapping, catch-up immediate run
- [x] 3.6 Test: Daemon CLI — `--help` output, `--dry-run` mode, mocked loop injection
