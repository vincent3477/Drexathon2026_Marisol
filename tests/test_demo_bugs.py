"""Automated repro tests for the six seeded bugs in the demo app.

Each test hits the demo app through FastAPI's test client with the bug's
query flag and asserts the buggy behavior is present (ground truth for the
later repro stage). The last test checks clean mode shows none of the bugs.
"""

from fastapi.testclient import TestClient

from demo_app.app import app

client = TestClient(app)


def get(url: str) -> str:
    resp = client.get(url)
    assert resp.status_code == 200
    return resp.text


def test_bug_login_401():
    html = get("/?bug=login_401")
    assert "401 Unauthorized" in html
    assert 'data-bug="login_401"' in html
    # correct credentials are still shown in the form
    assert "alice@example.com" in html and "correct-horse" in html


def test_bug_network_error():
    html = get("/?bug=network_error")
    assert "Failed to fetch" in html
    assert "ECONNREFUSED" in html
    assert 'data-bug="network_error"' in html


def test_bug_ui_layout_glitch():
    html = get("/?bug=ui_layout_glitch")
    assert 'data-bug="ui_layout_glitch"' in html
    # the glitch is structural: a 980px table plus overlapping legend inside
    # a card that is narrower at mobile widths
    assert 'class="glitch-table"' in html
    assert 'class="glitch-legend"' in html
    assert 'class="glitch-strip"' in html


def test_bug_data_not_loading():
    html = get("/?bug=data_not_loading")
    assert 'data-bug="data_not_loading"' in html
    assert 'class="spinner"' in html
    assert "Loading orders" in html


def test_bug_crash_dialog():
    html = get("/?bug=crash_dialog")
    assert 'data-bug="crash_dialog"' in html
    assert "Uncaught TypeError" in html
    assert 'class="stack-trace"' in html


def test_bug_permission_denied():
    html = get("/?bug=permission_denied")
    assert 'data-bug="permission_denied"' in html
    assert "403 Forbidden" in html
    assert "Permission denied" in html


def test_clean_mode_has_no_bugs():
    bug_markers = (
        "401 Unauthorized",
        "Failed to fetch",
        "ECONNREFUSED",
        'class="glitch-table"',
        'class="spinner"',
        "Uncaught TypeError",
        "403 Forbidden",
    )
    for page in ("dashboard", "orders", "login", "profile", "about"):
        html = get(f"/?page={page}")
        for marker in bug_markers:
            assert marker not in html, f"bug marker {marker!r} in clean {page}"
