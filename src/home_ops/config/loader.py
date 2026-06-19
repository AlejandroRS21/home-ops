"""Configuration loader: YAML + .env + defaults merged into a Pydantic Config model."""

import os
import warnings
from pathlib import Path
from typing import Any

import yaml
from dotenv import dotenv_values

from home_ops.models.schema import Config, ScheduleConfig, ScoringThresholds


def load_user_profile(path: Path | None = None) -> dict[str, Any]:
    """Load user_profile.yml and return raw dict.

    Raises FileNotFoundError if the file does not exist.
    """
    if path is None:
        path = Path.cwd() / "user_profile.yml"

    if not path.exists():
        raise FileNotFoundError(
            f"Configuration file not found: {path}\n"
            "Create a user_profile.yml from the template or set HOME_OPS_CONFIG."
        )

    with open(path) as f:
        result: dict[str, Any] = yaml.safe_load(f) or {}
        return result


def load_env(env_path: Path | None = None) -> dict[str, str]:
    """Load .env file and return secrets dict.

    Falls back to environment variables if .env doesn't exist.
    """
    if env_path is None:
        env_path = Path.cwd() / ".env"

    if env_path.exists():
        values = dotenv_values(env_path)
    else:
        values = {}
        warnings.warn(
            f".env file not found at {env_path}. "
            "Secrets (Telegram, Gemini, Apify tokens) will be missing. "
            "Copy .env.example to .env and fill in your credentials.",
            stacklevel=2,
        )

    return {
        "TELEGRAM_BOT_TOKEN": values.get("TELEGRAM_BOT_TOKEN")
        or os.environ.get("TELEGRAM_BOT_TOKEN", ""),
        "CHAT_ID": values.get("CHAT_ID")
        or values.get("TELEGRAM_CHAT_ID")
        or os.environ.get("CHAT_ID", "")
        or os.environ.get("TELEGRAM_CHAT_ID", ""),
        "GEMINI_API_KEY": values.get("GEMINI_API_KEY")
        or os.environ.get("GEMINI_API_KEY", ""),
        "APIFY_API_TOKEN": values.get("APIFY_API_TOKEN")
        or os.environ.get("APIFY_API_TOKEN", ""),
    }


def load_config(config_path: Path | None = None, env_path: Path | None = None) -> Config:
    """Load and merge configuration from YAML + .env into a Config model.

    Priority (last wins): built-in defaults -> YAML -> env vars.
    """
    raw = load_user_profile(config_path)
    secrets = load_env(env_path)

    scoring_raw = raw.get("scoring", {}).get("thresholds", {})
    scoring = ScoringThresholds(**scoring_raw) if scoring_raw else None

    # Parse alert_schedule section with backward-compat for old 'time' key
    alert_raw = raw.get("alert_schedule", {}) or {}
    if "time" in alert_raw and "daily_time" not in alert_raw:
        alert_raw["daily_time"] = alert_raw.pop("time")
    schedule_config = ScheduleConfig(**alert_raw) if alert_raw else ScheduleConfig()

    return Config(
        portal_url=raw.get("portal", {}).get("idealista_url", ""),
        scoring_thresholds=raw.get("scoring_thresholds", {}),
        scoring=scoring,
        alert_schedule=schedule_config,
        hitl_approval_required=raw.get("hitl_approval_required", True),
        euribor_rate=raw.get("euribor_rate", 3.5),
        telegram_bot_token=secrets.get("TELEGRAM_BOT_TOKEN", ""),
        telegram_chat_id=secrets.get("CHAT_ID", ""),
    )
