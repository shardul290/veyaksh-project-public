"""Run a clip through detection, rules, M5, and the local M6 dashboard."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
import webbrowser
from datetime import datetime
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_API = "http://127.0.0.1:8000"


def read_json(path: Path) -> dict[str, object]:
    with path.open(encoding="utf-8") as stream:
        value = json.load(stream)
    if not isinstance(value, dict):
        raise ValueError(f"Expected a JSON object in {path}")
    return value


def api_is_ready(api_url: str) -> bool:
    try:
        response = requests.get(f"{api_url.rstrip('/')}/health", timeout=2)
        return response.ok
    except requests.RequestException:
        return False


def ensure_backend(api_url: str, log_path: Path) -> bool:
    if api_is_ready(api_url):
        print(f"Backend already running at {api_url}")
        return False
    if api_url.rstrip("/") != DEFAULT_API:
        raise RuntimeError(f"No backend at {api_url}. This launcher only starts the local default API {DEFAULT_API}.")

    log_path.parent.mkdir(parents=True, exist_ok=True)
    log = log_path.open("a", encoding="utf-8")
    flags = 0
    if os.name == "nt":
        flags = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS
    process = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "m6_backend.main:app", "--host", "127.0.0.1", "--port", "8000"],
        cwd=ROOT,
        stdout=log,
        stderr=subprocess.STDOUT,
        creationflags=flags,
        start_new_session=(os.name != "nt"),
    )
    log.close()
    for _ in range(60):
        if api_is_ready(api_url):
            print(f"Started local backend at {api_url}; log: {log_path}")
            return True
        if process.poll() is not None:
            break
        time.sleep(0.5)
    raise RuntimeError(f"Could not start the backend. See {log_path}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--video", required=True, type=Path, help="Source video used for detection and evidence")
    parser.add_argument("--zones", required=True, type=Path, help="Calibrated camera zone JSON")
    parser.add_argument("--model", type=Path, default=Path("models/best.pt"), help="YOLO11s checkpoint")
    parser.add_argument("--tracks", type=Path, help="Reuse an existing ByteTrack JSONL and skip YOLO inference")
    parser.add_argument("--output-dir", type=Path, default=Path("runs/m3"))
    parser.add_argument("--device", default="cpu", help="Use cpu or a CUDA index such as 0")
    parser.add_argument("--imgsz", type=int, default=640)
    parser.add_argument("--conf", type=float, default=0.15)
    parser.add_argument("--stride", type=int, default=1)
    parser.add_argument("--api-url", default=DEFAULT_API)
    parser.add_argument("--source-start-ts", help="ISO 8601 recording start time; defaults to current local time")
    parser.add_argument("--no-open", action="store_true", help="Do not open the dashboard after publishing")
    args = parser.parse_args()

    video = args.video.resolve()
    zones_path = args.zones.resolve()
    output_dir = (ROOT / args.output_dir).resolve() if not args.output_dir.is_absolute() else args.output_dir.resolve()
    if not video.is_file():
        parser.error(f"Video not found: {video}")
    if not zones_path.is_file():
        parser.error(f"Zone configuration not found: {zones_path}")
    if args.tracks and not args.tracks.is_file():
        parser.error(f"Tracks file not found: {args.tracks}")
    if not args.tracks and not args.model.is_file():
        parser.error(f"Model checkpoint not found: {args.model}. Use --model with its local path.")
    if args.imgsz < 32 or args.stride < 1 or not 0 <= args.conf <= 1:
        parser.error("imgsz must be >=32, stride >=1, and conf in [0,1]")

    output_dir.mkdir(parents=True, exist_ok=True)
    zones = read_json(zones_path)
    node_id, cam_id = zones.get("node_id"), zones.get("cam_id")
    if not isinstance(node_id, str) or not isinstance(cam_id, str):
        parser.error("Zone config must include node_id and cam_id")

    tracks = args.tracks.resolve() if args.tracks else output_dir / f"{video.stem}_tracks.jsonl"
    events = output_dir / f"{video.stem}_events.jsonl"
    try:
        if args.tracks:
            print(f"Reusing tracking export: {tracks}")
            subprocess.run(
                [sys.executable, "-m", "m3_events.event_engine", "--tracks", str(tracks),
                 "--zones", str(zones_path), "--output", str(events)],
                cwd=ROOT, check=True,
            )
        else:
            subprocess.run(
                [sys.executable, "-m", "m3_events.run_video_pipeline", "--video", str(video),
                 "--model", str(args.model.resolve()), "--zones", str(zones_path),
                 "--output-dir", str(output_dir), "--device", args.device,
                 "--imgsz", str(args.imgsz), "--conf", str(args.conf), "--stride", str(args.stride)],
                cwd=ROOT, check=True,
            )

        with events.open(encoding="utf-8") as stream:
            event_count = sum(1 for line in stream if line.strip())
        ensure_backend(args.api_url, output_dir / "backend.log")
        if event_count:
            source_start = args.source_start_ts or datetime.now().astimezone().isoformat(timespec="seconds")
            config_path = output_dir / f"{video.stem}_m5_config.json"
            evidence_dir = f"m6_backend/media/event_evidence/{video.stem}"
            config_path.write_text(json.dumps({
                "api_url": args.api_url.rstrip("/"),
                "node_id": node_id,
                "cam_id": cam_id,
                "source_video": str(video),
                "source_start_ts": source_start,
                "evidence_dir": evidence_dir,
            }, indent=2) + "\n", encoding="utf-8")
            subprocess.run(
                [sys.executable, "m5_edge_agent/send_events.py", "--config", str(config_path), "--events", str(events)],
                cwd=ROOT, check=True,
            )
        else:
            print("No rule events were emitted; nothing to publish.")
        print(f"Dashboard: {args.api_url.rstrip('/')}/")
        print(f"Tracks: {tracks}")
        print(f"Events: {events}")
        if not args.no_open:
            webbrowser.open(f"{args.api_url.rstrip('/')}/")
        return 0
    except (OSError, ValueError, requests.RequestException, subprocess.CalledProcessError, RuntimeError) as exc:
        print(f"Video-to-dashboard run failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
