"""Generate the labeled screenshot dataset with Playwright.

Starts the demo app, visits every seeded-bug page under 8 viewport/theme/zoom
combinations per bug category, plus 8 "other" (clean / unrelated) pages, and
writes PNGs to data/screenshots/ together with labels.csv.

Columns of labels.csv: filename, category, known_issue_id, is_new_bug
  * known_issue_id links the row to data/known_issues.json (empty for "other"
    rows and for the held-out "new bug" category, NEW_BUG_CATEGORY below,
    which is deliberately absent from known_issues.json).
  * is_new_bug is "true" for the held-out category and "false" otherwise.

Only extra setup on a clean checkout: `playwright install chromium`.
Rerunnable: it overwrites the output directory and the CSV.
"""

import csv
import json
import socket
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

OUT_DIR = ROOT / "data" / "screenshots"
KNOWN_ISSUES = json.loads((ROOT / "data" / "known_issues.json").read_text())

# The one held-out category: it has no entry in known_issues.json, so the
# duplicate-matching step must answer "none" for these screenshots.
NEW_BUG_CATEGORY = "crash_dialog"

CATEGORY_TO_BUG_FLAG = {
    "auth_failure": "login_401",
    "network_error": "network_error",
    "ui_layout_glitch": "ui_layout_glitch",
    "data_not_loading": "data_not_loading",
    "crash_dialog": "crash_dialog",
    "permission_denied": "permission_denied",
}
BUG_CATEGORIES = list(CATEGORY_TO_BUG_FLAG)

# 8 combinations of (width, height, theme, zoom) per bug category.
VARIANTS = [
    (375, 667, "light", 1.00),
    (375, 667, "dark", 1.00),
    (375, 667, "light", 1.25),
    (768, 1024, "light", 1.00),
    (768, 1024, "dark", 1.25),
    (1280, 800, "light", 1.00),
    (1280, 800, "dark", 1.00),
    (1280, 800, "light", 1.25),
]

# 8 "other" screenshots: healthy pages and an unrelated informational page.
OTHER_PAGES = [
    ("dashboard", "light", 1280, 800, 1.00),
    ("orders", "light", 1280, 800, 1.00),
    ("profile", "dark", 768, 1024, 1.00),
    ("login", "light", 375, 667, 1.00),
    ("about", "light", 1280, 800, 1.00),
    ("dashboard", "dark", 1280, 800, 1.25),
    ("orders", "dark", 375, 667, 1.00),
    ("about", "dark", 768, 1024, 1.00),
]

PORT = 8765
BASE = f"http://127.0.0.1:{PORT}"


def free_port(port: int) -> bool:
    with socket.socket() as s:
        try:
            s.bind(("127.0.0.1", port))
            return True
        except OSError:
            return False


def start_demo_app() -> subprocess.Popen:
    if not free_port(PORT):
        sys.exit(f"port {PORT} is already in use; stop the other server first")
    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "demo_app.app:app",
         "--port", str(PORT), "--log-level", "warning"],
        cwd=ROOT,
    )
    import httpx

    deadline = time.time() + 30
    while time.time() < deadline:
        if proc.poll() is not None:
            sys.exit("demo app process exited during startup")
        try:
            if httpx.get(BASE + "/", timeout=1).status_code == 200:
                return proc
        except Exception:
            time.sleep(0.2)
    proc.kill()
    sys.exit("demo app did not come up within 30 s")


def shoot(page, url: str, width: int, height: int, path: Path) -> None:
    page.set_viewport_size({"width": width, "height": height})
    page.goto(url)
    page.wait_for_timeout(150)
    page.screenshot(path=str(path))


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    issue_id_by_category = {
        issue["category"]: issue["id"] for issue in KNOWN_ISSUES
    }
    if NEW_BUG_CATEGORY in issue_id_by_category:
        sys.exit("NEW_BUG_CATEGORY must not be present in known_issues.json")

    server = start_demo_app()
    rows = []
    try:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as p:
            browser = p.chromium.launch()
            pg = browser.new_page()

            for category in BUG_CATEGORIES:
                flag = CATEGORY_TO_BUG_FLAG[category]
                url = f"{BASE}/?bug={flag}"
                for i, (w, h, theme, zoom) in enumerate(VARIANTS, start=1):
                    name = f"{category}_{i:02d}.png"
                    shoot(pg, f"{url}&theme={theme}&zoom={zoom}", w, h, OUT_DIR / name)
                    rows.append({
                        "filename": name,
                        "category": category,
                        "known_issue_id": issue_id_by_category.get(category, ""),
                        "is_new_bug": "true" if category == NEW_BUG_CATEGORY else "false",
                    })

            for i, (page_name, theme, w, h, zoom) in enumerate(OTHER_PAGES, start=1):
                name = f"other_{i:02d}.png"
                shoot(pg, f"{BASE}/?page={page_name}&theme={theme}&zoom={zoom}",
                      w, h, OUT_DIR / name)
                rows.append({
                    "filename": name,
                    "category": "other",
                    "known_issue_id": "",
                    "is_new_bug": "false",
                })

            browser.close()
    finally:
        server.terminate()
        server.wait(timeout=10)

    csv_path = OUT_DIR / "labels.csv"
    with csv_path.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["filename", "category",
                                               "known_issue_id", "is_new_bug"])
        writer.writeheader()
        writer.writerows(rows)

    pngs = sorted(OUT_DIR.glob("*.png"))
    stale = [p.name for p in pngs if p.name not in {r["filename"] for r in rows}]
    print(f"wrote {len(pngs)} PNGs and {len(rows)} rows to {csv_path}")
    if stale:
        print(f"WARNING: stale PNGs not in labels.csv: {stale}")


if __name__ == "__main__":
    main()
