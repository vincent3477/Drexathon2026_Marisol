"""Central configuration: environment variables and editable price table.

All secrets are read from environment variables (optionally from a local
.env file, which is never committed) and are only ever used server-side.
"""

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _load_dotenv() -> None:
    """Load KEY=VALUE lines from ./.env without overriding real env vars."""
    path = ROOT / ".env"
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip())


_load_dotenv()

# ---------------------------------------------------------------------------
# Runtime mode
# ---------------------------------------------------------------------------
MOCK_MODE = os.environ.get("MOCK_MODE", "0").strip() == "1"

# ---------------------------------------------------------------------------
# Drex (Nace.AI)
# ---------------------------------------------------------------------------
NACE_API_KEY = os.environ.get("NACE_API_KEY", "")
# Hosted API root (see https://console.nace.ai/docs, "Evaluate questions").
NACE_BASE_URL = os.environ.get("NACE_BASE_URL", "https://console.nace.ai").rstrip("/")
# Drex Max, the active pinned id per the docs.
NACE_MODEL = os.environ.get("NACE_MODEL", "drex-max-v1.1")

# ---------------------------------------------------------------------------
# Second model: frontier LLM (used only for escalation and repro drafts).
# Default provider: DeepInfra (OpenAI-compatible endpoint, vision models).
# ---------------------------------------------------------------------------
FRONTIER_PROVIDER = os.environ.get("FRONTIER_PROVIDER", "deepinfra").lower()

if FRONTIER_PROVIDER == "deepinfra":
    # DeepInfra key from its own env var; FRONTIER_API_KEY still works as a
    # fallback so a single key var can be used for any provider.
    FRONTIER_API_KEY = (os.environ.get("DEEPINFRA_API_KEY")
                        or os.environ.get("FRONTIER_API_KEY", ""))
else:
    FRONTIER_API_KEY = os.environ.get("FRONTIER_API_KEY", "")

_FRONTIER_DEFAULT_MODELS = {
    "deepinfra": "Qwen/Qwen3-VL-30B-A3B-Instruct",  # vision model
    "openai": "gpt-4o",
    "anthropic": "claude-3-5-sonnet-20241022",
}
FRONTIER_MODEL = os.environ.get("FRONTIER_MODEL") or _FRONTIER_DEFAULT_MODELS.get(
    FRONTIER_PROVIDER, "gpt-4o")

_FRONTIER_DEFAULT_BASES = {
    "deepinfra": "https://api.deepinfra.com/v1/openai",  # OpenAI-compatible
    "openai": "https://api.openai.com/v1",
    "anthropic": "https://api.anthropic.com",
}
FRONTIER_BASE_URL = os.environ.get(
    "FRONTIER_BASE_URL",
    _FRONTIER_DEFAULT_BASES.get(FRONTIER_PROVIDER, "https://api.openai.com/v1"),
).rstrip("/")

# ---------------------------------------------------------------------------
# Taxonomy (label strings used consistently across the whole project)
# ---------------------------------------------------------------------------
BUG_CATEGORIES = [
    "auth_failure",
    "network_error",
    "ui_layout_glitch",
    "data_not_loading",
    "crash_dialog",
    "permission_denied",
]
OTHER = "other"
CLASSIFY_OPTIONS = BUG_CATEGORIES + [OTHER]

GATE_OPTIONS = ["bug_report", "not_a_bug"]

DEFAULT_THRESHOLD = 0.7

# ---------------------------------------------------------------------------
# Pricing — EDITABLE ESTIMATES, not authoritative figures.
# Units: USD per 1,000,000 tokens. Adjust freely before quoting real costs.
# ---------------------------------------------------------------------------
# Per the Nace docs, Drex requests are billed "at the per-input-token price"
# (output tokens of a decision are not billed), so the output price is 0.
DREX_PRICING = {
    "per_1m_input_tokens": 2.0,    # editable estimate
    "per_1m_output_tokens": 0.0,   # docs: billed on input tokens only
}

FRONTIER_PRICING = {
    # provider: model: {in, out} — fall back to the provider's "_default".
    "deepinfra": {
        # estimates from deepinfra.com's published per-1M-token prices
        "Qwen/Qwen3-VL-30B-A3B-Instruct": {"per_1m_input_tokens": 0.30, "per_1m_output_tokens": 0.60},
        "_default": {"per_1m_input_tokens": 0.50, "per_1m_output_tokens": 1.00},
    },
    "openai": {
        "gpt-4o": {"per_1m_input_tokens": 2.5, "per_1m_output_tokens": 10.0},
        "_default": {"per_1m_input_tokens": 3.0, "per_1m_output_tokens": 15.0},
    },
    "anthropic": {
        "claude-3-5-sonnet": {"per_1m_input_tokens": 3.0, "per_1m_output_tokens": 15.0},
        "_default": {"per_1m_input_tokens": 3.0, "per_1m_output_tokens": 15.0},
    },
}


def estimate_cost(provider: str, model: str, input_tokens: int, output_tokens: int) -> float:
    """USD cost for a call, from the editable table above (mock-safe: pure math)."""
    if provider == "drex":
        table = DREX_PRICING
    else:
        table = FRONTIER_PRICING.get(provider, {}).get(
            model, FRONTIER_PRICING.get(provider, {}).get("_default")
        ) or {"per_1m_input_tokens": 3.0, "per_1m_output_tokens": 15.0}
    return (
        input_tokens * table["per_1m_input_tokens"] / 1_000_000
        + output_tokens * table["per_1m_output_tokens"] / 1_000_000
    )
