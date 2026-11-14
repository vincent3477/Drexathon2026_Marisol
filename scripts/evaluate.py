"""Evaluate frontier-only vs Drex-only vs cascade on the labeled screenshot set.

Reads data/screenshots/labels.csv, runs every image through all three
pipeline modes (plus a cascade threshold sweep), and writes to
data/results/:

  results.csv   — one row per image per mode
  summary.json  — accuracies, latencies, costs, escalation rate, sweep,
                  calibration table (with a MOCK/real label on everything)
  accuracy_vs_cost.png
  latency_comparison.png
  calibration.png

Mock mode (MOCK_MODE=1) needs no API keys; every output is then labeled
"MOCK DATA". Usage:  python scripts/evaluate.py
"""

import csv
import json
import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import config, pipeline  # noqa: E402

SHOTS = ROOT / "data" / "screenshots"
OUT = ROOT / "data" / "results"
MODES = ("frontier_only", "drex_only", "cascade")
SWEEP = [round(0.50 + 0.05 * i, 2) for i in range(10)]  # 0.50 .. 0.95
CAL_BUCKETS = [(0.4, 0.5), (0.5, 0.6), (0.6, 0.7), (0.7, 0.8), (0.8, 0.9), (0.9, 1.0)]

MOCK = config.MOCK_MODE
TAG = "MOCK DATA — not real measurements" if MOCK else "real run"


def load_rows() -> list[dict]:
    with (SHOTS / "labels.csv").open() as f:
        rows = list(csv.DictReader(f))
    for row in rows:
        assert (SHOTS / row["filename"]).exists(), row
    return rows


def true_gate(category: str) -> str:
    return "bug_report" if category != "other" else "not_a_bug"


def run_case(rows, mode, threshold):
    """Run every image in one mode/threshold; return per-image result rows."""
    out = []
    for row in rows:
        r = pipeline.triage(SHOTS / row["filename"], "", mode, threshold)
        steps = r["steps"]
        match_true = row["known_issue_id"] or "none"
        out.append({
            "filename": row["filename"],
            "mode": mode,
            "gate_true": true_gate(row["category"]),
            "gate_pred": steps["gate"]["decision"],
            "gate_correct": steps["gate"]["decision"] == true_gate(row["category"]),
            "cat_true": row["category"],
            "cat_pred": steps["category"]["decision"],
            "cat_correct": steps["category"]["decision"] == row["category"],
            "match_true": match_true,
            "match_pred": steps["match"]["decision"],
            "match_correct": steps["match"]["decision"] == match_true,
            "decision_latency_ms": r["latency_ms"],
            "cost_usd_decision": r["cost_usd"]["decision"],
            "escalated": bool(r["escalated_steps"]),
            "repro_drafted": r["repro_draft"] is not None,
            "_top1_cat_prob": steps["category"]["confidence"],
        })
    return out


def stats(cases: list[dict], mode: str, threshold) -> dict:
    n = len(cases)
    lats = sorted(c["decision_latency_ms"] for c in cases)
    p95 = lats[min(n - 1, int(0.95 * n))]
    return {
        "mode": mode,
        "threshold": threshold,
        "n_images": n,
        "gate_accuracy": sum(c["gate_correct"] for c in cases) / n,
        "category_accuracy": sum(c["cat_correct"] for c in cases) / n,
        "match_accuracy": sum(c["match_correct"] for c in cases) / n,
        "mean_decision_latency_ms": sum(lats) / n,
        "p95_decision_latency_ms": p95,
        "cost_per_1000_usd": 1000 * sum(c["cost_usd_decision"] for c in cases) / n,
        "escalation_rate": sum(c["escalated"] for c in cases) / n,
    }


def calibration(cases_drex: list[dict]) -> list[dict]:
    buckets = []
    for lo, hi in CAL_BUCKETS:
        in_b = [c for c in cases_drex
                if c["_top1_cat_prob"] is not None and lo <= c["_top1_cat_prob"] < hi
                or (hi == 1.0 and c["_top1_cat_prob"] == 1.0)]
        if in_b:
            buckets.append({
                "bucket": f"[{lo:.1f}, {hi:.1f})",
                "n": len(in_b),
                "mean_top1_prob": sum(c["_top1_cat_prob"] for c in in_b) / len(in_b),
                "accuracy": sum(c["cat_correct"] for c in in_b) / len(in_b),
            })
        else:
            buckets.append({"bucket": f"[{lo:.1f}, {hi:.1f})", "n": 0,
                            "mean_top1_prob": None, "accuracy": None})
    return buckets


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    rows = load_rows()
    print(f"mode: {'MOCK' if MOCK else 'REAL'} | {len(rows)} labeled images")

    all_cases = []
    by_mode = {}
    for mode in MODES:
        cases = run_case(rows, mode, config.DEFAULT_THRESHOLD)
        all_cases.extend(cases)
        by_mode[mode] = stats(cases, mode, config.DEFAULT_THRESHOLD)
        print(f"  {mode}: cat_acc={by_mode[mode]['category_accuracy']:.3f} "
              f"cost/1k=${by_mode[mode]['cost_per_1000_usd']:.4f}")

    # threshold sweep for the cascade
    sweep = [stats(run_case(rows, "cascade", t), "cascade", t) for t in SWEEP]
    for s in sweep:
        print(f"  cascade@{s['threshold']:.2f}: cat_acc={s['category_accuracy']:.3f} "
              f"cost/1k=${s['cost_per_1000_usd']:.4f} "
              f"esc={s['escalation_rate']:.2f}")

    # calibration for Drex (category step, drex_only mode)
    drex_cases = [c for c in all_cases if c["mode"] == "drex_only"]
    cal = calibration(drex_cases)

    # results.csv (drop internal field)
    fields = ["filename", "mode", "gate_true", "gate_pred", "gate_correct",
              "cat_true", "cat_pred", "cat_correct", "match_true", "match_pred",
              "match_correct", "decision_latency_ms", "cost_usd_decision",
              "escalated", "repro_drafted"]
    with (OUT / "results.csv").open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        for c in all_cases:
            w.writerow({k: c[k] for k in fields})

    summary = {
        "mock": MOCK,
        "label": TAG,
        "n_images": len(rows),
        "default_threshold": config.DEFAULT_THRESHOLD,
        "modes": by_mode,
        "cascade_sweep": sweep,
        "drex_calibration": cal,
        "note": ("All numbers are deterministic MOCK outputs (no model calls), "
                 "not real measurements." if MOCK else
                 "Numbers measured against the live APIs."),
    }
    (OUT / "summary.json").write_text(json.dumps(summary, indent=2))

    # ---- charts ----------------------------------------------------------
    plt.rcParams.update({"figure.dpi": 140, "font.size": 11})

    # 1. accuracy vs cost per 1,000 images (decision steps only)
    fig, ax = plt.subplots(figsize=(8, 5.5))
    for mode, s in by_mode.items():
        ax.scatter(s["cost_per_1000_usd"], s["category_accuracy"], s=90, zorder=3)
        ax.annotate(mode, (s["cost_per_1000_usd"], s["category_accuracy"]),
                    textcoords="offset points", xytext=(8, 6))
    ax.plot([s["cost_per_1000_usd"] for s in sweep],
            [s["category_accuracy"] for s in sweep],
            "--o", ms=4, label="cascade threshold sweep")
    ax.set_xlabel("Estimated cost per 1,000 images (decision steps), USD")
    ax.set_ylabel("Category accuracy")
    ax.set_ylim(0.80, 1.03)
    ax.set_title(f"Accuracy vs cost — {TAG}")
    ax.legend()
    fig.tight_layout()
    fig.savefig(OUT / "accuracy_vs_cost.png")
    plt.close(fig)

    # 2. latency comparison
    fig, ax = plt.subplots(figsize=(7.5, 5.5))
    labels = list(by_mode)
    mean = [by_mode[m]["mean_decision_latency_ms"] for m in labels]
    p95 = [by_mode[m]["p95_decision_latency_ms"] for m in labels]
    x = range(len(labels))
    ax.bar([i - 0.2 for i in x], mean, width=0.4, label="mean")
    ax.bar([i + 0.2 for i in x], p95, width=0.4, label="p95")
    ax.set_xticks(list(x), labels)
    ax.set_ylabel("Decision latency, ms (3 steps, excluding repro draft)")
    ax.set_title(f"Latency comparison — {TAG}")
    ax.legend()
    fig.tight_layout()
    fig.savefig(OUT / "latency_comparison.png")
    plt.close(fig)

    # 3. Drex calibration
    fig, ax = plt.subplots(figsize=(7, 5.5))
    xs = [b["mean_top1_prob"] for b in cal if b["n"]]
    ys = [b["accuracy"] for b in cal if b["n"]]
    ax.plot([0, 1], [0, 1], "k:", label="perfectly calibrated")
    ax.plot(xs, ys, "-o", label="Drex (category step)")
    for b in cal:
        if b["n"]:
            ax.annotate(f"n={b['n']}", (b["mean_top1_prob"], b["accuracy"]),
                        textcoords="offset points", xytext=(0, 9), ha="center",
                        fontsize=9)
    ax.set_xlabel("Mean top-1 probability (bucket)")
    ax.set_ylabel("Actual category accuracy in bucket")
    ax.set_title(f"Drex calibration (reliability) — {TAG}")
    ax.set_xlim(0.4, 1.0)
    ax.legend()
    fig.tight_layout()
    fig.savefig(OUT / "calibration.png")
    plt.close(fig)

    print(f"wrote {len(all_cases)} rows -> {OUT}/results.csv")
    print(f"wrote {OUT}/summary.json and 3 charts")


if __name__ == "__main__":
    main()
