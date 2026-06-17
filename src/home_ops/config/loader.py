"""Configuration loader: YAML + .env + defaults merged into a Pydantic Config model."""

import os
import warnings
from pathlib import Path
from typing import Any

import yaml

from home_ops.models.schema import Config, ScraperConfig


def _load_dotenv(path: Path) -> dict[str, str]:
    """Minimal .env parser (key=value only, no interpolation)."""
    if not path.exists():
        warnings.warn(
            f".env file not found at {path}. "
            "Secrets (Telegram, Gemini, Apify tokens) will be missing. "
            "Copy .env.example to .env and fill in your credentials.",
            stacklevel=2,
        )
        return {}
    result: dict[str, str] = {}
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, val = line.partition("=")
            result[key.strip()] = val.strip().strip("\"'")
    return result


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
        values = _load_dotenv(env_path)
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

    return Config(
        portal_url=raw.get("portal", {}).get("idealista_url", ""),
        scoring_thresholds=raw.get("scoring_thresholds", {}),
        hitl_approval_required=raw.get("hitl_approval_required", True),
        garage_config=raw.get("garage", {}),
        euribor_rate=raw.get("euribor_rate", 3.5),
        alert_schedule=raw.get("alert_schedule", {"time": "09:00", "timezone": "Europe/Madrid"}),
        scraper=ScraperConfig(
            max_pages_per_scan=raw.get("scraper", {}).get("max_pages_per_scan", 5)
        ),
        telegram_bot_token=secrets.get("TELEGRAM_BOT_TOKEN", ""),
        telegram_chat_id=secrets.get("CHAT_ID", ""),
    )
