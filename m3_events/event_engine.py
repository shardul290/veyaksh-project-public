"""Apply wrong-way and no-parking dwell rules to M2 ByteTrack JSONL output.

The output is an intermediate event JSONL accepted by m5_edge_agent/send_events.py.
That sender adds the ISO timestamp and evidence paths required by the M6 schema.
"""

from __future__ import annotations

import argparse
import json
import math
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from m3_events.rules.encroachment import EncroachmentRule
from m3_events.rules.wrong_way import WrongWayRule

VEHICLE_CLASSES = {"car", "two_wheeler", "auto", "e_rickshaw", "bus_truck", "cart"}


def read_json(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as stream:
        value = json.load(stream)
    if not isinstance(value, dict):
        raise ValueError(f"Expected a JSON object in {path}")
    return value


def validate_polygon(value: Any, label: str) -> list[list[float]]:
    if not isinstance(value, list) or len(value) < 3:
        raise ValueError(f"{label}: polygon must have at least three [x, y] points")
    polygon: list[list[float]] = []
    for point in value:
        if (not isinstance(point, list) or len(point) != 2
                or not all(isinstance(v, (int, float)) and math.isfinite(v) for v in point)):
            raise ValueError(f"{label}: every polygon point must be two finite numbers")
        x, y = float(point[0]), float(point[1])
        if not 0.0 <= x <= 1.0 or not 0.0 <= y <= 1.0:
            raise ValueError(f"{label}: normalized polygon coordinates must be in [0, 1]")
        polygon.append([x, y])
    return polygon


def load_zones(config: dict[str, Any]) -> tuple[int, int, list[dict[str, Any]], list[dict[str, Any]]]:
    for key, pattern in (("node_id", r"^N\d{2}$"), ("cam_id", r"^C\d+$")):
        if not isinstance(config.get(key), str) or not re.fullmatch(pattern, config[key]):
            raise ValueError(f"zone config needs a valid {key} (pattern {pattern})")
    size = config.get("frame_size")
    if (not isinstance(size, dict) or not isinstance(size.get("width"), int)
            or not isinstance(size.get("height"), int)
            or size["width"] <= 0 or size["height"] <= 0):
        raise ValueError("frame_size must contain positive integer width and height")
    width, height = size["width"], size["height"]

    lanes = config.get("lanes", [])
    no_park = config.get("no_park_zones", [])
    if not isinstance(lanes, list) or not isinstance(no_park, list) or not (lanes or no_park):
        raise ValueError("zone config must include at least one lane or no_park_zone")
    used_ids: set[str] = set()
    for lane in lanes:
        if not isinstance(lane, dict) or not isinstance(lane.get("zone_id"), str) or not lane["zone_id"]:
            raise ValueError("each lane needs a non-empty zone_id")
        if lane["zone_id"] in used_ids:
            raise ValueError(f"duplicate zone_id: {lane['zone_id']}")
        used_ids.add(lane["zone_id"])
        lane["polygon"] = validate_polygon(lane.get("polygon"), lane["zone_id"])
        for key in ("allowed_dir_deg", "tolerance_deg", "min_elapsed_s", "min_displacement_ratio"):
            value = lane.get(key)
            if not isinstance(value, (int, float)) or not math.isfinite(value):
                raise ValueError(f"lane {lane['zone_id']}: {key} must be a finite number")
        if not 0 <= lane["allowed_dir_deg"] < 360 or not 0 <= lane["tolerance_deg"] <= 180:
            raise ValueError(f"lane {lane['zone_id']}: heading must be [0, 360), tolerance [0, 180]")
        boundary_margin = lane.get("boundary_margin_px", 0)
        if not isinstance(boundary_margin, (int, float)) or not math.isfinite(boundary_margin) or boundary_margin < 0:
            raise ValueError(f"lane {lane['zone_id']}: boundary_margin_px must be non-negative")
        if lane["min_elapsed_s"] < 0 or not 0 <= lane["min_displacement_ratio"] <= 1:
            raise ValueError(f"lane {lane['zone_id']}: invalid elapsed time or displacement ratio")
        for key in ("min_observations", "required_wrong_windows"):
            if not isinstance(lane.get(key), int) or lane[key] < 2:
                raise ValueError(f"lane {lane['zone_id']}: {key} must be an integer >= 2")

    for zone in no_park:
        if not isinstance(zone, dict) or not isinstance(zone.get("zone_id"), str) or not zone["zone_id"]:
            raise ValueError("each no_park_zone needs a non-empty zone_id")
        if zone["zone_id"] in used_ids:
            raise ValueError(f"duplicate zone_id: {zone['zone_id']}")
        used_ids.add(zone["zone_id"])
        zone["polygon"] = validate_polygon(zone.get("polygon"), zone["zone_id"])
        for key in ("max_dwell_s", "max_gap_s"):
            value = zone.get(key)
            if not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
                raise ValueError(f"zone {zone['zone_id']}: {key} must be positive")
    return width, height, lanes, no_park


def read_tracks(path: Path) -> dict[int, list[dict[str, Any]]]:
    grouped: dict[int, list[dict[str, Any]]] = defaultdict(list)
    with path.open(encoding="utf-8") as stream:
        for line_no, line in enumerate(stream, start=1):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"Invalid JSON at {path}:{line_no}: {exc}") from exc
            if not isinstance(row, dict):
                raise ValueError(f"Expected JSON object at {path}:{line_no}")
            if row.get("track_id") is None:
                continue
            track_id, frame, ts = row.get("track_id"), row.get("frame"), row.get("ts")
            bbox, label, conf = row.get("bbox"), row.get("class"), row.get("conf")
            if not isinstance(track_id, int) or isinstance(track_id, bool) or track_id < 0:
                raise ValueError(f"{path}:{line_no}: track_id must be a non-negative integer or null")
            if not isinstance(frame, int) or frame < 0:
                raise ValueError(f"{path}:{line_no}: frame must be a non-negative integer")
            if not isinstance(ts, (int, float)) or not math.isfinite(ts) or ts < 0:
                raise ValueError(f"{path}:{line_no}: ts must be a finite non-negative number")
            if label not in VEHICLE_CLASSES:
                continue
            if (not isinstance(bbox, list) or len(bbox) != 4
                    or not all(isinstance(v, (int, float)) and math.isfinite(v) for v in bbox)):
                raise ValueError(f"{path}:{line_no}: bbox must be [x1, y1, x2, y2]")
            if not isinstance(conf, (int, float)) or not math.isfinite(conf) or not 0 <= conf <= 1:
                raise ValueError(f"{path}:{line_no}: conf must be between 0 and 1")
            x1, y1, x2, y2 = map(float, bbox)
            if x2 <= x1 or y2 <= y1:
                raise ValueError(f"{path}:{line_no}: bbox must have positive width and height")
            grouped[track_id].append({
                "track_id": track_id, "frame": frame, "ts": float(ts),
                "bbox": [x1, y1, x2, y2], "class": label, "conf": float(conf),
            })
    if not grouped:
        raise ValueError(f"No usable tracked vehicle detections found in {path}")
    for rows in grouped.values():
        rows.sort(key=lambda item: (item["ts"], item["frame"]))
    return grouped


def run_engine(tracks: dict[int, list[dict[str, Any]]], config: dict[str, Any]) -> list[dict[str, Any]]:
    width, height, lanes, no_park = load_zones(config)
    max_gap_s = float(config.get("max_track_gap_s", 1.0))
    if max_gap_s <= 0:
        raise ValueError("max_track_gap_s must be positive")
    min_class_consistency = float(config.get("min_track_class_consistency", 0.0))
    if not 0.0 <= min_class_consistency <= 1.0:
        raise ValueError("min_track_class_consistency must be between 0 and 1")

    wrong_way = WrongWayRule(width, height, max_gap_s)
    encroachment = EncroachmentRule(width, height, max_gap_s)
    events: list[dict[str, Any]] = []
    consistent_detections: list[dict[str, Any]] = []
    for rows in tracks.values():
        class_counts = Counter(row["class"] for row in rows)
        if not class_counts:
            continue
        majority_class, majority_count = class_counts.most_common(1)[0]
        consistency = majority_count / len(rows)
        if consistency < min_class_consistency:
            continue
        # Keep the track's majority class and ignore intermittent class flips.
        consistent_detections.extend(row for row in rows if row["class"] == majority_class)

    detections = sorted(
        consistent_detections,
        key=lambda item: (item["ts"], item["frame"], item["track_id"]),
    )
    for detection in detections:
        for lane in lanes:
            event = wrong_way.update(detection, lane)
            if event:
                events.append(event)
        for zone in no_park:
            event = encroachment.update(detection, zone)
            if event:
                events.append(event)

    for event in events:
        event["node_id"] = config["node_id"]
        event["cam_id"] = config["cam_id"]
    events.sort(key=lambda item: (item["video_ts_s"], item["frame"], item["track_id"]))
    return events


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tracks", required=True, type=Path, help="M2 ByteTrack JSONL for one video")
    parser.add_argument("--zones", required=True, type=Path, help="Calibrated per-camera zone JSON")
    parser.add_argument("--output", required=True, type=Path, help="M5-compatible intermediate events JSONL")
    args = parser.parse_args()
    try:
        if not args.tracks.is_file():
            raise FileNotFoundError(f"Tracks file not found: {args.tracks}")
        if not args.zones.is_file():
            raise FileNotFoundError(f"Zone config not found: {args.zones}")
        events = run_engine(read_tracks(args.tracks), read_json(args.zones))
        args.output.parent.mkdir(parents=True, exist_ok=True)
        with args.output.open("w", encoding="utf-8", newline="\n") as stream:
            for event in events:
                stream.write(json.dumps(event, separators=(",", ":")) + "\n")
        counts = Counter(event["type"] for event in events)
        print(f"Wrote {len(events)} events to {args.output}: {dict(counts)}")
        print("Intermediate JSONL is ready for m5_edge_agent/send_events.py.")
        return 0
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"Event engine failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
