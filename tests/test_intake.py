"""Automated tests for the intake site (mock mode)."""

import io
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.store import ReportStore

ROOT = Path(__file__).resolve().parent.parent
SHOT = ROOT / "data" / "screenshots" / "auth_failure_01.png"
CRASH = ROOT / "data" / "screenshots" / "crash_dialog_01.png"

client = TestClient(app)


def post_triage(path, mode="cascade", threshold=0.7, description="",
                content_type="image/png"):
    return client.post(
        "/api/triage",
        files={"file": (path.name, path.open("rb"), content_type)},
        data={"mode": mode, "threshold": str(threshold), "description": description},
    )


def test_get_index_and_config():
    assert client.get("/").status_code == 200
    cfg = client.get("/api/config").json()
    assert cfg["mock"] is True
    assert cfg["mode_options"] == ["frontier_only", "drex_only", "cascade"]


def test_post_triage_runs_pipeline_and_stores_report():
    resp = post_triage(SHOT, description="login fails with 401")
    assert resp.status_code == 200, resp.text
    record = resp.json()
    assert "image_bytes" not in record  # bytes never leave the store
    result = record["result"]
    assert result["mock"] is True
    assert result["mode"] == "cascade"
    for step in ("gate", "category", "match"):
        assert step in result["steps"]
        assert result["steps"][step]["decision"]
    assert record["id"]
    assert record["description"] == "login fails with 401"

    # stored and listed, newest first, without image bytes
    reports = client.get("/api/reports").json()
    assert reports[0]["id"] == record["id"]
    assert "image_bytes" not in reports[0]

    detail = client.get(f"/api/reports/{record['id']}").json()
    assert detail["result"]["mode"] == "cascade"

    image = client.get(f"/api/reports/{record['id']}/image")
    assert image.status_code == 200
    assert image.headers["content-type"] == "image/png"
    assert image.content == SHOT.read_bytes()


def test_new_bug_flow_drafts_repro():
    resp = post_triage(CRASH, mode="drex_only", description="app crashed")
    assert resp.status_code == 200, resp.text
    result = resp.json()["result"]
    assert result["new_bug"] is True
    assert result["steps"]["match"]["decision"] == "none"
    assert result["repro_draft"] is not None
    assert "mock" in result["repro_draft"]["text"].lower()
    assert result["cost_usd"]["draft"] > 0


def test_rejects_non_image_upload(tmp_path):
    txt = tmp_path / "notes.txt"
    txt.write_text("not an image")
    resp = client.post(
        "/api/triage",
        files={"file": ("notes.txt", txt.open("rb"), "text/plain")},
        data={"mode": "cascade", "threshold": "0.7", "description": ""},
    )
    assert resp.status_code == 415


def test_rejects_oversized_upload(tmp_path):
    big = tmp_path / "big.png"
    big.write_bytes(b"\x89PNG\r\n" + b"\x00" * (5 * 1024 * 1024))
    resp = client.post(
        "/api/triage",
        files={"file": ("big.png", big.open("rb"), "image/png")},
        data={"mode": "cascade", "threshold": "0.7", "description": ""},
    )
    assert resp.status_code == 413


def test_rejects_bad_mode_and_threshold():
    resp = client.post(
        "/api/triage",
        files={"file": (SHOT.name, SHOT.open("rb"), "image/png")},
        data={"mode": "sometimes", "threshold": "0.7", "description": ""},
    )
    assert resp.status_code == 422
    resp = client.post(
        "/api/triage",
        files={"file": (SHOT.name, SHOT.open("rb"), "image/png")},
        data={"mode": "cascade", "threshold": "1.5", "description": ""},
    )
    assert resp.status_code == 422


def test_report_not_found():
    assert client.get("/api/reports/nope").status_code == 404
    assert client.get("/api/reports/nope/image").status_code == 404


def test_store_cap_drops_oldest():
    s = ReportStore(cap=3)
    ids = [s.add({"payload": i})["id"] for i in range(4)]
    listing = s.list()
    assert [r["id"] for r in listing] == [ids[3], ids[2], ids[1]]
    assert s.get(ids[0]) is None
    assert s.get(ids[3]) is not None
