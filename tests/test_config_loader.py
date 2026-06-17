"""Tests for config loading."""

import tempfile
from pathlib import Path

import pytest
import yaml

from home_ops.config.loader import load_config, load_env, load_user_profile


def test_load_user_profile_valid() -> None:
    """GIVEN valid user_profile.yml WHEN loaded THEN returns expected dict."""
    data = {
        "portal": {"idealista_url": "https://test.url"},
        "scoring_thresholds": {"min_score_to_alert": 70},
        "euribor_rate": 2.5,
    }
    with tempfile.NamedTemporaryFile(mode="w", suffix=".yml", delete=False) as f:
        yaml.dump(data, f)
        tmp_path = Path(f.name)

    try:
        result = load_user_profile(tmp_path)
        assert result["portal"]["idealista_url"] == "https://test.url"
        assert result["scoring_thresholds"]["min_score_to_alert"] == 70
        assert result["euribor_rate"] == 2.5
    finally:
        tmp_path.unlink(missing_ok=True)


def test_load_user_profile_missing() -> None:
    """GIVEN missing user_profile.yml WHEN loaded THEN raises FileNotFoundError."""
    with pytest.raises(FileNotFoundError):
        load_user_profile(Path("/nonexistent/path/user_profile.yml"))


def test_load_env_missing_warns() -> None:
    """GIVEN missing .env WHEN loaded THEN returns empty dict with warning."""
    with pytest.warns(UserWarning):
        result = load_env(Path("/nonexistent/.env"))
        expected = {
            "TELEGRAM_BOT_TOKEN": "",
            "CHAT_ID": "",
            "GEMINI_API_KEY": "",
            "APIFY_API_TOKEN": "",
        }
        assert result == expected


def test_load_env_valid() -> None:
    """GIVEN valid .env WHEN loaded THEN returns secrets dict."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".env", delete=False) as f:
        f.write("TELEGRAM_BOT_TOKEN=test_token\n")
        f.write("GEMINI_API_KEY=test_key\n")
        f.write("APIFY_API_TOKEN=test_apify\n")
        tmp_path = Path(f.name)

    try:
        result = load_env(tmp_path)
        assert result["TELEGRAM_BOT_TOKEN"] == "test_token"
        assert result["GEMINI_API_KEY"] == "test_key"
        assert result["APIFY_API_TOKEN"] == "test_apify"
    finally:
        tmp_path.unlink(missing_ok=True)


def test_load_config_integration() -> None:
    """GIVEN valid YAML and .env WHEN load_config called THEN returns Config model."""
    yaml_data = {
        "portal": {"idealista_url": "https://test.url"},
        "scoring_thresholds": {"min_score_to_alert": 70},
        "hitl_approval_required": True,
        "euribor_rate": 3.0,
    }
    env_data = "TELEGRAM_BOT_TOKEN=bot123\nGEMINI_API_KEY=gemini_key\nAPIFY_API_TOKEN=apify_key\n"

    with (
        tempfile.NamedTemporaryFile(mode="w", suffix=".yml", delete=False) as yf,
        tempfile.NamedTemporaryFile(mode="w", suffix=".env", delete=False) as ef,
    ):
        yaml.dump(yaml_data, yf)
        yml_path = Path(yf.name)
        ef.write(env_data)
        env_path = Path(ef.name)

    try:
        config = load_config(yml_path, env_path)
        assert config.portal_url == "https://test.url"
        assert config.scoring_thresholds["min_score_to_alert"] == 70
        assert config.hitl_approval_required is True
        assert config.euribor_rate == 3.0
        assert config.telegram_bot_token == "bot123"
        assert config.telegram_chat_id == ""
        assert config.scraper.max_pages_per_scan == 5  # default when not in YAML
    finally:
        yml_path.unlink(missing_ok=True)
        env_path.unlink(missing_ok=True)


def test_load_config_custom_scraper() -> None:
    """GIVEN scraper section in YAML WHEN load_config THEN reads max_pages_per_scan."""
    yaml_data = {
        "portal": {"idealista_url": "https://test.url"},
        "scraper": {"max_pages_per_scan": 3},
    }
    env_data = "TELEGRAM_BOT_TOKEN=bot123\nGEMINI_API_KEY=gemini_key\nAPIFY_API_TOKEN=apify_key\n"

    with (
        tempfile.NamedTemporaryFile(mode="w", suffix=".yml", delete=False) as yf,
        tempfile.NamedTemporaryFile(mode="w", suffix=".env", delete=False) as ef,
    ):
        yaml.dump(yaml_data, yf)
        yml_path = Path(yf.name)
        ef.write(env_data)
        env_path = Path(ef.name)

    try:
        config = load_config(yml_path, env_path)
        assert config.scraper.max_pages_per_scan == 3
    finally:
        yml_path.unlink(missing_ok=True)
        env_path.unlink(missing_ok=True)
