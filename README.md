# Home-Ops

> Agentic pipeline that scrapes Idealista, scores every listing across 5 configurable dimensions, and alerts you on Telegram before anyone else sees it.

<!-- TODO: Insert screenshot of a Telegram alert with score -->
<!-- Example: ![Telegram alert showing listing score 85/100](./docs/screenshot-alert.png) -->

**184 tests — passing.** [MIT license](./LICENSE).

---

## Quick start

```bash
git clone https://github.com/AlejandroRS21/home-ops
cd home-ops
cp .env.example .env                              # add secrets
cp config/user_profile.template.yml config/user_profile.yml  # edit your search URL & scoring
docker compose up
```

That's it. The daemon scrapes, scores, and alerts on your schedule. No cloud dependencies, no external services.

---

## How it works

```
Idealista ──> Scrapling ──> 5-dimension scorer ──> Telegram alert
                 │                    │
                 │                    ├── price (weight 0.35)
                 │                    ├── size (weight 0.25)
                 │                    ├── energy certificate (weight 0.15)
                 │                    ├── garage (weight 0.10)
                 │                    └── Euribor affordability (weight 0.15)
                 │
                 └── Dedup by content hash → only new listings trigger alerts
```

Every dimension is configurable via `user_profile.yml` — thresholds, weights, expected garage price, Euribor rate, and more.

The daemon runs on a schedule (daily at 09:00 Europe/Madrid by default), respects a daily alert quota, recovers from stale/crashed runs, and supports human-in-the-loop approval before alerts are sent.

---

## Configuration

| File | Purpose |
|------|---------|
| `.env` | Secrets: Telegram bot token, chat ID, Gemini API key |
| `user_profile.yml` | Idealista search URL, scoring thresholds & weights, alert schedule, Euribor rate |

Config lives outside the container — edit `config/user_profile.yml` and restart with `docker compose restart`.

---

## CLI

```
homeops scan       — run one scrape → score → alert cycle now
homeops approve    — approve pending listings for alerting (HITL mode)
homeops daemon     — persistent daemon loop with schedule
homeops status     — show pipeline state, last run, pending approvals
```

---

## Stack

| Layer | Choice | Why |
|-------|--------|-----|
| Language | Python 3.12 | — |
| Scraper | Scrapling + curl_cffi | Undetectable TLS fingerprinting, adaptive parser |
| Scoring | Custom engine | 5 configurable dimensions, Euribor-aware |
| Alerter | python-telegram-bot | Direct peer-to-peer, no server needed |
| Storage | DuckDB | Embedded, zero-config, persists across restarts |
| CLI | Typer + Rich | Typed, self-documenting commands |
| Config | YAML + .env | Human-readable, version-controlled |
| CI | pytest + ruff + mypy | 184 tests, strict typing |

---

## Tests

```bash
pytest                          # 184 tests, coverage report
pytest tests/test_scorer/       # scoring engine only
pytest tests/test_cli.py        # CLI commands
```

Tests run in CI on every push via GitHub Actions (pytest + ruff + mypy).

---

## Roadmap

- [x] MVP: scrape → score → alert on Telegram
- [x] Daemon scheduler with catch-up recovery and daily quota
- [x] Human-in-the-loop approval gate
- [x] Docker deployment
- [ ] Detail page scraping (exact garage price, real energy certificate)
- [ ] Catastro API integration for cadastral reference enrichment
- [ ] Textual TUI for real-time pipeline monitoring
- [ ] Multi-portal support (Fotocasa, Habitaclia)

---

## Why

Finding a flat in Spain is a race. By the time you open Idealista, the listing is hours old and the good ones are gone. This pipeline checks Idealista every morning at 09:00, scores every new listing against your personal criteria, and pushes the best matches to your phone before you finish breakfast.

No dashboards to check. No daily "I should look at Idealista" mental load. Just a Telegram ping when something worth seeing appears.

---

Project template: [openspec](https://github.com/gentleman-programming/openspec)
