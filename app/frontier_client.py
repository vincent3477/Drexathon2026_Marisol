"""Frontier LLM client — used ONLY for low-confidence escalations and for
drafting reproduction steps for new bugs.

The provider is selected by FRONTIER_PROVIDER ("openai" = any OpenAI-
compatible chat-completions endpoint, "anthropic" = Messages API). API keys
come from environment variables only and are sent only to the provider.

With MOCK_MODE=1 no HTTP call is made: outputs are deterministic functions
of the image bytes (see app/mocklib.py) and every mock output is labeled.
"""

import base64
import json
import re
import time
from pathlib import Path

import httpx

from . import config, mocklib

# Log of every call made through this module (the pipeline uses it to
# attribute latency, tokens and cost to individual steps).
CALL_LOG: list[dict] = []


def _record(latency_ms: float, input_tokens: int, output_tokens: int, mock: bool) -> None:
    CALL_LOG.append({
        "latency_ms": round(latency_ms, 3),
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "mock": mock,
    })


def _image_parts(image_path) -> tuple[str, str]:
    """(base64 data, mime type); accepts a path or raw uploaded bytes."""
    if isinstance(image_path, (bytes, bytearray)):
        data = bytes(image_path)
    else:
        data = Path(image_path).read_bytes()
    if data.startswith(b"\xff\xd8"):
        mime = "image/jpeg"
    elif data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        mime = "image/webp"
    else:
        mime = "image/png"
    return base64.b64encode(data).decode("ascii"), mime


def _classify_prompt(options, description: str) -> str:
    return (
        "You are triaging a support screenshot (Image 1) of a web application.\n"
        f"User description: {description or '(none given)'}\n"
        "Task: pick the single option that best fits the screenshot and description.\n"
        f"Options: {json.dumps(list(options))}\n"
        "Respond with JSON only, in this shape:\n"
        '{"choice": "<option>", "confidence": <float 0..1>, '
        '"probabilities": {"<option>": <float>}}\n'
        "The 'probabilities' field is optional."
    )


def _parse_choice_reply(text: str, options) -> dict:
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text)
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        raise ValueError(f"frontier model returned no JSON object: {text[:200]!r}")
    data = json.loads(text[start:end + 1])
    choice = data.get("choice")
    if choice not in options:
        raise ValueError(f"frontier model returned unknown option {choice!r}")
    confidence = data.get("confidence")
    if confidence is None:  # model returned none -> fixed value, per spec
        confidence = 0.5
    confidence = max(0.0, min(1.0, float(confidence)))
    probs = data.get("probabilities")
    if isinstance(probs, dict):
        probs = {o: max(0.0, float(probs.get(o, 0.0))) for o in options}
        total = sum(probs.values())
        probs = {o: p / total for o, p in probs.items()} if total > 0 else None
    else:
        probs = None
    return {"choice": choice, "confidence": confidence, "probabilities": probs}


def classify(image_path, options, description: str) -> dict:
    """Classify the screenshot among `options`.

    Returns: {"choice", "confidence", "probabilities" (dict or None),
    "latency_ms", "input_tokens", "output_tokens", "mock"}.
    """
    options = list(options)
    prompt = _classify_prompt(options, description)

    if config.MOCK_MODE:
        seed = mocklib.image_seed(image_path)
        probs, top = mocklib.mock_probabilities(image_path, options,
                                                salt="frontier")
        latency = mocklib.mock_latency(seed, "frontier_lat", base_ms=600, spread_ms=900)
        in_tok = 600 + len(prompt) // 3
        out_tok = 90 + len(str(description)) // 2
        _record(latency, in_tok, out_tok, mock=True)
        return {
            "choice": top,
            "confidence": probs[top],
            "probabilities": probs,
            "latency_ms": latency,
            "input_tokens": in_tok,
            "output_tokens": out_tok,
            "mock": True,
        }

    b64, mime = _image_parts(image_path)
    start = time.perf_counter()
    if config.FRONTIER_PROVIDER == "openai":
        resp = httpx.post(
            f"{config.FRONTIER_BASE_URL}/chat/completions",
            headers={"Authorization": f"Bearer {config.FRONTIER_API_KEY}"},
            json={
                "model": config.FRONTIER_MODEL,
                "max_tokens": 400,
                "messages": [{
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {"type": "image_url",
                         "image_url": {"url": f"data:{mime};base64,{b64}"}},
                    ],
                }],
            },
            timeout=120,
        )
        resp.raise_for_status()
        data = resp.json()
        text = data["choices"][0]["message"]["content"]
        usage = data.get("usage", {})
        in_tok = int(usage.get("prompt_tokens", 0))
        out_tok = int(usage.get("completion_tokens", 0))
    elif config.FRONTIER_PROVIDER == "anthropic":
        resp = httpx.post(
            f"{config.FRONTIER_BASE_URL}/v1/messages",
            headers={
                "x-api-key": config.FRONTIER_API_KEY,
                "anthropic-version": "2023-06-01",
            },
            json={
                "model": config.FRONTIER_MODEL,
                "max_tokens": 400,
                "system": "You triage support screenshots and answer with JSON only.",
                "messages": [{
                    "role": "user",
                    "content": [
                        {"type": "image",
                         "source": {"type": "base64", "media_type": mime, "data": b64}},
                        {"type": "text", "text": prompt},
                    ],
                }],
            },
            timeout=120,
        )
        resp.raise_for_status()
        data = resp.json()
        text = "".join(block.get("text", "") for block in data.get("content", []))
        usage = data.get("usage", {})
        in_tok = int(usage.get("input_tokens", 0))
        out_tok = int(usage.get("output_tokens", 0))
    else:
        raise ValueError(f"unknown FRONTIER_PROVIDER {config.FRONTIER_PROVIDER!r}")

    latency = (time.perf_counter() - start) * 1000
    parsed = _parse_choice_reply(text, options)
    _record(latency, in_tok, out_tok, mock=False)
    return {
        **parsed,
        "latency_ms": latency,
        "input_tokens": in_tok,
        "output_tokens": out_tok,
        "mock": False,
    }


# ---------------------------------------------------------------------------
# Repro drafts
# ---------------------------------------------------------------------------

_CATEGORY_STEPS = {
    "auth_failure": "Go to the Login page, enter a known-valid username and password, and submit the form.",
    "network_error": "Open the Dashboard page and wait for the data to load.",
    "ui_layout_glitch": "Open the Orders page in a narrow (mobile) viewport.",
    "data_not_loading": "Open the Orders page and wait for the table to render.",
    "crash_dialog": "Open the Dashboard and interact with the overview card.",
    "permission_denied": "Open the Profile page as an editor user and click 'Save changes'.",
    "other": "Open the page shown in the screenshot.",
}


def _mock_draft(description: str, category: str) -> str:
    step = _CATEGORY_STEPS.get(category, _CATEGORY_STEPS["other"])
    return (
        "REPRODUCTION STEPS (mock draft — generated without a real LLM)\n"
        f"1. Open the Demo Co. site in a browser.\n"
        f"2. {step}\n"
        "3. Observe the state shown in the screenshot.\n\n"
        "ISSUE DRAFT (mock)\n"
        f"Title: {category} — matches screenshot\n"
        f"Labels: bug, {category}\n"
        f"Description: {description or 'See attached screenshot.'}"
    )


def draft_repro(image_path, description: str, category: str) -> str:
    """Plain-text reproduction steps plus a short issue draft."""
    if config.MOCK_MODE:
        seed = mocklib.image_seed(image_path)
        latency = mocklib.mock_latency(seed, "frontier_draft_lat",
                                       base_ms=900, spread_ms=1200)
        _record(latency, 700, 220, mock=True)
        return _mock_draft(description, category)

    prompt = (
        "You are writing a bug report from a support screenshot (Image 1) "
        f"and a short user description: {description or '(none given)'}. "
        f"The triage category is '{category}' and no known issue matched. "
        "Reply in plain text with (1) numbered reproduction steps, then "
        "(2) a short issue draft with a Title, Labels and Description."
    )
    b64, mime = _image_parts(image_path)
    start = time.perf_counter()
    if config.FRONTIER_PROVIDER == "openai":
        resp = httpx.post(
            f"{config.FRONTIER_BASE_URL}/chat/completions",
            headers={"Authorization": f"Bearer {config.FRONTIER_API_KEY}"},
            json={
                "model": config.FRONTIER_MODEL,
                "max_tokens": 600,
                "messages": [{
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {"type": "image_url",
                         "image_url": {"url": f"data:{mime};base64,{b64}"}},
                    ],
                }],
            },
            timeout=180,
        )
        resp.raise_for_status()
        data = resp.json()
        text = data["choices"][0]["message"]["content"].strip()
        usage = data.get("usage", {})
        in_tok = int(usage.get("prompt_tokens", 0))
        out_tok = int(usage.get("completion_tokens", 0))
    elif config.FRONTIER_PROVIDER == "anthropic":
        resp = httpx.post(
            f"{config.FRONTIER_BASE_URL}/v1/messages",
            headers={
                "x-api-key": config.FRONTIER_API_KEY,
                "anthropic-version": "2023-06-01",
            },
            json={
                "model": config.FRONTIER_MODEL,
                "max_tokens": 600,
                "system": "You write concise, factual bug reports in plain text.",
                "messages": [{
                    "role": "user",
                    "content": [
                        {"type": "image",
                         "source": {"type": "base64", "media_type": mime, "data": b64}},
                        {"type": "text", "text": prompt},
                    ],
                }],
            },
            timeout=180,
        )
        resp.raise_for_status()
        data = resp.json()
        text = "".join(block.get("text", "") for block in data.get("content", [])).strip()
        usage = data.get("usage", {})
        in_tok = int(usage.get("input_tokens", 0))
        out_tok = int(usage.get("output_tokens", 0))
    else:
        raise ValueError(f"unknown FRONTIER_PROVIDER {config.FRONTIER_PROVIDER!r}")

    _record((time.perf_counter() - start) * 1000, in_tok, out_tok, mock=False)
    return text
