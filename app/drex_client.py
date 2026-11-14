"""Client for the Drex Max decision model (Nace.AI).

Drex takes an image plus a fixed list of options and returns a probability
per option — it generates no text. All Drex-specific request/response
handling lives in this module behind two small functions:

    classify(image_path, options) -> dict[str, float]
    decide(image_path, question, options) -> dict[str, float]

API format confirmed against the hosted API docs
(https://console.nace.ai/docs, "Drex / Evaluate questions", fetched
2026-11-14) and the nace-ai/drex-decision-models repo:

  POST {NACE_BASE_URL}/v1/systemone
  Authorization: Bearer <NACE_API_KEY>
  Body:
  {
    "model": "drex-max-v1.1",            # Drex Max
    "state": <any JSON value>,
    "images": [<string>],                # Drex Max only; images shared by
                                         # every question, "Image 1, ..."
    "questions": {
      "answer": {
        "type": "choice",
        "instructions": "<question text>",
        "criteria": {"option": null, ...}
      }
    }
  }
  Response 200:
  {
    "answers": {"answer": {"type": "choice", "choice": "...",
                           "confidence": 0.9, "probabilities": {"opt": 0.9}}},
    "usage": {"input_tokens": 106, "output_tokens": 212},
    "evaluation_time_ms": 142.6,
    "request_id": "req_..."
  }

With MOCK_MODE=1 no HTTP call is made: a deterministic mock (see
app/mocklib.py) derives plausible probabilities from the screenshot
file name, with hash-driven noise so results are non-trivial.
"""

import base64
import time
from pathlib import Path

import httpx

from . import config, mocklib

# Log of every call made through this module (used by the pipeline to
# attribute latency and tokens to individual steps).
CALL_LOG: list[dict] = []


def _record(latency_ms: float, input_tokens: int, output_tokens: int, mock: bool) -> None:
    CALL_LOG.append({
        "latency_ms": round(latency_ms, 3),
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "mock": mock,
    })


def _read_image(image_path) -> bytes:
    """Accepts a path (eval script, tests) or raw uploaded bytes (intake site)."""
    if isinstance(image_path, (bytes, bytearray)):
        return bytes(image_path)
    return Path(image_path).read_bytes()


def _mime(data: bytes) -> str:
    if data.startswith(b"\xff\xd8"):
        return "image/jpeg"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return "image/png"


def _data_url(image_path) -> str:
    data = _read_image(image_path)
    return f"data:{_mime(data)};base64,{base64.b64encode(data).decode('ascii')}"


def _normalize(raw: dict, options) -> dict[str, float]:
    """Map raw option probabilities onto the requested options, sum to 1."""
    probs = {opt: float(raw.get(opt, 0.0)) for opt in options}
    total = sum(probs.values())
    if total <= 0:
        even = 1.0 / len(options)
        return {opt: even for opt in options}
    return {opt: p / total for opt, p in probs.items()}


def decide(image_path, question: str, options) -> dict[str, float]:
    """Probability per option for a closed-set question about the image.

    Returns a dict mapping every requested option to a normalized
    probability. The per-call record (latency, tokens, mock flag) is
    appended to CALL_LOG.
    """
    options = list(options)
    if config.MOCK_MODE:
        start = time.perf_counter()
        probs, _top = mocklib.mock_probabilities(image_path, options, salt="drex")
        seed = mocklib.image_seed(image_path)
        latency = mocklib.mock_latency(seed, "drex_lat", base_ms=120, spread_ms=180)
        _record(latency, 350 + len(question) // 2, 60, mock=True)
        return probs

    body = {
        "model": config.NACE_MODEL,
        "state": {
            "screenshot": "Image 1 — a screenshot of a web application page",
            "question": question,
        },
        # ASSUMPTION: the docs say `images` items are `string | object` and
        # that inline images count toward the 4 MiB body limit, but do not
        # state the string encoding. We assume a base64 data URL.
        "images": [_data_url(image_path)],
        "questions": {
            "answer": {
                "type": "choice",
                "instructions": question,
                "criteria": {opt: None for opt in options},
            }
        },
    }
    start = time.perf_counter()
    resp = httpx.post(
        f"{config.NACE_BASE_URL}/v1/systemone",
        headers={
            "Authorization": f"Bearer {config.NACE_API_KEY}",
            "Content-Type": "application/json",
        },
        json=body,
        timeout=90,
    )
    latency = (time.perf_counter() - start) * 1000
    resp.raise_for_status()
    data = resp.json()

    answer = data["answers"]["answer"]
    # ASSUMPTION: `probabilities` keys are exactly the criteria keys (as in
    # the documented example response); missing options get probability 0.
    probs = _normalize(answer.get("probabilities", {}), options)
    usage = data.get("usage", {})
    _record(latency, int(usage.get("input_tokens", 0)),
            int(usage.get("output_tokens", 0)), mock=False)
    return probs


def classify(image_path, options) -> dict[str, float]:
    """Probability per option for the closed-set bug-category question."""
    question = (
        "Look at Image 1, a screenshot of a web application. Which of these "
        "categories best describes what the screenshot shows? If the page is "
        "healthy or unrelated to any listed bug, choose 'other'."
    )
    return decide(image_path, question, options)
