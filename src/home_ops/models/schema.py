"""Pydantic data models for listings, snapshots, price history, and config."""

from datetime import datetime
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, Field


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


class ScraperConfig(BaseModel):
    """Scraper-specific configuration."""

    max_pages_per_scan: int = Field(default=5, ge=1)


class Config(BaseModel):
    """Merged application configuration from YAML + .env."""

    portal_url: str = ""
    scoring_thresholds: dict[str, Any] = Field(default_factory=lambda: {"min_score_to_alert": 70})
    hitl_approval_required: bool = True
    garage_config: dict[str, Any] = Field(default_factory=dict)
    euribor_rate: float = 3.5
    alert_schedule: dict[str, str] = Field(
        default_factory=lambda: {"time": "09:00", "timezone": "Europe/Madrid"}
    )
    scraper: ScraperConfig = Field(default_factory=ScraperConfig)
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""
