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
    """GIVEN missing .env WHEN loaded THEN returns telegram-only dict with warning."""
    with pytest.warns(UserWarning) as record:
        result = load_env(Path("/nonexistent/.env"))
    # Dead API keys (Gemini/Apify) must not be part of the secrets surface.
    expected = {
        "TELEGRAM_BOT_TOKEN": "",
        "CHAT_ID": "",
    }
    assert result == expected
    message = str(record[0].message)
    assert "Telegram" in message
    assert "Gemini" not in message
    assert "Apify" not in message


def test_load_env_valid() -> None:
    """GIVEN valid .env WHEN loaded THEN returns telegram secrets only."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".env", delete=False) as f:
        f.write("TELEGRAM_BOT_TOKEN=test_token\n")
        f.write("TELEGRAM_CHAT_ID=chat_999\n")
        tmp_path = Path(f.name)

    try:
        result = load_env(tmp_path)
        assert result["TELEGRAM_BOT_TOKEN"] == "test_token"
        assert result["CHAT_ID"] == "chat_999"
        # Dead keys must not leak into the secrets surface.
        assert "GEMINI_API_KEY" not in result
        assert "APIFY_API_TOKEN" not in result
    finally:
        tmp_path.unlink(missing_ok=True)


def test_load_env_excludes_dead_api_keys() -> None:
    """GIVEN .env still references purged keys WHEN loaded THEN they are absent.

    Even if a user's stale .env contains Gemini/Apify tokens, load_env MUST NOT
    expose them — the keys were purged from the config surface (CONT-004).
    """
    with tempfile.NamedTemporaryFile(mode="w", suffix=".env", delete=False) as f:
        f.write("TELEGRAM_BOT_TOKEN=bot123\n")
        f.write("GEMINI_API_KEY=should_not_appear\n")
        f.write("APIFY_API_TOKEN=should_not_appear\n")
        tmp_path = Path(f.name)

    try:
        result = load_env(tmp_path)
        assert result["TELEGRAM_BOT_TOKEN"] == "bot123"
        assert result["CHAT_ID"] == ""
        assert "GEMINI_API_KEY" not in result
        assert "APIFY_API_TOKEN" not in result
        assert set(result.keys()) == {"TELEGRAM_BOT_TOKEN", "CHAT_ID"}
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
    env_data = "TELEGRAM_BOT_TOKEN=bot123\n"

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
        assert config.scoring is not None
        assert config.scoring.min_score_to_alert == 70
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

    def test_home_ops_config_env_var_used_when_path_none(self) -> None:
        """GIVEN HOME_OPS_CONFIG set WHEN load_user_profile(None) THEN reads from env var."""
        import os

        with tempfile.NamedTemporaryFile(mode="w", suffix=".yml", delete=False) as f:
            yaml.dump({"portal": {"idealista_url": "https://env.url"}}, f)
            env_path = f.name

        try:
            os.environ["HOME_OPS_CONFIG"] = env_path
            result = load_user_profile()
            assert result["portal"]["idealista_url"] == "https://env.url"
        finally:
            del os.environ["HOME_OPS_CONFIG"]
            Path(env_path).unlink(missing_ok=True)

    def test_home_ops_config_fallback_to_config_dir(self) -> None:
        """GIVEN user_profile.yml absent in cwd THEN falls back to config/user_profile.yml."""
        import os

        # Ensure cwd user_profile.yml is not present or mocked
        config_dir = Path.cwd() / "config"
        config_dir.mkdir(exist_ok=True)
        fallback_file = config_dir / "user_profile.yml"
        created_fallback = False
        if not fallback_file.exists():
            fallback_file.write_text("portal:\n  idealista_url: 'https://fallback.url'\n")
            created_fallback = True

        old_env = os.environ.pop("HOME_OPS_CONFIG", None)
        try:
            cwd_file = Path.cwd() / "user_profile.yml"
            renamed_cwd = False
            if cwd_file.exists():
                cwd_file.rename(Path.cwd() / "user_profile.yml.tmp_test")
                renamed_cwd = True

            try:
                result = load_user_profile(None)
                assert result["portal"]["idealista_url"] is not None
            finally:
                if renamed_cwd:
                    (Path.cwd() / "user_profile.yml.tmp_test").rename(cwd_file)
        finally:
            if old_env is not None:
                os.environ["HOME_OPS_CONFIG"] = old_env
            if created_fallback:
                fallback_file.unlink(missing_ok=True)

    def test_home_ops_config_env_var_explicit_path_still_works(self) -> None:
        """GIVEN HOME_OPS_CONFIG set and explicit path THEN uses explicit path."""
        import os

        with tempfile.NamedTemporaryFile(mode="w", suffix=".yml", delete=False) as f:
            yaml.dump({"portal": {"idealista_url": "https://other.url"}}, f)
            explicit_path = f.name
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yml", delete=False) as f:
            yaml.dump({"portal": {"idealista_url": "https://env.url"}}, f)
            env_path = f.name

        try:
            os.environ["HOME_OPS_CONFIG"] = env_path
            result = load_user_profile(Path(explicit_path))
            assert result["portal"]["idealista_url"] == "https://other.url"
        finally:
            del os.environ["HOME_OPS_CONFIG"]
            Path(explicit_path).unlink(missing_ok=True)
            Path(env_path).unlink(missing_ok=True)

    def test_old_time_key_backward_compat(self) -> None:
        """GIVEN old 'time' key in alert_schedule WHEN loaded THEN maps to daily_time."""

    # ------------------------------------------------------------------
    # Buyer protection block parsing
    # ------------------------------------------------------------------

    def test_buyer_protection_block_parsing(self) -> None:
        """GIVEN buyer_protection block WHEN loaded THEN BuyerProtectionConfig populated."""
        yaml_data = {
            "portal": {"idealista_url": "https://test.url"},
            "buyer_protection": {
                "regional_itp_rates": {"madrid": 0.06, "valencia": 0.10},
                "default_itp_rate": 0.09,
                "scam_weights": {"red_flag_text": 50.0, "price_bait": 20.0, "missing_cert": 5.0},
                "red_flag_patterns": [r"solo\s+whatsapp", r"pago\s+por\sbizum"],
                "mortgage_income_ceiling": 0.30,
                "down_payment_pct": 0.25,
                "mortgage_years": 25,
            },
        }
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yml", delete=False) as f:
            yaml.dump(yaml_data, f)
            tmp_path = Path(f.name)

        try:
            config = load_config(tmp_path)
            bp = config.buyer_protection
            assert bp is not None
            assert bp.regional_itp_rates == {"madrid": 0.06, "valencia": 0.10}
            assert bp.default_itp_rate == 0.09
            assert bp.scam_weights == {"red_flag_text": 50.0, "price_bait": 20.0, "missing_cert": 5.0}
            assert bp.red_flag_patterns == [r"solo\s+whatsapp", r"pago\s+por\sbizum"]
            assert bp.mortgage_income_ceiling == 0.30
            assert bp.down_payment_pct == 0.25
            assert bp.mortgage_years == 25
        finally:
            tmp_path.unlink(missing_ok=True)

    def test_buyer_protection_missing_block_opt_out(self) -> None:
        """GIVEN no buyer_protection block WHEN loaded THEN buyer protection is OFF.

        Buyer protection is opt-in: a missing block must leave
        ``config.buyer_protection`` as None so existing deployments keep
        their previous scoring behavior.
        """
        yaml_data = {
            "portal": {"idealista_url": "https://test.url"},
        }
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yml", delete=False) as f:
            yaml.dump(yaml_data, f)
            tmp_path = Path(f.name)

        try:
            config = load_config(tmp_path)
            assert config.buyer_protection is None
        finally:
            tmp_path.unlink(missing_ok=True)

    def test_buyer_protection_empty_block_opt_out(self) -> None:
        """GIVEN an empty buyer_protection block WHEN loaded THEN it stays OFF."""
        yaml_data = {
            "portal": {"idealista_url": "https://test.url"},
            "buyer_protection": {},
        }
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yml", delete=False) as f:
            yaml.dump(yaml_data, f)
            tmp_path = Path(f.name)

        try:
            config = load_config(tmp_path)
            assert config.buyer_protection is None
        finally:
            tmp_path.unlink(missing_ok=True)

    def test_buyer_protection_partial_block_merges_defaults(self) -> None:
        """GIVEN partial buyer_protection block WHEN loaded THEN omitted sub-keys use defaults."""
        yaml_data = {
            "portal": {"idealista_url": "https://test.url"},
            "buyer_protection": {
                "default_itp_rate": 0.09,
            },
        }
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yml", delete=False) as f:
            yaml.dump(yaml_data, f)
            tmp_path = Path(f.name)

        try:
            config = load_config(tmp_path)
            bp = config.buyer_protection
            assert bp is not None
            assert bp.default_itp_rate == 0.09  # from YAML
            assert bp.regional_itp_rates == {
                "madrid": 0.06,
                "catalunya": 0.10,
                "andalucia": 0.07,
            }  # default
            assert bp.scam_weights == {
                "red_flag_text": 40.0,
                "price_bait": 30.0,
                "missing_cert": 10.0,
            }  # default
            assert bp.mortgage_years == 30  # default
        finally:
            tmp_path.unlink(missing_ok=True)
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
