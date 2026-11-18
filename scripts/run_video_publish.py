"""Run the video pipeline and publish its resulting events to the M6 dashboard."""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import subprocess
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any

import requests


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def project_path(value: str | Path) -> Path:
    path = Path(value)
    return (PROJECT_ROOT / path).resolve() if not path.is_absolute() else path.resolve()


def validate_start_time(value: str) -> str:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("--source-start-ts must include a timezone, e.g. 2026-11-16T18:00:00+05:30")
    return parsed.isoformat(timespec="seconds")


def backup_database(db_path: Path, video_stem: str, run_stamp: str) -> Path:
    if not db_path.is_file():
        raise FileNotFoundError(f"Dashboard database not found: {db_path}")
    backup_path = db_path.with_name(f"{db_path.stem}_before_{video_stem}_{run_stamp}{db_path.suffix}")
    with sqlite3.connect(db_path, timeout=30) as source, sqlite3.connect(backup_path) as backup:
        source.backup(backup)
    return backup_path


def restore_database(backup_path: Path, db_path: Path) -> None:
    with sqlite3.connect(backup_path, timeout=30) as backup, sqlite3.connect(db_path, timeout=30) as target:
        backup.backup(target)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--video", required=True, help="Input video file")
    parser.add_argument("--model", required=True, help="YOLO checkpoint, e.g. models/best.pt")
    parser.add_argument("--zones", required=True, help="Calibrated zone JSON for this camera")
    parser.add_argument("--output-dir", required=True, help="Directory for tracks and rule-event JSONL")
    parser.add_argument("--annotated-output", help="Optional video file to save with detector boxes and track IDs")
    parser.add_argument("--device", default="cpu", help="GPU index or 'cpu' (default: cpu)")
    parser.add_argument("--imgsz", type=int, default=640)
    parser.add_argument("--conf", type=float, default=0.15)
    parser.add_argument("--stride", type=int, default=1)
    parser.add_argument("--api-url", default="http://127.0.0.1:8000")
    parser.add_argument("--source-start-ts", help="ISO 8601 clip start time with timezone; defaults to video file modification time")
    parser.add_argument(
        "--replace-dashboard-events",
        action="store_true",
        help="Back up the dashboard DB, then replace all existing events with this video's events",
    )
    args = parser.parse_args()

    video = project_path(args.video)
    model = project_path(args.model)
    zones_path = project_path(args.zones)
    output_dir = project_path(args.output_dir)
    if not video.is_file():
        parser.error(f"Video not found: {video}")
    if not model.is_file():
        parser.error(f"Model checkpoint not found: {model}")
    if not zones_path.is_file():
        parser.error(f"Zone config not found: {zones_path}")

    try:
        zones: dict[str, Any] = json.loads(zones_path.read_text(encoding="utf-8-sig"))
        node_id, cam_id = zones["node_id"], zones["cam_id"]
        if args.source_start_ts:
            source_start_ts = validate_start_time(args.source_start_ts)
        else:
            source_start_ts = datetime.fromtimestamp(video.stat().st_mtime).astimezone().isoformat(timespec="seconds")
    except (OSError, KeyError, TypeError, json.JSONDecodeError, ValueError) as exc:
        parser.error(f"Could not read zone config or clip time: {exc}")

    output_dir.mkdir(parents=True, exist_ok=True)
    tracks_path = output_dir / f"{video.stem}_tracks.jsonl"
    events_path = output_dir / f"{video.stem}_events.jsonl"
    pipeline_command = [
        sys.executable, "-m", "m3_events.run_video_pipeline",
        "--video", str(video), "--model", str(model), "--zones", str(zones_path),
        "--output-dir", str(output_dir), "--device", args.device,
        "--imgsz", str(args.imgsz), "--conf", str(args.conf), "--stride", str(args.stride),
    ]
    if args.annotated_output:
        pipeline_command.extend(["--annotated-output", str(project_path(args.annotated_output))])
    print("Running detection, tracking, and event rules...")
    try:
        subprocess.run(pipeline_command, cwd=PROJECT_ROOT, check=True)
    except subprocess.CalledProcessError as exc:
        return exc.returncode or 1

    if not events_path.is_file():
        print(f"Pipeline did not create event output: {events_path}", file=sys.stderr)
        return 1
    event_rows = [
        json.loads(line)
        for line in events_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    if not event_rows:
        print("No rule events were detected; dashboard was left unchanged.")
        return 0
    print(f"Prepared {len(event_rows)} event records: {dict(Counter(row['type'] for row in event_rows))}")

    api_url = args.api_url.rstrip("/")
    try:
        response = requests.get(f"{api_url}/health", timeout=5)
        response.raise_for_status()
    except requests.RequestException as exc:
        print(f"Dashboard API is unavailable at {api_url}: {exc}. Existing dashboard events were left unchanged.", file=sys.stderr)
        return 1

    run_stamp = datetime.now().astimezone().strftime("%Y%m%d_%H%M%S")
    evidence_rel = Path("m6_backend/media/event_evidence") / f"{video.stem}_{run_stamp}"
    evidence_dir = PROJECT_ROOT / evidence_rel
    config_path = output_dir / f"{video.stem}_m5_publish_config.json"
    m5_config = {
        "api_url": api_url,
        "node_id": node_id,
        "cam_id": cam_id,
        "source_video": str(video),
        "source_start_ts": source_start_ts,
        "evidence_dir": evidence_rel.as_posix(),
    }
    config_path.write_text(json.dumps(m5_config, indent=2) + "\n", encoding="utf-8")

    db_path = Path(os.environ.get("EVENTS_DB_PATH", PROJECT_ROOT / "m6_backend/veyaksh.db"))
    if not db_path.is_absolute():
        db_path = PROJECT_ROOT / db_path
    backup_path: Path | None = None
    if args.replace_dashboard_events:
        try:
            api_rows = requests.get(f"{api_url}/events?limit=500", timeout=10)
            api_rows.raise_for_status()
            if not isinstance(api_rows.json(), list):
                raise ValueError("Dashboard /events response was not a list")
            backup_path = backup_database(db_path, video.stem, run_stamp)
            with sqlite3.connect(db_path, timeout=30) as db:
                old_count = db.execute("SELECT COUNT(*) FROM events").fetchone()[0]
                if old_count != len(api_rows.json()):
                    raise ValueError(
                        f"Dashboard API reports {len(api_rows.json())} events but {db_path} contains {old_count}; "
                        "no rows were cleared. Check EVENTS_DB_PATH."
                    )
                db.execute("DELETE FROM events")
            print(f"Backed up dashboard DB to {backup_path}; cleared {old_count} old event rows.")
        except (OSError, sqlite3.Error, requests.RequestException, ValueError) as exc:
            print(f"Could not safely back up/clear dashboard events: {exc}", file=sys.stderr)
            return 1

    sender_command = [
        sys.executable, "-m", "m5_edge_agent.send_events",
        "--config", str(config_path), "--events", str(events_path),
    ]
    print(f"Publishing {len(event_rows)} events to {api_url}...")
    result = subprocess.run(sender_command, cwd=PROJECT_ROOT, check=False)
    if result.returncode != 0:
        if backup_path is not None:
            try:
                restore_database(backup_path, db_path)
                print(f"Upload failed; restored the previous dashboard DB from {backup_path}.", file=sys.stderr)
            except sqlite3.Error as exc:
                print(f"Upload failed and automatic DB restore failed: {exc}; backup remains at {backup_path}.", file=sys.stderr)
        else:
            print("Upload failed; dashboard may contain a partial batch. Review the sender output before retrying.", file=sys.stderr)
        return result.returncode or 1

    print(f"Done. Events: {events_path}")
    if backup_path is not None:
        print(f"Previous dashboard events backup: {backup_path}")
    print(f"Evidence files: {evidence_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
