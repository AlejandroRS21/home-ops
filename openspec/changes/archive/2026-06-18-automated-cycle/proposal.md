# Proposal: automated-cycle

## Intent

Add `homeops daemon` — a persistent CLI process that runs the pipeline (scrape → score → alert) on a configurable schedule. Currently the pipeline is manual-only; this change makes it autonomous with systemd supervision, catch-up recovery, daily alert limits, and HITL re-queuing.

## Scope

### In Scope
- `homeops daemon` command (Typer) with stdlib asyncio sleep loop
- Schedule config: `alert_schedule` section in `user_profile.yml` goes live
- Systemd unit file (`homeops.service`) with `Restart=on-failure`
- Catch-up recovery: run immediately if daemon start missed a scheduled window
- Daily alert cap: `max_alerts_per_day` tracked in DuckDB, excess listings get queued
- HITL queuing: listings awaiting approval visible in `homeops status`, auto-processed on approve

### Out of Scope
- Multi-instance/HA daemon coordination
- Web dashboard or push notifications for pending approvals
- Config hot-reload (requires restart)
- Alert retry/backoff beyond systemd restart

## Capabilities

### New Capabilities
- `daemon-scheduler`: scheduled pipeline execution with interval/daily modes, timezone-aware, catch-up on start

### Modified Capabilities
- None — this adds a new daemon command and doesn't change scrape/score/alert behavior

## Approach

New `homeops daemon` command in `cli/app.py`. Stdlib `asyncio` + `zoneinfo` (Python 3.11+, zero new deps). Sleep loop: wait → check schedule → run `_run_scan()` → log result.

Two new DuckDB tables:
- `scraping_runs` — tracks `last_run` timestamp for catch-up detection
- `alerts_sent` — per-day alert count for `max_alerts_per_day` gating

Schedule config (`alert_schedule`) loaded from `user_profile.yml` into `Config` model. Systemd unit generated at `systemd/homeops.service`.

## Affected Areas

| Area | Impact | Description |
|------|--------|-------------|
| `src/home_ops/cli/app.py` | Modified | Add `daemon` Typer command |
| `src/home_ops/models/schema.py` | Modified | Add `alert_schedule` fields to `Config` |
| `src/home_ops/models/data_storage.py` | Modified | New tables + daily alert query |
| `src/home_ops/config/loader.py` | Modified | Parse `alert_schedule` section |
| `systemd/homeops.service` | New | Systemd unit file |
| `user_profile.yml` | Modified | New `mode`/`daily_time`/`interval_hours` keys |

## Risks

| Risk | Likelihood | Mitigation |
|------|------------|------------|
| Daemon exits silently | Low | systemd `Restart=on-failure`, journald logs |
| Cron overlap (interval < scan time) | Low | Skip if previous run still in progress |
| DST/timezone edge cases | Low | zoneinfo handles DST; daily mode checks wall clock |

## Rollback Plan

Stop daemon: `systemctl stop homeops && systemctl disable homeops`. Remove `homeops daemon` code. Keep new tables but back-compat — `_run_scan()` unchanged.

## Dependencies

- None (stdlib only: `asyncio`, `zoneinfo`, `datetime`, `time`)
- Python 3.11+ required (already in pyproject.toml)

## Success Criteria

- [ ] `homeops daemon --dry-run` prints next scheduled execution and exits
- [ ] Daemon executes pipeline at correct schedule intervals
- [ ] Catch-up: stop daemon, run manually, restart → daemon catches missed window
- [ ] Daily alert cap: pipeline respects max_alerts_per_day, queues overflow
- [ ] Systemd unit: service starts on boot, restarts on failure
- [ ] Existing tests pass, no regressions
