"""Deterministic mock primitives shared by the Drex and frontier clients.

Every mock output is a pure function of the image bytes plus the question,
so repeated runs (eval, tests, demo) produce identical results. The mock
derives its "ground truth" from the screenshot file name, which the dataset
generator names `<category>_NN.png`; unknown files fall back to a
hash-picked option. A small hash-driven error rate keeps the mock results
non-trivial (imperfect accuracy, some low-confidence cases, some
escalations in the cascade).
"""

import contextvars
import hashlib
import json
from pathlib import Path

from . import config

KNOWN_ISSUES_PATH = config.ROOT / "data" / "known_issues.json"

# confusion rate: share of mock calls where the top-1 is a WRONG option
CONFUSION_RATE = 0.08
# top-1 probability range of the mock
P_TOP_MIN = 0.45
P_TOP_SPAN = 0.53  # top p in [0.45, 0.98)


def _unit(seed: bytes, salt: str) -> float:
    """Deterministic pseudo-random float in [0, 1)."""
    digest = hashlib.sha256(salt.encode("utf-8") + b"|" + seed).digest()
    return int.from_bytes(digest[:4], "big") / 2**32


def _pick(seed: bytes, salt: str, n: int) -> int:
    digest = hashlib.sha256(salt.encode("utf-8") + b"|" + seed).digest()
    return int.from_bytes(digest[:4], "big") % n


def image_seed(image_path) -> bytes:
    """Hash of the image bytes; accepts a path or raw bytes."""
    if isinstance(image_path, (bytes, bytearray)):
        data = bytes(image_path)
    else:
        data = Path(image_path).read_bytes()
    return hashlib.sha256(data).digest()


# Optional, mock-only: when the pipeline runs on raw uploaded bytes (no file
# name), the intake site may set the original file name as a *mock hint* so
# mock results stay plausible. It is used ONLY by mock code paths and only in
# MOCK_MODE; real API calls never see it, and it is never used for validation
# or security logic (upload file names are untrusted).
MOCK_HINT: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "mock_hint", default=None)


def _category_from_name(name: str | None) -> str | None:
    if not name:
        return None
    stem = Path(name).name
    for label in config.BUG_CATEGORIES + [config.OTHER]:
        if stem.startswith(label + "_"):
            return label
    return None


def _category_from_filename(image_path) -> str | None:
    if isinstance(image_path, (str, Path)):
        return _category_from_name(Path(image_path).name)
    # raw uploaded bytes: fall back to the mock hint, if one was set
    return _category_from_name(MOCK_HINT.get())


def _known_issue_category(issue_id: str) -> str | None:
    try:
        issues = json.loads(KNOWN_ISSUES_PATH.read_text())
    except (OSError, json.JSONDecodeError):
        return None
    for issue in issues:
        if issue["id"] == issue_id:
            return issue["category"]
    return None


def truth_option(image_path, options) -> str | None:
    """The option the mock should favor, given the file name and the options."""
    options = list(options)
    category = _category_from_filename(image_path)
    if category is None:
        return None
    # closed-set category question
    if category in options:
        return category
    # gate question
    if set(options) == set(config.GATE_OPTIONS):
        return "bug_report" if category in config.BUG_CATEGORIES else "not_a_bug"
    # known-issue matching question (options are issue ids plus "none")
    if "none" in options and all(_known_issue_category(o) is not None
                                 or o == "none" for o in options):
        for option in options:
            if option != "none" and _known_issue_category(option) == category:
                return option
        return "none"
    return None


def mock_probabilities(image_path, options, salt: str = "mock") -> tuple[dict[str, float], str]:
    """Probabilities over `options` plus the option the mock chose as top-1.

    `salt` distinguishes model variants (e.g. "drex" vs "frontier") so the
    two mocks do not make identical mistakes on every image.
    """
    options = list(options)
    seed = image_seed(image_path)
    truth = truth_option(image_path, options)
    top = truth
    if top is not None and _unit(seed, f"{salt}:confusion") < CONFUSION_RATE:
        wrong = [o for o in options if o != top]
        top = wrong[_pick(seed, f"{salt}:confuse_pick", len(wrong))]
    if top is None:
        top = options[_pick(seed, f"{salt}:pick", len(options))]
    p_top = P_TOP_MIN + P_TOP_SPAN * _unit(seed, f"{salt}:ptop")
    rest = 1.0 - p_top
    rest_options = [o for o in options if o != top]
    weights = [_unit(seed, f"{salt}:w:{o}") + 1e-6 for o in rest_options]
    total = sum(weights)
    probs = {o: rest * w / total for o, w in zip(rest_options, weights)}
    probs[top] = p_top
    return probs, top


def mock_latency(seed: bytes, salt: str, base_ms: float, spread_ms: float) -> float:
    return base_ms + spread_ms * _unit(seed, salt)
