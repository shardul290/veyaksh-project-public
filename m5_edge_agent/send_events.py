"""Map M5 event-engine JSONL records to M6 and upload evidence to the API."""

from __future__ import annotations

import argparse
import hashlib
import os
import json
import shutil
import subprocess
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import cv2
import requests

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = Path(__file__).with_name("config.json")
DEFAULT_EVENTS = Path(__file__).with_name("sample_events.jsonl")
ALLOWED_TYPES = {"wrong_way", "encroachment"}
ALLOWED_CLASSES = {"two_wheeler", "auto", "e_rickshaw", "car", "bus_truck", "cart"}
CLIP_SECONDS = 3.0


def read_json(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as stream:
        value = json.load(stream)
    if not isinstance(value, dict):
        raise ValueError(f"Expected a JSON object in {path}")
    return value


def read_events(path: Path) -> list[tuple[int, dict[str, Any]]]:
    events: list[tuple[int, dict[str, Any]]] = []
    with path.open(encoding="utf-8") as stream:
        for line_no, line in enumerate(stream, start=1):
            if not line.strip():
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"Invalid JSON on {path}:{line_no}: {exc}") from exc
            if not isinstance(event, dict):
                raise ValueError(f"Expected a JSON object on {path}:{line_no}")
            events.append((line_no, event))
    if not events:
        raise ValueError(f"No events found in {path}")
    return events


def resolve_project_path(value: str, label: str) -> Path:
    path = Path(value)
    if not path.is_absolute():
        path = PROJECT_ROOT / path
    if not path.exists():
        raise FileNotFoundError(f"Configured {label} does not exist: {path}")
    return path.resolve()


def validate_engine_event(event: dict[str, Any], line_no: int) -> float:
    for field in ("type", "track_id", "class", "conf", "zone_id", "frame", "video_ts_s"):
        if field not in event:
            raise ValueError(f"Line {line_no}: missing required event field {field!r}")
    if event["type"] not in ALLOWED_TYPES:
        raise ValueError(f"Line {line_no}: unsupported type {event['type']!r}")
    if event["class"] not in ALLOWED_CLASSES:
        raise ValueError(f"Line {line_no}: unsupported class {event['class']!r}")
    if not isinstance(event["track_id"], int) or event["track_id"] < 0:
        raise ValueError(f"Line {line_no}: track_id must be a non-negative integer")
    if not isinstance(event["conf"], (int, float)) or not 0 <= event["conf"] <= 1:
        raise ValueError(f"Line {line_no}: conf must be between 0 and 1")
    if not isinstance(event["frame"], int) or event["frame"] < 0:
        raise ValueError(f"Line {line_no}: frame must be a non-negative integer")
    seconds = event["video_ts_s"]
    if not isinstance(seconds, (int, float)) or seconds < 0:
        raise ValueError(f"Line {line_no}: video_ts_s must be non-negative")
    return float(seconds)


def extract_snapshot(
    source: Path,
    timestamp_s: float,
    target: Path,
    bbox: list[float] | None = None,
    label: str | None = None,
) -> None:
    capture = cv2.VideoCapture(str(source))
    try:
        if not capture.isOpened():
            raise RuntimeError(f"OpenCV could not open source video: {source}")
        duration = capture.get(cv2.CAP_PROP_FRAME_COUNT) / max(capture.get(cv2.CAP_PROP_FPS), 0.001)
        if timestamp_s > duration:
            raise ValueError(f"Event timestamp {timestamp_s:.3f}s exceeds source duration {duration:.3f}s")
        capture.set(cv2.CAP_PROP_POS_MSEC, timestamp_s * 1000)
        ok, frame = capture.read()
        if not ok or frame is None:
            raise RuntimeError(f"Could not read source frame at {timestamp_s:.3f}s")
        if bbox is not None and len(bbox) == 4:
            height, width = frame.shape[:2]
            x1, y1, x2, y2 = (int(round(value)) for value in bbox)
            x1, x2 = max(0, min(width - 1, x1)), max(0, min(width - 1, x2))
            y1, y2 = max(0, min(height - 1, y1)), max(0, min(height - 1, y2))
            if x2 > x1 and y2 > y1:
                red = (0, 0, 255)
                cv2.rectangle(frame, (x1, y1), (x2, y2), red, thickness=max(2, width // 500))
                text = label or "EVENT"
                font = cv2.FONT_HERSHEY_SIMPLEX
                scale = max(0.5, width / 1600)
                text_size, baseline = cv2.getTextSize(text, font, scale, 2)
                text_y = max(text_size[1] + baseline + 4, y1)
                cv2.rectangle(frame, (x1, text_y - text_size[1] - baseline - 4),
                              (min(width - 1, x1 + text_size[0] + 8), text_y + 2), red, -1)
                cv2.putText(frame, text, (x1 + 4, text_y - baseline), font, scale,
                            (255, 255, 255), 2, cv2.LINE_AA)
        target.parent.mkdir(parents=True, exist_ok=True)
        if not cv2.imwrite(str(target), frame, [cv2.IMWRITE_JPEG_QUALITY, 90]):
            raise RuntimeError(f"Could not write snapshot: {target}")
    finally:
        capture.release()


def extract_clip(source: Path, timestamp_s: float, target: Path) -> tuple[Path, float | None]:
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        # Keep the demo usable without a system FFmpeg install. The dashboard
        # opens the original clip at the event offset using a media fragment.
        digest = hashlib.sha256(str(source.resolve()).encode("utf-8")).hexdigest()[:10]
        source_link = target.parent / f"source_{source.stem}_{digest}{source.suffix.lower()}"
        target.parent.mkdir(parents=True, exist_ok=True)
        if not source_link.exists() or source_link.stat().st_size != source.stat().st_size:
            source_link.unlink(missing_ok=True)
            try:
                os.link(source, source_link)
            except OSError:
                shutil.copy2(source, source_link)
        return source_link, timestamp_s
    capture = cv2.VideoCapture(str(source))
    try:
        fps = capture.get(cv2.CAP_PROP_FPS)
        frame_count = capture.get(cv2.CAP_PROP_FRAME_COUNT)
        duration = frame_count / fps if fps > 0 else 0
    finally:
        capture.release()
    if duration < CLIP_SECONDS:
        raise ValueError(f"Source video must be at least {CLIP_SECONDS:g} seconds; got {duration:.3f}s")
    start_s = min(max(0.0, timestamp_s - CLIP_SECONDS / 2), duration - CLIP_SECONDS)
    target.parent.mkdir(parents=True, exist_ok=True)
    command = [
        ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-ss", f"{start_s:.3f}",
        "-i", str(source), "-vf",
        "tpad=stop_mode=clone:stop_duration=2,setpts=PTS-STARTPTS,fps=30,trim=duration=3",
        "-an", "-c:v", "libx264",
        "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(target),
    ]
    result = subprocess.run(command, capture_output=True, text=True, check=False)
    if result.returncode != 0 or not target.exists() or target.stat().st_size == 0:
        target.unlink(missing_ok=True)
        raise RuntimeError(f"ffmpeg could not create {target.name}: {result.stderr.strip()}")
    check = cv2.VideoCapture(str(target))
    try:
        output_fps = check.get(cv2.CAP_PROP_FPS)
        output_duration = check.get(cv2.CAP_PROP_FRAME_COUNT) / output_fps if output_fps > 0 else 0
    finally:
        check.release()
    if not CLIP_SECONDS <= output_duration <= 5.0:
        target.unlink(missing_ok=True)
        raise RuntimeError(f"Generated clip duration must be 3 to 5 seconds; got {output_duration:.3f}s")
    return target, None


def source_timestamp(source_start: str, offset_s: float) -> str:
    try:
        start = datetime.fromisoformat(source_start.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("source_start_ts must be ISO 8601") from exc
    if start.tzinfo is None or start.utcoffset() is None:
        raise ValueError("source_start_ts must include a timezone offset")
    return (start + timedelta(seconds=offset_s)).isoformat(timespec="milliseconds")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--events", type=Path, default=DEFAULT_EVENTS)
    parser.add_argument("--api-url", help="Override config api_url")
    parser.add_argument("--interval", type=float, default=0.0,
                        help="Seconds between uploads (default: send immediately)")
    args = parser.parse_args()
    if args.interval < 0:
        parser.error("--interval cannot be negative")
    try:
        config = read_json(args.config)
        events = read_events(args.events)
        api_url = (args.api_url or config["api_url"]).rstrip("/")
        node_id, cam_id = config["node_id"], config["cam_id"]
        if not isinstance(node_id, str) or not node_id or not isinstance(cam_id, str) or not cam_id:
            raise ValueError("config must provide non-empty node_id and cam_id")
        source = resolve_project_path(config["source_video"], "source_video")
        evidence_dir = Path(config["evidence_dir"])
        if not evidence_dir.is_absolute():
            evidence_dir = PROJECT_ROOT / evidence_dir
        evidence_dir = evidence_dir.resolve()
        media_root = (PROJECT_ROOT / "m6_backend" / "media").resolve()
        try:
            evidence_relative = evidence_dir.relative_to(media_root).as_posix()
        except ValueError as exc:
            raise ValueError("evidence_dir must be inside m6_backend/media for API serving") from exc
        source_start = config["source_start_ts"]
        source_timestamp(source_start, 0)  # Validate recording time before extracting any evidence.
        response = requests.get(f"{api_url}/health", timeout=5)
        response.raise_for_status()
        print(f"Uploading {len(events)} events to {api_url}/events via {node_id}/{cam_id}")
        for index, (line_no, event) in enumerate(events, start=1):
            video_ts = validate_engine_event(event, line_no)
            identity = f"{source.resolve()}|{source_start}|{video_ts}|{event['type']}|{event['track_id']}"
            suffix = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:12]
            stem = f"event_{index:03d}_track_{event['track_id']}_{suffix}"
            snapshot_path = evidence_dir / f"{stem}.jpg"
            clip_path = evidence_dir / f"{stem}.mp4"
            bbox = event.get("bbox")
            if bbox is not None and (
                not isinstance(bbox, list) or len(bbox) != 4
                or not all(isinstance(value, (int, float)) for value in bbox)
            ):
                raise ValueError(f"Line {line_no}: bbox must be four numeric pixel coordinates")
            snapshot_label = f"{event['type'].replace('_', ' ').upper()} | {event['class']} | ID {event['track_id']}"
            extract_snapshot(source, video_ts, snapshot_path, bbox=bbox, label=snapshot_label)
            clip_asset, clip_offset = extract_clip(source, video_ts, clip_path)
            clip_value = f"{evidence_relative}/{clip_asset.name}"
            if clip_offset is not None:
                clip_value += f"#t={clip_offset:.3f}"
            payload = {
                "node_id": node_id,
                "cam_id": cam_id,
                "ts": source_timestamp(source_start, video_ts),
                "video_ts_s": video_ts,
                "type": event["type"],
                "track_id": event["track_id"],
                "class": event["class"],
                "conf": float(event["conf"]),
                "zone_id": str(event["zone_id"]),
                "plate": event.get("plate"),
                "snapshot": f"{evidence_relative}/{snapshot_path.name}",
                "clip": clip_value,
            }
            result = requests.post(f"{api_url}/events", json=payload, timeout=15)
            if not result.ok:
                raise RuntimeError(f"API rejected line {line_no} (HTTP {result.status_code}): {result.text}")
            event_id = result.json().get("id")
            if not isinstance(event_id, int):
                raise RuntimeError(f"API returned no event id for line {line_no}")
            print(f"{index}/{len(events)} sent: id={event_id}, {event['type']}, {event['class']}, "
                  f"{node_id}, snapshot=/media/{payload['snapshot']}, clip=/media/{payload['clip']}",
                  flush=True)
            if args.interval and index < len(events):
                time.sleep(args.interval)
        print(f"Done: {len(events)} events submitted. Evidence is stored locally in {evidence_dir}.")
        return 0
    except (KeyError, OSError, ValueError, RuntimeError, requests.RequestException, json.JSONDecodeError) as exc:
        print(f"Event upload failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
