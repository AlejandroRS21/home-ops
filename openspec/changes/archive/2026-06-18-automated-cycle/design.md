# Design: automated-cycle — Daemon Scheduler

## Technical Approach

Add `homeops daemon` — a persistent CLI process that runs the pipeline on a configurable schedule. Schedule config from `user_profile.yml.alert_schedule`. Stdlib-only: `time.sleep(60)` loop, `zoneinfo` for timezone math, pure functions for schedule computation. Records run history in `scraping_runs`, tracks daily alert quota in `daily_alert_log`. Systemd unit for supervision. Strict TDD — `_run_daemon_cycle` accepts an injectable run function so tests never sleep.

## Architecture Decisions

| Decision | Options | Tradeoff | Choice |
|----------|---------|----------|--------|
| **Loop type** | `asyncio.sleep(60)` vs `time.sleep(60)` | Async adds complexity; `_run_scan` is sync. Ergonomic | `time.sleep(60)` blocking loop |
| **Schedule computation** | Inline in daemon vs pure function | Pure fn is testable with no I/O | Pure fn `_next_run_time(schedule, last_run)` |
| **Daily quota gating** | Inside `_run_scan` vs daemon wrapper | Inside `_run_scan` means `scan` command also respects quota (correct) | Modify alert loop in `_run_scan` to check `daily_alert_log` |
| **Queued alerts** | Separate table vs status field in `daily_alert_log` | Simpler: single table with `status` enum | `daily_alert_log.status` in (`sent`, `queued`) |
| **Catch-up trigger** | Always run on start vs conditional | Conditional avoids unnecessary runs | Only if no `scraping_runs` record or last_run < expected window |
| **Config field naming** | Keep `time` vs rename to `daily_time` | Spec uses `daily_time`; existing yml has `time`. Forward-compat | Accept both with `daily_time` preferred, `time` fallback |

## Data Flow

```
homeops daemon
  │
  ├─ 1. Load config → get ScheduleConfig
  │
  ├─ 2. Catch-up check
  │     └─ query scraping_runs → last_run
  │         ├─ no record → run NOW
  │         ├─ last_run < expected → run NOW
  │         └─ last_run recent → wait until next window
  │
  └─ [loop] while True:
       ├─ time.sleep(60)
       ├─ if schedule says "run now":
       │    ├─ _run_daemon_cycle(config)
       │    │    ├─ _run_scan(config_path, quota_remaining)
       │    │    │    ├─ scrape → dedup → score → HITL gate
       │    │    │    └─ alert: check daily_alert_log COUNT
       │    │    │         ├─ < max → alerter.send_alert(), INSERT sent
       │    │    │         └─ ≥ max → INSERT queued (status='queued')
       │    │    └─ INSERT scraping_runs (started_at, finished_at, ...)
       │    └─ process queued alerts if new day + quota available
       └─ sleep
```

## File Changes

| File | Action | Description |
|------|--------|-------------|
| `src/home_ops/models/schema.py` | Modify | Add `ScheduleConfig` model, wire `Config.alert_schedule: ScheduleConfig \| None` |
| `src/home_ops/config/loader.py` | Modify | Map `alert_schedule` YAML → `ScheduleConfig`; support `time`/`daily_time` aliasing |
| `src/home_ops/models/data_storage.py` | Modify | Add `scraping_runs` + `daily_alert_log` table creation; add `_get_daily_alert_count()`, `log_alert()` helpers |
| `src/home_ops/cli/app.py` | Modify | Add `daemon` Typer command; `_run_daemon_cycle(config, run_fn)`; `_next_run_time(schedule, last_run)`; catch-up logic |
| `systemd/homeops.service` | Create | Systemd unit with `Restart=on-failure`, `RestartSec=30`, `ExecStart` pointing to daemon |
| `user_profile.yml` | Modify | Update `alert_schedule` to use `daily_time` + `interval_hours` fields |
| `tests/test_schema.py` | Modify | Tests for `ScheduleConfig` validation (defaults, bad timezone) |
| `tests/test_config_loader.py` | Modify | Tests for `alert_schedule` mapping (both `time` and `daily_time`) |
| `tests/test_data_storage.py` | Modify | Tests for new tables, `_get_daily_alert_count()`, `log_alert()` |
| `tests/test_cli.py` | Modify | Tests for `daemon` command (mocked loop), `_next_run_time`, catch-up |

## Interfaces / Contracts

```python
# src/home_ops/models/schema.py

class ScheduleConfig(BaseModel):
    mode: str = "daily"                     # "daily" | "interval"
    daily_time: str = "09:00"               # HH:MM in daily mode
    interval_hours: float = 24.0            # period in interval mode
    timezone: str = "Europe/Madrid"         # IANA timezone name
    max_alerts_per_day: int = 5

class Config(BaseModel):
    ...
    alert_schedule: ScheduleConfig | None = None

# Pure function for schedule computation
def _next_run_time(schedule: ScheduleConfig, last_run: datetime | None) -> datetime | None:
    """Return next datetime the pipeline should run, or None if not yet due."""

# Daemon cycle — injectable run_fn for testing
def _run_daemon_cycle(
    config: Config,
    run_fn: Callable = _run_scan,
) -> None:
    """One daemon cycle: catch-up check → optional run → loop sleep."""
```

## Testing Strategy

| Layer | What to Test | Approach |
|-------|-------------|----------|
| Unit | `ScheduleConfig` defaults + validation | Bad timezone → `ZoneInfoNotFoundError`; empty → defaults |
| Unit | `_next_run_time(schedule, last_run)` | daily: run at 09:00; interval: every N h; no last_run → immediate |
| Unit | `_get_daily_alert_count(conn)` | Insert 3 today, 2 yesterday → returns 3 |
| Unit | `_run_daemon_cycle` with mock run_fn | Verify run_fn called when schedule says "now" |
| CLI | `homeops daemon --help` | Exit 0, shows daemon description |
| CLI | `homeops daemon` (mocked sleep) | Inject mock run_fn, verify loop fires once then exits |
| Integration | `init_db()` includes new tables | DuckDB in-memory, verify `scraping_runs` + `daily_alert_log` exist |
| Integration | Daily quota in `_run_scan` | Mock `_get_daily_alert_count` returning ≥max → verify status='queued' |

## Migration / Rollout

No data migration needed — new tables are created alongside existing ones via `init_db()` (idempotent). Rollback: stop daemon (`systemctl stop homeops`), revert code, delete `homeops.service`. Tables remain but don't affect `scan` command.

## Open Questions

None.
