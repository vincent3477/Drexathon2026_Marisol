"""Unit tests for the triage pipeline (mock mode).

Cover: all three modes return the full structured result; escalation
triggers when the top-1 probability is below threshold; a held-out
new-bug screenshot yields "none" for the match step most of the time.
"""

from pathlib import Path

from app import pipeline

ROOT = Path(__file__).resolve().parent.parent
SHOTS = ROOT / "data" / "screenshots"
CRASH = sorted(SHOTS.glob("crash_dialog_*.png"))
AUTH = sorted(SHOTS.glob("auth_failure_*.png"))
OTHER = sorted(SHOTS.glob("other_*.png"))

assert CRASH and AUTH and OTHER, "run scripts/generate_screenshots.py first"

STEP_KEYS = {"decision", "confidence", "probabilities", "model", "escalated",
             "latency_ms", "input_tokens", "output_tokens", "cost_usd"}


def _check_full(result, mode, threshold):
    assert result["mode"] == mode
    assert result["threshold"] == threshold
    assert result["mock"] is True
    for name in ("gate", "category", "match"):
        assert set(result["steps"][name]) == STEP_KEYS, result["steps"][name]
    assert result["cost_usd"]["total"] >= 0
    assert result["total_latency_ms"] >= result["latency_ms"] > 0
    assert isinstance(result["escalated_steps"], list)
    return result


def test_frontier_only_full_result():
    r = pipeline.triage(AUTH[0], "login fails", "frontier_only", 0.7)
    _check_full(r, "frontier_only", 0.7)
    assert all(s["model"] == "frontier" for s in r["steps"].values())
    assert all(not s["escalated"] for s in r["steps"].values())
    # known-issue match: auth_failure is a known category -> should not be none
    assert r["steps"]["match"]["decision"] != "none"
    assert r["known_issue"] is not None
    # known issue matched -> no repro draft
    assert r["repro_draft"] is None


def test_drex_only_full_result():
    r = pipeline.triage(AUTH[0], "login fails", "drex_only", 0.7)
    _check_full(r, "drex_only", 0.7)
    assert all(s["model"] == "drex" for s in r["steps"].values())
    # drex probabilities must be normalized over the 7 category options
    probs = r["steps"]["category"]["probabilities"]
    assert abs(sum(probs.values()) - 1.0) < 1e-6
    assert set(probs) == set(pipeline.config.CLASSIFY_OPTIONS)


def test_cascade_low_threshold_does_not_escalate():
    r = pipeline.triage(AUTH[0], "login fails", "cascade", 0.01)
    _check_full(r, "cascade", 0.01)
    assert r["escalated_steps"] == []
    assert all(s["model"] == "drex" for s in r["steps"].values())


def test_cascade_impossible_threshold_esc_every_step():
    # threshold above any probability forces frontier redo of all steps
    r = pipeline.triage(AUTH[0], "login fails", "cascade", 0.999)
    _check_full(r, "cascade", 0.999)
    assert r["escalated_steps"] == ["gate", "category", "match"]
    assert all(s["model"] == "frontier" for s in r["steps"].values())


def test_held_out_new_bug_matches_none_most_of_the_time():
    n_none = 0
    for img in CRASH:
        r = pipeline.triage(str(img), "app crashed", "drex_only", 0.7)
        if r["steps"]["match"]["decision"] == "none":
            n_none += 1
    # "none" must win a clear majority of the held-out screenshots
    assert n_none > len(CRASH) / 2, f"only {n_none}/{len(CRASH)} got 'none'"
    assert n_none >= 5, f"only {n_none}/{len(CRASH)} got 'none'"
    # and the repro-draft step runs for the new bug
    r = pipeline.triage(str(CRASH[0]), "app crashed", "drex_only", 0.7)
    assert r["new_bug"] is True
    assert r["repro_draft"] is not None
    assert "mock" in r["repro_draft"]["text"].lower()


def test_other_screenshot_not_a_bug_and_no_draft():
    n_not_bug = 0
    for img in OTHER:
        r = pipeline.triage(str(img), "", "drex_only", 0.7)
        if r["steps"]["gate"]["decision"] == "not_a_bug":
            n_not_bug += 1
            # a non-bug screenshot must never produce a repro draft
            assert r["new_bug"] is False
            assert r["repro_draft"] is None
    # the mock gate is imperfect but should flag most clean pages correctly
    assert n_not_bug > len(OTHER) / 2, f"only {n_not_bug}/{len(OTHER)} not_a_bug"


def test_invalid_mode_rejected():
    try:
        pipeline.triage(AUTH[0], "", "sometimes", 0.7)
    except ValueError:
        return
    raise AssertionError("expected ValueError for unknown mode")
