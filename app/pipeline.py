"""Triage pipeline: gate -> classify -> match known issue -> (draft repro).

Three modes:
  frontier_only — the frontier LLM answers all three decision steps.
  drex_only     — Drex Max answers all three decision steps.
  cascade       — Drex answers; any step whose top-1 probability is below
                  `threshold` is redone with the frontier LLM (escalation).

Step 4 (repro draft) is identical in all three modes and is billed/tracked
separately so it does not affect the decision-quality comparison.
"""

import json
from pathlib import Path

from . import config, drex_client, frontier_client

KNOWN_ISSUES_PATH = config.ROOT / "data" / "known_issues.json"

MODES = ("frontier_only", "drex_only", "cascade")

GATE_QUESTION = (
    "Look at Image 1, a screenshot uploaded with a support report. Is this "
    "screenshot showing a software bug (an error state, crash, or broken "
    "behavior), or is it a healthy / unrelated page?"
)
MATCH_QUESTION = (
    "Look at Image 1. Does it match one of these known issues? "
    "Answer with the matching issue id, or 'none' if it matches none."
)


def load_known_issues() -> list[dict]:
    return json.loads(KNOWN_ISSUES_PATH.read_text())


def build_candidates(predicted_category: str) -> list[dict]:
    """Up to 5 candidate issues: same category first, then the rest.

    Built the same way for all three modes.
    """
    issues = load_known_issues()
    same = [i for i in issues if i["category"] == predicted_category]
    rest = [i for i in issues if i["category"] != predicted_category]
    return (same + rest)[:5]


def _tail(call_log: list[dict]) -> dict:
    return call_log[-1]


def _run_step(step_name, image_path, description, question, options,
              mode, threshold) -> dict:
    """Run one decision step; returns the structured step record."""
    record = {
        "decision": None, "confidence": None, "probabilities": None,
        "model": None, "escalated": False,
        "latency_ms": 0.0, "input_tokens": 0, "output_tokens": 0,
        "cost_usd": 0.0,
    }

    def use_drex():
        before = len(drex_client.CALL_LOG)
        probs = drex_client.decide(image_path, question, options)
        call = _tail(drex_client.CALL_LOG)
        top = max(probs, key=probs.get)
        assert before <= len(drex_client.CALL_LOG) - 1, "drex call not logged"
        return top, probs, {
            "model": "drex",
            "decision": top,
            "confidence": probs[top],
            "probabilities": probs,
            "latency_ms": call["latency_ms"],
            "input_tokens": call["input_tokens"],
            "output_tokens": call["output_tokens"],
            "cost_usd": config.estimate_cost(
                "drex", config.NACE_MODEL, call["input_tokens"], call["output_tokens"]),
        }

    def use_frontier():
        res = frontier_client.classify(image_path, options, description)
        return res["choice"], None, {
            "model": "frontier",
            "decision": res["choice"],
            "confidence": res["confidence"],
            "probabilities": res["probabilities"],
            "latency_ms": res["latency_ms"],
            "input_tokens": res["input_tokens"],
            "output_tokens": res["output_tokens"],
            "cost_usd": config.estimate_cost(
                config.FRONTIER_PROVIDER, config.FRONTIER_MODEL,
                res["input_tokens"], res["output_tokens"]),
        }

    if mode == "frontier_only":
        _decision, _probs, step = use_frontier()
    elif mode == "drex_only":
        _decision, _probs, step = use_drex()
    else:  # cascade
        _decision, _probs, step = use_drex()
        if step["confidence"] < threshold:
            record["escalated"] = True
            _decision, _probs, step = use_frontier()
    record.update(step)
    return record


def triage(image_path, description: str, mode: str,
           threshold: float = config.DEFAULT_THRESHOLD) -> dict:
    """Run the full triage pipeline; returns a structured result dict."""
    if mode not in MODES:
        raise ValueError(f"mode must be one of {MODES}, got {mode!r}")

    # Step 1: gate -----------------------------------------------------
    gate = _run_step("gate", image_path, description, GATE_QUESTION,
                     config.GATE_OPTIONS, mode, threshold)

    # Step 2: category --------------------------------------------------
    category = _run_step("category", image_path, description,
                         _CATEGORY_QUESTION,
                         config.CLASSIFY_OPTIONS, mode, threshold)

    # Step 3: match known issue (same candidate construction for all modes)
    candidates = build_candidates(category["decision"])
    candidate_options = [i["id"] for i in candidates] + ["none"]
    match_question = MATCH_QUESTION + " " + "; ".join(
        f"{i['id']}: {i['title']}" for i in candidates)
    match = _run_step("match", image_path, description, match_question,
                      candidate_options, mode, threshold)

    matched = next((i for i in candidates if i["id"] == match["decision"]), None)
    is_bug = gate["decision"] == "bug_report"
    new_bug = is_bug and match["decision"] == "none"

    # Step 4: repro draft — identical in all three modes, tracked separately
    repro = None
    if new_bug:
        before = len(frontier_client.CALL_LOG)
        text = frontier_client.draft_repro(image_path, description,
                                           category["decision"])
        call = _tail(frontier_client.CALL_LOG)
        assert before <= len(frontier_client.CALL_LOG) - 1, "draft not logged"
        repro = {
            "text": text,
            "latency_ms": call["latency_ms"],
            "input_tokens": call["input_tokens"],
            "output_tokens": call["output_tokens"],
            "cost_usd": config.estimate_cost(
                config.FRONTIER_PROVIDER, config.FRONTIER_MODEL,
                call["input_tokens"], call["output_tokens"]),
            "mock": call["mock"],
        }

    steps = {"gate": gate, "category": category, "match": match}
    decision_latency = sum(s["latency_ms"] for s in steps.values())
    decision_cost = sum(s["cost_usd"] for s in steps.values())
    draft_cost = repro["cost_usd"] if repro else 0.0
    return {
        "mode": mode,
        "threshold": threshold,
        "mock": config.MOCK_MODE,
        "steps": steps,
        "known_issue": matched,
        "new_bug": new_bug,
        "repro_draft": repro,
        "latency_ms": round(decision_latency, 3),          # decision steps only
        "total_latency_ms": round(decision_latency + (repro["latency_ms"] if repro else 0.0), 3),
        "cost_usd": {
            "decision": round(decision_cost, 6),
            "draft": round(draft_cost, 6),
            "total": round(decision_cost + draft_cost, 6),
        },
        "escalated_steps": [name for name, s in steps.items() if s["escalated"]],
    }


_CATEGORY_QUESTION = (
    "Look at Image 1, a screenshot of a web application. Which of these "
    "categories best describes what the screenshot shows? If the page is "
    "healthy or unrelated to any listed bug, choose 'other'."
)
