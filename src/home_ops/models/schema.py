"""Pydantic data models for listings, snapshots, price history, and config."""

from datetime import datetime
from decimal import Decimal
from typing import Any, Literal
from zoneinfo import available_timezones

from pydantic import BaseModel, Field, field_validator


class Listing(BaseModel):
    """A single property listing scraped from a portal."""

    id: int | None = None
    content_hash: str = Field(..., description="SHA256 of normalized address+m2+floor")
    external_id: str | None = Field(None, description="Portal-specific listing ID")
    url: str = ""
    address: str = ""
    m2: float | None = None
    floor: str | None = None
    price: Decimal | None = None
    garage_price: Decimal | None = Field(None, description="Separate garage cost")
    price_includes_garage: bool = False
    certificado_energetico_present: bool | None = Field(
        None, description="None = unknown, False = missing (illegal since 2013 in Spain)"
    )
    rooms: int | None = None
    description: str = ""
    portal: str = "idealista"
    fetched_at: datetime = Field(default_factory=datetime.now)


class ScoringThresholds(BaseModel):
    """Scoring thresholds and weights driven by user_profile.yml.

    All threshold values come from config — zero hardcoded values in code.
    """

    min_score_to_alert: float = 70.0
    weights: dict[str, float] = Field(
        default_factory=lambda: {
            "price": 0.35,
            "size": 0.25,
            "energy_cert": 0.15,
            "garage": 0.10,
            "affordability": 0.15,
        }
    )
    price_median: float = 250_000.0
    price_over_median_penalty: float = 0.30
    m2_threshold: float = 80.0
    m2_large_threshold: float = 120.0
    affordability_high_ratio: float = 0.50
    affordability_medium_ratio: float = 0.30
    salary_province: float = 30_000.0


class ScheduleConfig(BaseModel):
    """Schedule configuration for the automated daemon pipeline.

    Controls when and how often the pipeline runs, timezone awareness,
    and daily alert volume caps.
    """

    mode: Literal["daily", "interval"] = "daily"
    daily_time: str = "09:00"
    interval_hours: float = 6.0
    timezone: str = "Europe/Madrid"
    max_alerts_per_day: int = 5

    @field_validator("timezone")
    @classmethod
    def validate_timezone(cls, v: str) -> str:
        """Validate that the timezone string is a known IANA timezone."""
        if v not in available_timezones():
            raise ValueError(
                f"timezone '{v}' is not a valid IANA timezone. "
                f"Must be one of the zoneinfo available_timezones()."
            )
        return v

    @field_validator("mode")
    @classmethod
    def validate_mode(cls, v: str) -> str:
        """Validate mode is one of the allowed values."""
        allowed = {"daily", "interval"}
        if v not in allowed:
            raise ValueError(f"mode must be one of {allowed}, got '{v}'")
        return v


class Config(BaseModel):
    """Merged application configuration from YAML + .env."""

    portal_url: str = ""
    scoring_thresholds: dict[str, Any] = Field(default_factory=lambda: {"min_score_to_alert": 70.0})
    hitl_approval_required: bool = True
    euribor_rate: float = 3.5
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""
    scoring: ScoringThresholds | None = None
    alert_schedule: ScheduleConfig = Field(default_factory=ScheduleConfig)
