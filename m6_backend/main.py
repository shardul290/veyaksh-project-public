from __future__ import annotations

import os
import json
import re
import sqlite3
import subprocess
import sys
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

import cv2
from fastapi import FastAPI, Header, HTTPException, Query, Request
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field, field_validator

from .database import DB_PATH as DEFAULT_DB_PATH, initialize_database

ROOT = Path(__file__).resolve().parent.parent
BACKEND_DIR = Path(__file__).resolve().parent
DASHBOARD_FILE = ROOT / "m6_dashboard" / "index.html"
MAP_DASHBOARD_FILE = ROOT / "m6_dashboard" / "map.html"
PRIVACY_FILE = ROOT / "m6_dashboard" / "privacy.html"
TERMS_FILE = ROOT / "m6_dashboard" / "terms.html"
FAVICON_FILE = ROOT / "m6_dashboard" / "favicon.svg"
BRAND_LOGO_FILE = ROOT / "m6_dashboard" / "veyaksh-logo.jpg"
DB_PATH = Path(os.environ.get("EVENTS_DB_PATH", DEFAULT_DB_PATH))
MEDIA_DIR = BACKEND_DIR / "media"
UPLOAD_DIR = ROOT / "data" / "dashboard_uploads"
JOB_DIR = ROOT / "runs" / "dashboard_jobs"
MODEL_FILE = ROOT / "models" / "best.pt"
ZONE_DIR = ROOT / "configs" / "zones"
MAX_VIDEO_BYTES = 1024 * 1024 * 1024
ALLOWED_VIDEO_SUFFIXES = {".mp4", ".mov", ".m4v", ".avi", ".mkv", ".webm"}
VIDEO_JOBS: dict[str, dict[str, Any]] = {}
VIDEO_JOBS_LOCK = threading.Lock()
VIDEO_JOB_POOL = ThreadPoolExecutor(max_workers=1, thread_name_prefix="veyaksh-video")

app = FastAPI(title="VEYAKSH Traffic Events API", version="2.0.0")
app.mount("/media", StaticFiles(directory=MEDIA_DIR), name="media")
app.mount("/dashboard-assets", StaticFiles(directory=ROOT / "m6_dashboard"), name="dashboard-assets")


@app.middleware("http")
async def prevent_stale_dashboard(request: Request, call_next):
    response = await call_next(request)
    if request.url.path in {"/", "/map", "/history"} or request.url.path.startswith("/dashboard-assets/"):
        response.headers["Cache-Control"] = "no-store"
    return response


def _job_snapshot(job_id: str) -> dict[str, Any] | None:
    with VIDEO_JOBS_LOCK:
        job = VIDEO_JOBS.get(job_id)
        if job is None:
            return None
        result = {key: value for key, value in job.items() if key not in {
            "video_path", "output_dir", "annotated_path", "zone_path", "api_url",
            "tracks_path", "events_path",
        }}
        if job["state"] == "succeeded":
            result["detections_url"] = f"/video-jobs/{job_id}/detections"
        return result


def _run_video_job(job_id: str) -> None:
    with VIDEO_JOBS_LOCK:
        job = VIDEO_JOBS[job_id]
        job["state"] = "running"
        video_path = Path(job["video_path"])
        output_dir = Path(job["output_dir"])
        zone_path = Path(job["zone_path"])
        device = str(job["device"])
        api_url = str(job["api_url"])

    command = [
        sys.executable, str(ROOT / "scripts" / "run_video_publish.py"),
        "--video", str(video_path), "--model", str(MODEL_FILE),
        "--zones", str(zone_path), "--output-dir", str(output_dir),
        "--device", device, "--api-url", api_url,
    ]
    tail: list[str] = []
    try:
        process = subprocess.Popen(
            command, cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, encoding="utf-8", errors="replace", bufsize=1,
        )
        assert process.stdout is not None
        for line in process.stdout:
            line = line.rstrip()
            if line:
                tail.append(line)
                del tail[:-30]
            progress = re.search(r"Processed (\d+) sampled frames", line)
            if progress is None:
                progress = re.search(r"sampled frames=(\d+)", line)
            if progress:
                with VIDEO_JOBS_LOCK:
                    VIDEO_JOBS[job_id]["processed_frames"] = int(progress.group(1))
        return_code = process.wait()
        if return_code != 0:
            raise RuntimeError("\n".join(tail[-12:]) or f"Pipeline exited with code {return_code}")
        tracks_path = output_dir / f"{video_path.stem}_tracks.jsonl"
        events_path = output_dir / f"{video_path.stem}_events.jsonl"
        if not tracks_path.is_file() or not events_path.is_file():
            raise RuntimeError("Pipeline finished without producing tracking and event outputs.")
        event_count = sum(1 for line in events_path.read_text(encoding="utf-8").splitlines() if line.strip()) if events_path.exists() else 0
        with VIDEO_JOBS_LOCK:
            VIDEO_JOBS[job_id].update({
                "state": "succeeded", "event_count": event_count,
                "tracks_path": str(tracks_path), "events_path": str(events_path),
                "log_tail": tail[-12:], "error": None,
            })
    except Exception as exc:
        with VIDEO_JOBS_LOCK:
            VIDEO_JOBS[job_id].update({
                "state": "failed", "error": str(exc), "log_tail": tail[-12:],
            })


class PlateIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str = Field(min_length=1)
    conf: float = Field(ge=0, le=1)
    img: str = Field(min_length=1)


class EventIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    node_id: str = Field(pattern=r"^N[0-9]{2}$")
    cam_id: str = Field(pattern=r"^C[0-9]+$")
    ts: str
    type: Literal["wrong_way", "encroachment"]
    track_id: int = Field(ge=0)
    vehicle_class: Literal[
        "car", "two_wheeler", "auto", "e_rickshaw", "bus_truck", "cart", "pedestrian", "other"
    ] = Field(alias="class")
    conf: float = Field(ge=0, le=1)
    zone_id: str = Field(min_length=1)
    plate: PlateIn | None
    clip: str = Field(min_length=1)
    snapshot: str | None = None
    video_ts_s: float | None = Field(default=None, ge=0)

    @field_validator("ts")
    @classmethod
    def validate_timestamp(cls, value: str) -> str:
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValueError("ts must be an ISO 8601 date-time") from exc
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            raise ValueError("ts must include a timezone offset")
        return value


def connect() -> sqlite3.Connection:
    initialize_database(DB_PATH)
    db = sqlite3.connect(DB_PATH, timeout=10)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys = ON")
    return db


@app.get("/", include_in_schema=False)
def dashboard() -> FileResponse:
    return FileResponse(DASHBOARD_FILE)


@app.get("/map", include_in_schema=False)
def map_dashboard() -> FileResponse:
    return FileResponse(DASHBOARD_FILE)


@app.get("/history", include_in_schema=False)
def history_page() -> FileResponse:
    return FileResponse(DASHBOARD_FILE)


@app.get("/privacy", include_in_schema=False)
def privacy_policy() -> FileResponse:
    return FileResponse(PRIVACY_FILE)


@app.get("/terms", include_in_schema=False)
def terms_and_conditions() -> FileResponse:
    return FileResponse(TERMS_FILE)


@app.get("/favicon.svg", include_in_schema=False)
def favicon() -> FileResponse:
    return FileResponse(FAVICON_FILE, media_type="image/svg+xml")


@app.get("/branding/veyaksh-logo.jpg", include_in_schema=False)
def brand_logo() -> FileResponse:
    return FileResponse(BRAND_LOGO_FILE, media_type="image/jpeg")


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/video-jobs", status_code=202)
async def create_video_job(
    request: Request,
    zone_config: str = Query("configs/zones/C01_encroachment_test.json"),
    device: str = Query("cpu"),
    x_video_filename: str | None = Header(default=None),
) -> dict[str, Any]:
    """Accept a local video and run YOLO/ByteTrack, M3 rules, M5 publishing, and annotated export."""
    filename = Path(x_video_filename or "video.mp4").name
    suffix = Path(filename).suffix.lower()
    if suffix not in ALLOWED_VIDEO_SUFFIXES:
        raise HTTPException(status_code=415, detail="Upload an MP4, MOV, M4V, AVI, MKV, or WebM video.")
    if device != "cpu" and not device.isdigit():
        raise HTTPException(status_code=422, detail="device must be 'cpu' or a CUDA index such as '0'.")
    if not MODEL_FILE.is_file():
        raise HTTPException(status_code=503, detail=f"YOLO checkpoint is missing: {MODEL_FILE}")
    if Path(zone_config).is_absolute():
        raise HTTPException(status_code=422, detail="zone_config must be a project-relative path under configs/zones/.")
    zone_path = (ROOT / zone_config).resolve()
    try:
        zone_path.relative_to(ZONE_DIR.resolve())
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="zone_config must be inside configs/zones/.") from exc
    if not zone_path.is_file() or zone_path.suffix.lower() != ".json":
        raise HTTPException(status_code=404, detail=f"Zone configuration not found: {zone_config}")
    try:
        zone_data = json.loads(zone_path.read_text(encoding="utf-8-sig"))
        if not all(isinstance(zone_data.get(key), str) and zone_data[key] for key in ("node_id", "cam_id")):
            raise ValueError("zone config must include node_id and cam_id")
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=f"Invalid zone configuration: {exc}") from exc

    with VIDEO_JOBS_LOCK:
        if any(job["state"] in {"queued", "running"} for job in VIDEO_JOBS.values()):
            raise HTTPException(status_code=409, detail="A video is already being processed. Wait for it to finish.")

    job_id = uuid.uuid4().hex
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    upload_path = UPLOAD_DIR / f"{job_id}{suffix}"
    size = 0
    try:
        with upload_path.open("wb") as destination:
            async for chunk in request.stream():
                if not chunk:
                    continue
                size += len(chunk)
                if size > MAX_VIDEO_BYTES:
                    raise HTTPException(status_code=413, detail="Maximum uploaded video size is 1 GiB.")
                destination.write(chunk)
    except Exception:
        upload_path.unlink(missing_ok=True)
        raise
    if size == 0:
        upload_path.unlink(missing_ok=True)
        raise HTTPException(status_code=400, detail="The uploaded file is empty.")

    capture = cv2.VideoCapture(str(upload_path))
    video_fps = float(capture.get(cv2.CAP_PROP_FPS))
    video_frames = int(capture.get(cv2.CAP_PROP_FRAME_COUNT))
    can_read_video = capture.isOpened()
    capture.release()
    if not can_read_video or video_fps <= 0 or video_frames <= 0:
        upload_path.unlink(missing_ok=True)
        raise HTTPException(status_code=422, detail="The uploaded file could not be decoded as a video.")

    output_dir = JOB_DIR / job_id
    with VIDEO_JOBS_LOCK:
        VIDEO_JOBS[job_id] = {
            "job_id": job_id, "filename": filename,
            "state": "queued", "processed_frames": 0, "event_count": None,
            "error": None, "log_tail": [], "video_path": str(upload_path),
            "output_dir": str(output_dir), "fps": video_fps, "frame_count": video_frames,
            "zone_path": str(zone_path), "device": device,
            "api_url": str(request.base_url).rstrip("/"),
        }
    VIDEO_JOB_POOL.submit(_run_video_job, job_id)
    return _job_snapshot(job_id) or {"job_id": job_id, "state": "queued"}


@app.get("/video-jobs/{job_id}")
def get_video_job(job_id: str) -> dict[str, Any]:
    result = _job_snapshot(job_id)
    if result is None:
        raise HTTPException(status_code=404, detail="Video job not found.")
    return result


@app.get("/video-jobs/{job_id}/detections")
def get_video_job_detections(job_id: str) -> dict[str, Any]:
    result = _job_snapshot(job_id)
    if result is None:
        raise HTTPException(status_code=404, detail="Video job not found.")
    if result["state"] != "succeeded":
        raise HTTPException(status_code=409, detail="Detection results are not ready yet.")
    with VIDEO_JOBS_LOCK:
        tracks_path = Path(VIDEO_JOBS[job_id]["tracks_path"])
        events_path = Path(VIDEO_JOBS[job_id]["events_path"])
        fps = float(VIDEO_JOBS[job_id]["fps"])
        frame_count = int(VIDEO_JOBS[job_id]["frame_count"])

    def read_jsonl(path: Path) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        with path.open(encoding="utf-8") as stream:
            for line in stream:
                if line.strip():
                    value = json.loads(line)
                    if isinstance(value, dict):
                        rows.append(value)
        return rows

    try:
        detections = read_jsonl(tracks_path)
        events = read_jsonl(events_path)
    except (OSError, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=500, detail=f"Could not load video job results: {exc}") from exc
    return {"fps": fps, "frame_count": frame_count, "detections": detections, "events": events}


@app.post("/events", status_code=201)
def create_event(event: EventIn) -> dict[str, int]:
    if {"N01": "C01", "N02": "C02"}.get(event.node_id) != event.cam_id:
        raise HTTPException(422, "Use the surveyed camera pair N01/C01 or N02/C02")
    with connect() as db:
        node = db.execute("SELECT 1 FROM nodes WHERE node_id = ?", (event.node_id,)).fetchone()
        if node is None:
            raise HTTPException(status_code=422, detail=f"Unknown node_id: {event.node_id}")

        plate = event.plate
        cursor = db.execute(
            """INSERT INTO events
               (node_id, cam_id, ts, type, track_id, class, conf, zone_id,
                plate_text, plate_conf, plate_img, clip, snapshot, video_ts_s)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                event.node_id, event.cam_id, event.ts, event.type, event.track_id,
                event.vehicle_class, event.conf, event.zone_id,
                plate.text if plate else None,
                plate.conf if plate else None,
                plate.img if plate else None,
                event.clip,
                event.snapshot,
                event.video_ts_s,
            ),
        )
        event_id = cursor.lastrowid
    return {"id": event_id}


@app.get("/events")
def list_events(
    since_id: int | None = Query(default=None, ge=0),
    type: Literal["wrong_way", "encroachment"] | None = None,
    node_id: str | None = Query(default=None, pattern=r"^N[0-9]{2}$"),
    limit: int = Query(default=100, ge=1, le=500),
    before_id: int | None = Query(default=None, ge=1),
    status: Literal["pending", "false_alert", "action_taken", "authority_requested"] | None = None,
    active: bool = False,
) -> list[dict[str, object]]:
    clauses: list[str] = []
    values: list[object] = []
    if since_id is not None:
        clauses.append("id > ?")
        values.append(since_id)
    if type is not None:
        clauses.append("type = ?")
        values.append(type)
    if node_id is not None:
        clauses.append("node_id = ?")
        values.append(node_id)
    if before_id is not None:
        clauses.append("id < ?")
        values.append(before_id)
    if status is not None:
        clauses.append("status = ?")
        values.append(status)
    if active:
        clauses.append("archived = 0")
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    values.append(limit)

    with connect() as db:
        rows = db.execute(
            f"SELECT * FROM events {where} ORDER BY id DESC LIMIT ?", values
        ).fetchall()
    result: list[dict[str, object]] = []
    for row in rows:
        plate = None
        if row["plate_text"] is not None:
            plate = {
                "text": row["plate_text"],
                "conf": row["plate_conf"],
                "img": row["plate_img"],
            }
        result.append({
            "id": row["id"], "node_id": row["node_id"], "cam_id": row["cam_id"],
            "ts": row["ts"], "type": row["type"], "track_id": row["track_id"],
            "class": row["class"], "conf": row["conf"], "zone_id": row["zone_id"],
            "plate": plate, "clip": row["clip"], "snapshot": row["snapshot"],
            "received_at": row["received_at"],
            "status": row["status"], "status_at": row["status_at"],
            "video_ts_s": row["video_ts_s"],
            "archived": bool(row["archived"]),
        })
    return result


@app.get("/nodes")
def list_nodes() -> list[dict[str, object]]:
    with connect() as db:
        rows = db.execute(
            "SELECT node_id, name, lat, lon FROM nodes WHERE node_id IN ('N01', 'N02') ORDER BY node_id"
        ).fetchall()
    return [dict(row) for row in rows]


class ReviewIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    status: Literal["false_alert", "action_taken", "authority_requested"]


@app.get("/events-summary")
def event_summary() -> dict[str, int]:
    result = dict.fromkeys(("pending", "false_alert", "action_taken", "authority_requested"), 0)
    with connect() as db:
        for row in db.execute("SELECT status, COUNT(*) AS total FROM events WHERE archived = 0 GROUP BY status"):
            result[row["status"]] = row["total"]
    return result


@app.patch("/events/{event_id}/status")
def review_event(event_id: int, review: ReviewIn) -> dict[str, object]:
    with connect() as db:
        db.execute("BEGIN IMMEDIATE")
        event = db.execute("SELECT status, status_at, archived FROM events WHERE id = ?", (event_id,)).fetchone()
        if event is None:
            raise HTTPException(404, "Event not found")
        if event["archived"]:
            raise HTTPException(409, "Legacy evidence is archived and cannot be actioned")
        if event["status"] == review.status:
            return {"id": event_id, "status": event["status"], "status_at": event["status_at"]}
        allowed = {"pending": {"false_alert", "action_taken"}, "action_taken": {"authority_requested"}}
        if review.status not in allowed.get(event["status"], set()):
            raise HTTPException(409, "This event has already been reviewed. Refresh its status.")
        now = datetime.now(timezone.utc).isoformat(timespec="milliseconds")
        db.execute("UPDATE events SET status = ?, status_at = ? WHERE id = ?", (review.status, now, event_id))
        db.execute("INSERT INTO event_actions (event_id, status, ts) VALUES (?, ?, ?)", (event_id, review.status, now))
    return {"id": event_id, "status": review.status, "status_at": now}


@app.get("/events/{event_id}/actions")
def event_actions(event_id: int) -> list[dict[str, object]]:
    with connect() as db:
        if db.execute("SELECT 1 FROM events WHERE id = ?", (event_id,)).fetchone() is None:
            raise HTTPException(404, "Event not found")
        return [dict(row) for row in db.execute(
            "SELECT status, ts FROM event_actions WHERE event_id = ? ORDER BY id", (event_id,)
        ).fetchall()]


@app.get("/cameras")
def camera_recordings() -> list[dict[str, object]]:
    config = json.loads((ROOT / "configs" / "cameras.json").read_text(encoding="utf-8"))
    for camera in config:
        available = []
        for recording in camera.get("recordings", []):
            path = (MEDIA_DIR / recording["path"]).resolve()
            if path.is_relative_to(MEDIA_DIR.resolve()) and path.is_file():
                available.append(recording)
        camera["recordings"] = available
    return config
