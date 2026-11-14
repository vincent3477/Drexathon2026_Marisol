"""Sanity checks for data/screenshots/: CSV rows, files, counts, held-out set.

Exits non-zero (and prints what is wrong) if the dataset is invalid, so it
can be used as a CI-style check after generate_screenshots.py.
"""

import csv
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SHOTS = ROOT / "data" / "screenshots"
BUG_CATEGORIES = {
    "auth_failure", "network_error", "ui_layout_glitch",
    "data_not_loading", "crash_dialog", "permission_denied",
}


def main() -> None:
    errors: list[str] = []
    labels = SHOTS / "labels.csv"
    known = json.loads((ROOT / "data" / "known_issues.json").read_text())
    known_categories = {issue["category"] for issue in known}

    if not labels.exists():
        sys.exit(f"missing {labels}")
    with labels.open() as f:
        rows = list(csv.DictReader(f))

    # 1. every row points at an existing PNG
    seen = set()
    for row in rows:
        if not (SHOTS / row["filename"]).exists():
            errors.append(f"row without file: {row['filename']}")
        seen.add(row["filename"])

    # 2. no PNG on disk without a CSV row
    for p in sorted(SHOTS.glob("*.png")):
        if p.name not in seen:
            errors.append(f"PNG without CSV row: {p.name}")

    # 3. counts: 8 per bug category, 8 "other"
    counts: dict[str, int] = {}
    for row in rows:
        counts[row["category"]] = counts.get(row["category"], 0) + 1
        if row["category"] not in BUG_CATEGORIES | {"other"}:
            errors.append(f"unknown category {row['category']!r} in {row['filename']}")
    for cat in BUG_CATEGORIES:
        if counts.get(cat, 0) != 8:
            errors.append(f"expected 8 screenshots for {cat}, found {counts.get(cat, 0)}")
    if counts.get("other", 0) != 8:
        errors.append(f"expected 8 'other' screenshots, found {counts.get('other', 0)}")

    # 4. exactly one held-out new-bug category, absent from known_issues.json
    new_cats = {r["category"] for r in rows if r["is_new_bug"] == "true"}
    if len(new_cats) != 1:
        errors.append(f"expected exactly one is_new_bug category, found {new_cats}")
    for cat in new_cats:
        if cat in known_categories:
            errors.append(f"held-out category {cat!r} is in known_issues.json")
        ids = {r["known_issue_id"] for r in rows if r["category"] == cat}
        if ids != {""}:
            errors.append(f"held-out rows should have empty known_issue_id, got {ids}")

    # 5. known_issue_id values are consistent with known_issues.json
    id_by_cat = {i["category"]: i["id"] for i in known}
    for row in rows:
        cat = row["category"]
        want = "" if cat in ("other", *new_cats) else id_by_cat.get(cat, "")
        if row["known_issue_id"] != want:
            errors.append(
                f"{row['filename']}: known_issue_id={row['known_issue_id']!r}, expected {want!r}"
            )

    if errors:
        print("DATASET INVALID:")
        for e in errors:
            print(" -", e)
        sys.exit(1)
    print(f"dataset OK: {len(rows)} rows, {len(seen)} PNGs, "
          f"held-out new-bug category: {', '.join(new_cats)}")


if __name__ == "__main__":
    main()
