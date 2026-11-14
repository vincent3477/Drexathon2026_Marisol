"""Mock intake site: upload a bug screenshot, run the triage pipeline.

Run:  uvicorn app.main:app  (MOCK_MODE=1 for a keyless demo)

Uploads are validated (image type, 4 MiB limit), kept in memory only
(never written to disk), and stored in the capped in-memory ReportStore.
API keys are read server-side from the environment only; nothing here
sends secrets to the browser.
"""

from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from . import config, mocklib, pipeline
from .store import ReportStore, DEFAULT_CAP

app = FastAPI(title="Screenshot-to-Repro intake")
store = ReportStore(cap=DEFAULT_CAP)

MAX_UPLOAD_BYTES = 4 * 1024 * 1024  # matches the Drex inline-image limit
ALLOWED_TYPES = {"image/png", "image/jpeg", "image/webp"}

MODES = pipeline.MODES
STATIC = Path(__file__).parent / "static"
app.mount("/static", StaticFiles(directory=STATIC), name="static")


@app.get("/")
def index():
    return FileResponse(STATIC / "index.html")


@app.get("/api/config")
def get_config():
    """Public (non-secret) runtime info so the UI can label mock results."""
    return {
        "mock": config.MOCK_MODE,
        "mode_options": list(MODES),
        "default_threshold": config.DEFAULT_THRESHOLD,
        "frontier_provider": config.FRONTIER_PROVIDER,
        "store_cap": DEFAULT_CAP,
    }


@app.get("/api/known-issues")
def known_issues():
    return JSONResponse(pipeline.load_known_issues())


@app.post("/api/triage")
async def api_triage(
    file: UploadFile = File(...),
    mode: str = Form(pipeline.MODES[2]),
    threshold: float = Form(config.DEFAULT_THRESHOLD),
    description: str = Form(""),
):
    if mode not in MODES:
        raise HTTPException(422, f"mode must be one of {list(MODES)}")
    if not (0.0 < threshold < 1.0):
        raise HTTPException(422, "threshold must be between 0 and 1")

    content_type = (file.content_type or "").lower()
    if content_type not in ALLOWED_TYPES:
        raise HTTPException(415, "only image/png, image/jpeg or image/webp uploads")

    data = await file.read()
    if len(data) == 0:
        raise HTTPException(400, "empty upload")
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(413, f"upload exceeds {MAX_UPLOAD_BYTES // 1024} KiB limit")

    # Image bytes stay in memory; the pipeline accepts raw bytes, no temp files.
    # Mock only: let the deterministic mock use the original file name for
    # plausible results. Never used for validation or in real mode.
    hint_token = mocklib.MOCK_HINT.set(file.filename or None) if config.MOCK_MODE else None
    try:
        result = pipeline.triage(data, description, mode, threshold)
    finally:
        if hint_token is not None:
            mocklib.MOCK_HINT.reset(hint_token)

    record = {
        "created_at": None,  # store assigns
        "content_type": content_type,
        "image_bytes": data,
        "description": description,
        "result": result,
    }
    record = store.add(record)

    public = dict(record)
    public.pop("image_bytes", None)
    return JSONResponse(public)


@app.get("/api/reports")
def api_reports():
    return JSONResponse(store.list())


@app.get("/api/reports/{report_id}")
def api_report(report_id: str):
    record = store.get(report_id)
    if record is None:
        raise HTTPException(404, "no such report")
    public = dict(record)
    public.pop("image_bytes", None)
    public.pop("content_type", None)
    return JSONResponse(public)


@app.get("/api/reports/{report_id}/image")
def api_report_image(report_id: str):
    record = store.get(report_id)
    if record is None:
        raise HTTPException(404, "no such report")
    return Response(content=record["image_bytes"],
                    media_type=record["content_type"])
