"""Tests for config loading."""

import tempfile
from pathlib import Path

import pytest
import yaml

from home_ops.config.loader import load_config, load_env, load_user_profile
from home_ops.models.schema import ScheduleConfig


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
    finally:
        yml_path.unlink(missing_ok=True)
        env_path.unlink(missing_ok=True)


class TestAlertScheduleYAML:
    """Tests for alert_schedule YAML mapping to ScheduleConfig."""

    def test_full_alert_section(self) -> None:
        """GIVEN full alert_schedule section WHEN loaded THEN ScheduleConfig populated."""
        yaml_data = {
            "portal": {"idealista_url": "https://test.url"},
            "alert_schedule": {
                "mode": "interval",
                "daily_time": "14:00",
                "interval_hours": 8,
                "timezone": "America/New_York",
                "max_alerts_per_day": 10,
            },
        }
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yml", delete=False) as f:
            yaml.dump(yaml_data, f)
            tmp_path = Path(f.name)

        try:
            config = load_config(tmp_path)
            sched = config.alert_schedule
            assert sched.mode == "interval"
            assert sched.daily_time == "14:00"
            assert sched.interval_hours == 8
            assert sched.timezone == "America/New_York"
            assert sched.max_alerts_per_day == 10
        finally:
            tmp_path.unlink(missing_ok=True)

    def test_missing_alert_section_uses_defaults(self) -> None:
        """GIVEN no alert_schedule section WHEN loaded THEN defaults are used."""
        yaml_data = {
            "portal": {"idealista_url": "https://test.url"},
        }
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yml", delete=False) as f:
            yaml.dump(yaml_data, f)
            tmp_path = Path(f.name)

        try:
            config = load_config(tmp_path)
            sched = config.alert_schedule
            assert sched.mode == "daily"
            assert sched.daily_time == "09:00"
            assert sched.timezone == "Europe/Madrid"
            assert sched.max_alerts_per_day == 5
        finally:
            tmp_path.unlink(missing_ok=True)

    def test_partial_alert_section_merges_defaults(self) -> None:
        """GIVEN partial alert_schedule WHEN loaded THEN missing fields use defaults."""
        yaml_data = {
            "alert_schedule": {
                "mode": "interval",
            },
        }
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yml", delete=False) as f:
            yaml.dump(yaml_data, f)
            tmp_path = Path(f.name)

        try:
            config = load_config(tmp_path)
            sched = config.alert_schedule
            assert sched.mode == "interval"
            assert sched.daily_time == "09:00"  # default
            assert sched.timezone == "Europe/Madrid"  # default
            assert sched.max_alerts_per_day == 5  # default
        finally:
            tmp_path.unlink(missing_ok=True)

    def test_old_time_key_backward_compat(self) -> None:
        """GIVEN old 'time' key in alert_schedule WHEN loaded THEN maps to daily_time."""
        yaml_data = {
            "portal": {"idealista_url": "https://test.url"},
            "alert_schedule": {
                "mode": "daily",
                "time": "14:00",
                "timezone": "America/New_York",
                "max_alerts_per_day": 3,
            },
        }
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yml", delete=False) as f:
            yaml.dump(yaml_data, f)
            tmp_path = Path(f.name)

        try:
            config = load_config(tmp_path)
            sched = config.alert_schedule
            assert sched.daily_time == "14:00"
            assert sched.timezone == "America/New_York"
            assert sched.max_alerts_per_day == 3
        finally:
            tmp_path.unlink(missing_ok=True)
