"""Tests for the LLM description enrichment module."""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import MagicMock, patch

from home_ops.enricher import llm_analyzer
from home_ops.models.schema import Config, Listing, LlmConfig

if TYPE_CHECKING:
    from home_ops.models.data_storage import DuckDBConnection

_VALID_JSON = (
    '{"estado_reforma": "reformado", "orientacion": "sur", '
    '"ruido_zona": "tranquila", "red_flags_llm": ["urgencia de pago"]}'
)


def _make_config(enabled: bool = True) -> Config:
    """Config with LLM enabled and a fake key/model (no real network)."""
    return Config(
        llm=LlmConfig(
            enabled=enabled,
            model="test/model",
            base_url="http://127.0.0.1:9/v1",
            api_key="sk-test",
        )
    )


def _make_listing(
    db: DuckDBConnection,
    description: str = "Piso reformado, orientación sur, zona tranquila.",
) -> Listing:
    listing = Listing(
        content_hash=f"hash-llm-{description[:8]}",
        address="Calle Test 1",
        url="https://www.idealista.com/inmueble/999/",
        portal="idealista",
        description=description,
    )
    listing.id = db.insert_listing(listing)
    return listing


def _mock_completion(json_body: str) -> MagicMock:
    """Build a litellm.completion mock returning a single-choice response."""
    choice = MagicMock()
    choice.message.content = json_body
    response = MagicMock()
    response.choices = [choice]
    response.usage = MagicMock(prompt_tokens=10, completion_tokens=5)
    return MagicMock(return_value=response)


class TestValidResponse:
    def test_parses_and_persists(self, db: DuckDBConnection) -> None:
        listing = _make_listing(db)
        with patch("litellm.completion", _mock_completion(_VALID_JSON)):
            result = llm_analyzer.analyze_description(listing, _make_config(), db)
        assert result is not None
        assert result.estado_reforma == "reformado"
        assert result.orientacion == "sur"
        assert result.ruido_zona == "tranquila"
        assert result.red_flags_llm == ["urgencia de pago"]
        assert result.model_used == "test/model"
        # Persisted
        rows = db.conn.execute(
            "SELECT estado_reforma, model_used, raw_response "
            "FROM llm_analysis WHERE listing_id = ?",
            [listing.id],
        ).fetchall()
        assert len(rows) == 1
        assert rows[0][0] == "reformado"
        assert rows[0][1] == "test/model"
        assert rows[0][2] == _VALID_JSON


class TestMalformedJson:
    def test_fallback_none_but_persists_raw(self, db: DuckDBConnection) -> None:
        """GIVEN a non-JSON completion WHEN analyzed THEN fields None, raw persisted."""
        listing = _make_listing(db)
        raw = "esto no es json"
        with patch("litellm.completion", _mock_completion(raw)):
            result = llm_analyzer.analyze_description(listing, _make_config(), db)
        assert result is not None
        assert result.estado_reforma is None
        assert result.red_flags_llm == []
        rows = db.conn.execute(
            "SELECT raw_response FROM llm_analysis WHERE listing_id = ?",
            [listing.id],
        ).fetchall()
        assert len(rows) == 1
        assert rows[0][0] == raw

    def test_markdown_fenced_json_parsed(self, db: DuckDBConnection) -> None:
        """GIVEN JSON wrapped in markdown fences WHEN analyzed THEN parsed via fence strip."""
        listing = _make_listing(db)
        fenced = f"```json\n{_VALID_JSON}\n```"
        with patch("litellm.completion", _mock_completion(fenced)):
            result = llm_analyzer.analyze_description(listing, _make_config(), db)
        assert result is not None
        assert result.estado_reforma == "reformado"


class TestDisabled:
    def test_disabled_does_not_call_litellm(self, db: DuckDBConnection) -> None:
        listing = _make_listing(db)
        with patch("litellm.completion") as mock_completion:
            result = llm_analyzer.analyze_description(listing, _make_config(enabled=False), db)
        mock_completion.assert_not_called()
        assert result is None

    def test_unconfigured_model_returns_none(self, db: DuckDBConnection) -> None:
        listing = _make_listing(db)
        config = Config(llm=LlmConfig(enabled=True, model="", api_key="", base_url=""))
        with patch("litellm.completion") as mock_completion:
            result = llm_analyzer.analyze_description(listing, config, db)
        mock_completion.assert_not_called()
        assert result is None


class TestNetworkFailure:
    def test_exception_caught_returns_none(self, db: DuckDBConnection) -> None:
        listing = _make_listing(db)
        with patch(
            "litellm.completion",
            side_effect=Exception("connection refused"),
        ):
            result = llm_analyzer.analyze_description(listing, _make_config(), db)
        assert result is None
        # Nothing persisted on network failure (call never completed).
        rows = db.conn.execute(
            "SELECT COUNT(*) FROM llm_analysis WHERE listing_id = ?",
            [listing.id],
        ).fetchone()
        assert rows is not None and rows[0] == 0


class TestEmptyDescription:
    def test_empty_description_returns_none(self, db: DuckDBConnection) -> None:
        listing = _make_listing(db, description="")
        with patch("litellm.completion") as mock_completion:
            result = llm_analyzer.analyze_description(listing, _make_config(), db)
        mock_completion.assert_not_called()
        assert result is None
