"""Best-effort LLM enrichment for scraped listings.

Uses litellm to analyze a listing's free-text description and extract
structured signals the regex parser can't reliably capture (renovation
state, orientation, zone noise) plus LLM-judged scam red flags. The raw
call is always persisted for traceability; the function never raises.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from home_ops.models.data_storage import DuckDBConnection
    from home_ops.models.schema import Config, Listing

logger = logging.getLogger(__name__)

_PROMPT = (
    "Analiza esta descripción de un anuncio inmobiliario en español. "
    "Devuelve SOLO un JSON válido, sin texto adicional, con estas claves:\n"
    '{"estado_reforma": "string|null", "orientacion": "string|null", '
    '"ruido_zona": "string|null", "red_flags_llm": ["string", ...]}\n'
    "- estado_reforma: estado de reforma si se menciona (ej. 'reformado', "
    "'a reformar', 'segunda mano'), null si no.\n"
    "- orientacion: orientación si se menciona (ej. 'sur', 'este'), null si no.\n"
    "- ruido_zona: si describe zona ruidosa o tranquila, null si no.\n"
    "- red_flags_llm: frases sospechosas de estafa (urgencia, pago fuera de "
    "plataforma, precio irreal, pedir señal sin visita); lista vacía si no hay.\n"
    "Descripción:\n"
)


@dataclass
class LlmAnalysis:
    """Structured result of the LLM description analysis."""

    listing_id: int
    estado_reforma: str | None = None
    orientacion: str | None = None
    ruido_zona: str | None = None
    red_flags_llm: list[str] = field(default_factory=list)
    model_used: str = ""
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    raw_response: str = ""


def analyze_description(
    listing: Listing,
    config: Config,
    db: DuckDBConnection,
) -> LlmAnalysis | None:
    """Analyze a listing's description via LLM and persist the raw call.

    Best-effort: returns None (and never raises) if the LLM is disabled,
    unconfigured, the description is empty, or the call/parse fails. When
    litellm.completion returns a response, the raw result is always
    persisted to ``llm_analysis`` even if JSON parsing fails — traceability
    is preserved regardless of parse success.

    Args:
        listing: Listing with a non-empty description and an ``id`` set
            (post-insert), the caller guarantees this.
        config: Application config carrying ``llm`` (LlmConfig).
        db: DuckDB connection for persisting the analysis row.

    Returns:
        LlmAnalysis on success, None otherwise.
    """
    llm = config.llm
    if not llm.enabled or not llm.api_key or not llm.model:
        return None
    if listing.id is None:
        return None
    if not listing.description or not listing.description.strip():
        return None

    try:
        import litellm  # lazy import: litellm calls load_dotenv() on import
        # litellm requires a provider prefix on custom OpenAI-compatible
        # endpoints; default to "openai/" when the model name has none.
        model = llm.model if "/" in llm.model else f"openai/{llm.model}"
        response = litellm.completion(
            model=model,
            messages=[
                {
                    "role": "user",
                    "content": _PROMPT + listing.description,
                }
            ],
            api_base=llm.base_url or None,
            api_key=llm.api_key,
            timeout=30,
        )
    except Exception as exc:  # noqa: BLE001 — network/API failures are non-fatal
        logger.warning("LLM enrichment failed for listing %s: %s", listing.id, exc)
        return None

    raw = _response_text(response)
    usage = getattr(response, "usage", None)
    prompt_tokens = getattr(usage, "prompt_tokens", None)
    completion_tokens = getattr(usage, "completion_tokens", None)

    parsed = _parse_json(raw)
    result = LlmAnalysis(
        listing_id=listing.id,
        model_used=llm.model,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        raw_response=raw,
    )
    if parsed is not None:
        result.estado_reforma = parsed.get("estado_reforma")
        result.orientacion = parsed.get("orientacion")
        result.ruido_zona = parsed.get("ruido_zona")
        flags = parsed.get("red_flags_llm")
        if isinstance(flags, list):
            result.red_flags_llm = [str(f) for f in flags]

    _persist(result, db)
    return result


def _response_text(response: Any) -> str:
    """Extract the text of the first choice from a litellm response."""
    try:
        return str(response.choices[0].message.content or "")
    except (AttributeError, IndexError, TypeError):
        return ""


def _parse_json(raw: str) -> dict[str, Any] | None:
    """Tolerantly parse the LLM's JSON, returning None on any failure."""
    text = raw.strip()
    if not text:
        return None
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        # Some models wrap the JSON in markdown fences.
        start = text.find("{")
        end = text.rfind("}")
        if start == -1 or end <= start:
            return None
        try:
            data = json.loads(text[start : end + 1])
        except json.JSONDecodeError:
            return None
    return data if isinstance(data, dict) else None


def _persist(result: LlmAnalysis, db: DuckDBConnection) -> None:
    """Persist the analysis row, always keeping the raw response.

    Raises nothing: persistence failure is logged and swallowed so the
    enrichment itself stays non-blocking.
    """
    try:
        db.conn.execute(
            """
            INSERT INTO llm_analysis (
                listing_id, estado_reforma, orientacion, ruido_zona,
                red_flags_llm, model_used, prompt_tokens, completion_tokens,
                raw_response
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                result.listing_id,
                result.estado_reforma,
                result.orientacion,
                result.ruido_zona,
                result.red_flags_llm,
                result.model_used,
                result.prompt_tokens,
                result.completion_tokens,
                result.raw_response,
            ],
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("Failed to persist llm_analysis for listing %s: %s", result.listing_id, exc)
