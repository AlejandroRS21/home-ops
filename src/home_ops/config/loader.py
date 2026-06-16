"""Configuration loader: YAML + .env + defaults merged into a Pydantic Config model."""

import warnings
from pathlib import Path
from typing import Any

import yaml
from dotenv import dotenv_values

from home_ops.models.schema import Config


def find_project_root() -> Path:
    """Find the project root by looking for pyproject.toml upwards."""
    cwd = Path.cwd()
    for parent in [cwd] + list(cwd.parents):
        if (parent / "pyproject.toml").exists():
            return parent
    return cwd


def load_user_profile(path: Path | None = None) -> dict[str, Any]:
    """Load user_profile.yml and return raw dict.

    Raises FileNotFoundError if the file does not exist.
    """
    if path is None:
        path = find_project_root() / "user_profile.yml"

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

    Returns a warning (via warnings.warn) if .env is missing.
    """
    searched_path = env_path or find_project_root() / ".env"

    if not searched_path.exists():
        warnings.warn(
            f".env file not found at {searched_path}. "
            "Secrets (Telegram, Gemini, Apify tokens) will be missing. "
            "Copy .env.example to .env and fill in your credentials.",
            stacklevel=2,
        )
        return {}

    values = dotenv_values(searched_path)
    return {
        "TELEGRAM_BOT_TOKEN": values.get("TELEGRAM_BOT_TOKEN") or "",
        "GEMINI_API_KEY": values.get("GEMINI_API_KEY") or "",
        "APIFY_API_TOKEN": values.get("APIFY_API_TOKEN") or "",
    }


def load_config(config_path: Path | None = None, env_path: Path | None = None) -> Config:
    """Load and merge configuration from YAML + .env into a Config model.

    Priority (last wins): built-in defaults → YAML → env vars.
    """
    raw = load_user_profile(config_path)
    secrets = load_env(env_path)

    # Build Config from merged data
    return Config(
        portal_url=raw.get("portal", {}).get("idealista_url", ""),
        scoring_thresholds=raw.get("scoring_thresholds", {}),
        hitl_approval_required=raw.get("hitl_approval_required", True),
        garage_config=raw.get("garage", {}),
        euribor_rate=raw.get("euribor_rate", 3.5),
        alert_schedule=raw.get("alert_schedule", {"time": "09:00", "timezone": "Europe/Madrid"}),
        telegram_chat_id=secrets.get("TELEGRAM_BOT_TOKEN", ""),
    )
