"""Seeded-bug demo app used as the source of screenshots.

A tiny FastAPI site with five pages (dashboard, orders, login, profile,
about). A `?bug=<flag>` query flag deterministically triggers one of six
seeded bugs; with no flag every page is healthy.

Supported bug flags (the six bug categories of the project):
  login_401        -> login page: 401 banner with correct credentials
  network_error    -> dashboard: "Failed to fetch / ECONNREFUSED" state
  ui_layout_glitch -> orders page: overlapping / overflowing layout
  data_not_loading -> orders page: endless spinner, no data
  crash_dialog     -> dashboard: unhandled-exception dialog with stack trace
  permission_denied-> profile page: 403 banner on a valid action

Other query flags:
  page=dashboard|orders|login|profile|about   (default: dashboard)
  theme=dark|light                             (default: light)
  zoom=1.0|1.25|...                             (default: 1.0)
"""

from fastapi import FastAPI, Query
from fastapi.responses import HTMLResponse

app = FastAPI(title="Demo site with seeded bugs")

# Each bug flag forces its own page, so ?bug=X is fully deterministic.
BUG_PAGES = {
    "login_401": "login",
    "network_error": "dashboard",
    "ui_layout_glitch": "orders",
    "data_not_loading": "orders",
    "crash_dialog": "dashboard",
    "permission_denied": "profile",
}
BUG_FLAGS = set(BUG_PAGES)

PAGES = ("dashboard", "orders", "login", "profile", "about")

CSS = """
:root {
  --bg: #f4f6f8; --card: #ffffff; --text: #111827; --muted: #4b5563;
  --accent: #1d4ed8; --border: #d1d5db;
  --error-bg: #fee2e2; --error: #b91c1c; --ok-bg: #dcfce7; --ok: #15803d;
}
[data-theme="dark"] {
  --bg: #0f1115; --card: #1a1d24; --text: #f3f4f6; --muted: #9ca3af;
  --accent: #60a5fa; --border: #374151;
  --error-bg: #3b1414; --error: #f87171; --ok-bg: #14301f; --ok: #4ade80;
}
* { box-sizing: border-box; }
body { margin: 0; background: var(--bg); color: var(--text);
       font-family: -apple-system, "Segoe UI", Helvetica, Arial, sans-serif; }
header { display: flex; align-items: center; gap: 16px; padding: 14px 24px;
         background: var(--card); border-bottom: 1px solid var(--border); }
header h1 { font-size: 18px; margin: 0; }
nav { display: flex; gap: 12px; flex: 1; }
nav a { color: var(--accent); text-decoration: none; font-size: 15px; }
main { padding: 24px; max-width: 1000px; margin: 0 auto; }
.card { background: var(--card); border: 1px solid var(--border);
        border-radius: 10px; padding: 20px; margin-bottom: 18px; }
.card h2 { margin-top: 0; font-size: 18px; }
.banner { border-radius: 8px; padding: 12px 16px; margin: 12px 0; font-size: 15px; }
.banner.error { background: var(--error-bg); color: var(--error); font-weight: 600; }
.banner.ok { background: var(--ok-bg); color: var(--ok); }
table { width: 100%; border-collapse: collapse; font-size: 15px; }
th, td { border-bottom: 1px solid var(--border); padding: 8px 10px; text-align: left; }
.stat { display: inline-block; background: var(--card); border: 1px solid var(--border);
        border-radius: 10px; padding: 14px 18px; margin: 0 12px 12px 0; font-size: 15px; }
.stat b { display: block; font-size: 24px; }
input[type=text], input[type=password] { display: block; width: 100%; margin: 6px 0 14px;
  padding: 10px; font-size: 15px; border: 1px solid var(--border); border-radius: 8px;
  background: var(--bg); color: var(--text); }
button { background: var(--accent); color: white; border: 0; border-radius: 8px;
         padding: 10px 18px; font-size: 15px; cursor: pointer; }
/* --- bug: ui_layout_glitch --- */
.glitch-wrap { position: relative; }
.glitch-table { width: 980px; table-layout: fixed; }
.glitch-legend { position: absolute; left: 220px; top: 40px; width: 320px;
  background: var(--card); border: 1px solid var(--border); border-radius: 10px;
  padding: 12px; z-index: 3; }
.glitch-strip { width: 1400px; height: 18px; background: var(--accent);
  border-radius: 4px; margin: 30px 0; }
/* --- bug: data_not_loading --- */
.spinner { width: 46px; height: 46px; border: 6px solid var(--border);
  border-top-color: var(--accent); border-radius: 50%; }
/* --- bug: crash_dialog --- */
.crash-overlay { position: fixed; inset: 0; background: rgba(0,0,0,0.55);
  display: flex; align-items: center; justify-content: center; z-index: 10; }
.crash-dialog { background: var(--card); border: 2px solid var(--error);
  border-radius: 12px; padding: 24px; width: min(680px, 92%); }
.crash-dialog h2 { color: var(--error); margin-top: 0; }
.stack-trace { background: var(--bg); border: 1px solid var(--border); border-radius: 8px;
  padding: 12px; font-family: ui-monospace, Menlo, monospace; font-size: 13px;
  white-space: pre-wrap; color: var(--muted); }
"""


def _header(page: str) -> str:
    links = []
    for p in ("dashboard", "orders", "login", "profile"):
        style = ' style="font-weight:700"' if p == page else ""
        links.append(f'<a href="/?page={p}"{style}>{p.capitalize()}</a>')
    return (
        "<header><h1>Demo Co.</h1><nav>"
        + "".join(links)
        + '<button id="theme-toggle" type="button">Toggle theme</button></nav></header>'
    )


def _dashboard_html(bug: str | None) -> str:
    if bug == "network_error":
        return f"""
{_header("dashboard")}
<main>
  <div class="card">
    <h2>Dashboard</h2>
    <div class="banner error" data-bug="network_error">
      Failed to fetch: ECONNREFUSED &mdash; could not load dashboard data
      from <code>api.example.com:5432</code>. Retry in a few seconds.
    </div>
  </div>
</main>"""
    if bug == "crash_dialog":
        return f"""
{_header("dashboard")}
<main>
  <div class="card"><h2>Dashboard</h2><p>Loading overview&hellip;</p></div>
</main>
<div class="crash-overlay" data-bug="crash_dialog">
  <div class="crash-dialog" role="alertdialog" aria-modal="true">
    <h2>&#128165; Uncaught exception</h2>
    <p><b>Uncaught TypeError:</b> Cannot read properties of <i>undefined</i>
       (reading 'map')</p>
    <pre class="stack-trace">at renderOverview (app.js:42:15)
at dispatchRender (runtime.js:1187:9)
at main (app.js:108:3)
Error reported automatically &mdash; the page is unusable.</pre>
    <button type="button">Restart application</button>
  </div>
</div>"""
    return f"""
{_header("dashboard")}
<main>
  <div class="card">
    <h2>Dashboard</h2>
    <div class="stat"><b>1,284</b> orders this month</div>
    <div class="stat"><b>99.98%</b> uptime</div>
    <div class="stat"><b>42</b> active users</div>
    <div class="banner ok">All systems operational</div>
  </div>
</main>"""


def _orders_html(bug: str | None) -> str:
    rows = (
        "<tr><td>#1001</td><td>Acme Corp</td><td>$4,210</td><td>Paid</td></tr>"
        "<tr><td>#1002</td><td>Globex</td><td>$980</td><td>Paid</td></tr>"
        "<tr><td>#1003</td><td>Initech</td><td>$1,545</td><td>Pending</td></tr>"
    )
    if bug == "data_not_loading":
        return f"""
{_header("orders")}
<main>
  <div class="card" data-bug="data_not_loading">
    <h2>Orders</h2>
    <div style="display:flex;align-items:center;gap:16px;padding:24px 0">
      <div class="spinner" role="status" aria-label="Loading"></div>
      <span style="font-size:16px">Loading orders&hellip; this may take a moment.</span>
    </div>
  </div>
</main>"""
    if bug == "ui_layout_glitch":
        return f"""
{_header("orders")}
<main>
  <div class="card glitch-wrap" data-bug="ui_layout_glitch">
    <h2>Orders &amp; legend</h2>
    <div class="glitch-legend"><b>Legend</b><br>Paid &middot; Pending &middot; Refunded
      &middot; Disputed &middot; Open</div>
    <table class="glitch-table">
      <tr><th>ID</th><th>Customer</th><th>Amount</th><th>Status</th><th>Notes</th><th>Region</th><th>Channel</th></tr>
      {rows}
    </table>
    <div class="glitch-strip"></div>
  </div>
</main>"""
    return f"""
{_header("orders")}
<main>
  <div class="card">
    <h2>Orders</h2>
    <table>
      <tr><th>ID</th><th>Customer</th><th>Amount</th><th>Status</th></tr>
      {rows}
    </table>
  </div>
</main>"""


def _login_html(bug: str | None) -> str:
    banner = ""
    if bug == "login_401":
        banner = (
            '<div class="banner error" data-bug="login_401">'
            "<b>401 Unauthorized</b> &mdash; Login failed. The credentials "
            "alice@example.com / (correct password) were rejected by the "
            "auth service even though they are valid.</div>"
        )
    return f"""
{_header("login")}
<main>
  <div class="card" style="max-width:440px;margin:40px auto">
    <h2>Sign in</h2>
    {banner}
    <label for="email">Email</label>
    <input type="text" id="email" value="alice@example.com">
    <label for="password">Password</label>
    <input type="password" id="password" value="correct-horse">
    <button type="button">Sign in</button>
  </div>
</main>"""


def _profile_html(bug: str | None) -> str:
    banner = ""
    if bug == "permission_denied":
        banner = (
            '<div class="banner error" data-bug="permission_denied">'
            "<b>403 Forbidden</b> &mdash; Permission denied while saving your "
            "profile. Your account has editor access, so this request should "
            "have succeeded.</div>"
        )
    return f"""
{_header("profile")}
<main>
  <div class="card" style="max-width:520px">
    <h2>Profile</h2>
    {banner}
    <label for="name">Display name</label>
    <input type="text" id="name" value="Alice Example">
    <label for="role">Role</label>
    <input type="text" id="role" value="editor" disabled>
    <button type="button">Save changes</button>
  </div>
</main>"""


def _about_html() -> str:
    return f"""
{_header("about")}
<main>
  <div class="card">
    <h2>About Demo Co.</h2>
    <p>Demo Co. is a fictional company used for this demo. This page is just
       a static informational page with a team contact form placeholder.</p>
    <p>Contact: <b>hello@demo.co</b> &middot; Office hours: weekdays 9&ndash;5.</p>
  </div>
</main>"""


PAGE_HTML = {
    "dashboard": _dashboard_html,
    "orders": _orders_html,
    "login": _login_html,
    "profile": _profile_html,
}


@app.get("/", response_class=HTMLResponse)
def index(
    page: str = Query("dashboard", pattern="^(" + "|".join(PAGES) + ")$"),
    bug: str | None = Query(None),
    theme: str = Query("light", pattern="^(light|dark)$"),
    zoom: float = Query(1.0, ge=0.5, le=2.0),
):
    if bug not in BUG_FLAGS:
        bug = None  # unknown/missing flags are ignored, same as clean mode
    if bug is not None:
        page = BUG_PAGES[bug]
    if page in PAGE_HTML:
        body = PAGE_HTML[page](bug)
    else:
        body = _about_html()
    theme_attr = f' data-theme="{theme}"' if theme == "dark" else ""
    zoom_style = f' style="zoom:{zoom}"' if abs(zoom - 1.0) > 1e-9 else ""
    script = (
        "<script>"
        'document.getElementById("theme-toggle").addEventListener("click", () => {'
        "document.documentElement.toggleAttribute(\"data-theme\");} );"
        "</script>"
    )
    return (
        f"<!doctype html><html lang=\"en\"{theme_attr}{zoom_style}>"
        "<head><meta charset=\"utf-8\"><title>Demo Co.</title>"
        f"<style>{CSS}</style></head><body>{body}{script}</body></html>"
    )
