"""Interactively draw lane and no-parking polygons for one camera frame."""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

import cv2
import numpy as np


def read_frame(path: Path, frame_index: int) -> "cv2.Mat":
    if path.suffix.lower() in {".jpg", ".jpeg", ".png", ".bmp", ".webp"}:
        frame = cv2.imread(str(path))
    else:
        capture = cv2.VideoCapture(str(path))
        try:
            capture.set(cv2.CAP_PROP_POS_FRAMES, frame_index)
            ok, frame = capture.read()
            if not ok:
                frame = None
        finally:
            capture.release()
    if frame is None:
        raise ValueError(f"Could not read an image/frame from {path}")
    return frame


def draw_polygon(frame: "cv2.Mat", title: str) -> list[list[float]]:
    height, width = frame.shape[:2]
    scale = min(1.0, 1280 / width, 800 / height)
    display = cv2.resize(frame, (round(width * scale), round(height * scale)))
    points: list[tuple[int, int]] = []
    window = "VEYAKSH zone calibration"

    def on_mouse(event: int, x: int, y: int, _flags: int, _param: object) -> None:
        if event == cv2.EVENT_LBUTTONDOWN:
            points.append((round(x / scale), round(y / scale)))

    cv2.namedWindow(window, cv2.WINDOW_AUTOSIZE)
    cv2.setMouseCallback(window, on_mouse)
    while True:
        canvas = display.copy()
        for point in points:
            shown = (round(point[0] * scale), round(point[1] * scale))
            cv2.circle(canvas, shown, 5, (0, 220, 255), -1)
        if len(points) > 1:
            shown_points = [(round(x * scale), round(y * scale)) for x, y in points]
            cv2.polylines(canvas, [np.asarray(shown_points, dtype=np.int32)], False, (0, 220, 255), 2)
        cv2.putText(canvas, f"{title} | click vertices; Enter=finish, Backspace=undo, R=reset, Esc=cancel",
                    (12, 28), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (255, 255, 255), 2, cv2.LINE_AA)
        cv2.imshow(window, canvas)
        key = cv2.waitKey(20) & 0xFF
        if key in (10, 13) and len(points) >= 3:
            break
        if key in (8, 127) and points:
            points.pop()
        elif key in (ord("r"), ord("R")):
            points.clear()
        elif key == 27:
            raise KeyboardInterrupt("Calibration cancelled")
    cv2.destroyWindow(window)
    return [[x / width, y / height] for x, y in points]


def draw_heading(frame: "cv2.Mat") -> float:
    height, width = frame.shape[:2]
    scale = min(1.0, 1280 / width, 800 / height)
    display = cv2.resize(frame, (round(width * scale), round(height * scale)))
    points: list[tuple[int, int]] = []
    window = "Set permitted lane direction"

    def on_mouse(event: int, x: int, y: int, _flags: int, _param: object) -> None:
        if event == cv2.EVENT_LBUTTONDOWN and len(points) < 2:
            points.append((round(x / scale), round(y / scale)))

    cv2.namedWindow(window, cv2.WINDOW_AUTOSIZE)
    cv2.setMouseCallback(window, on_mouse)
    while len(points) < 2:
        canvas = display.copy()
        if points:
            p = (round(points[0][0] * scale), round(points[0][1] * scale))
            cv2.circle(canvas, p, 6, (0, 255, 0), -1)
        cv2.putText(canvas, "Click start then permitted travel direction; Esc=cancel",
                    (12, 28), cv2.FONT_HERSHEY_SIMPLEX, 0.65, (255, 255, 255), 2, cv2.LINE_AA)
        cv2.imshow(window, canvas)
        if cv2.waitKey(20) & 0xFF == 27:
            raise KeyboardInterrupt("Calibration cancelled")
    cv2.destroyWindow(window)
    dx = points[1][0] - points[0][0]
    dy = points[1][1] - points[0][1]
    if math.hypot(dx, dy) < 5:
        raise ValueError("Direction arrow is too short; select two points farther apart")
    return round(math.degrees(math.atan2(dy, dx)) % 360, 2)


def ask_float(label: str, default: float, minimum: float, maximum: float) -> float:
    raw = input(f"{label} [{default}]: ").strip()
    value = default if not raw else float(raw)
    if not minimum <= value <= maximum:
        raise ValueError(f"{label} must be between {minimum} and {maximum}")
    return value


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, type=Path, help="Camera image or video")
    parser.add_argument("--frame", type=int, default=0, help="Video frame index to show")
    parser.add_argument("--node-id", default="N01")
    parser.add_argument("--cam-id", default="C01")
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    try:
        if not args.input.is_file() or args.frame < 0:
            raise ValueError("Input must exist and frame index must be non-negative")
        frame = read_frame(args.input, args.frame)
        height, width = frame.shape[:2]
        lanes = []
        while True:
            lane_number = len(lanes) + 1
            lane_polygon = draw_polygon(frame, f"Draw directional lane ROI {lane_number}")
            heading = draw_heading(frame)
            tolerance = ask_float("Allowed heading tolerance in degrees", 35, 0, 180)
            lanes.append({
                "zone_id": f"LANE_{lane_number}", "polygon": lane_polygon,
                "allowed_dir_deg": heading, "tolerance_deg": tolerance,
                "min_observations": 8, "min_elapsed_s": 0.75,
                "min_displacement_ratio": 0.015, "required_wrong_windows": 3,
            })
            if input("Add another directional lane? [y/N]: ").strip().lower() not in {"y", "yes"}:
                break
        no_park_zones = []
        if input("Add a no-parking/encroachment region? [y/N]: ").strip().lower() in {"y", "yes"}:
            polygon = draw_polygon(frame, "Draw no-parking ROI")
            dwell = ask_float("Dwell threshold in seconds", 15, 0.1, 86400)
            no_park_zones.append({"zone_id": "NO_PARK_1", "polygon": polygon,
                                  "max_dwell_s": dwell, "max_gap_s": 1.0})
        config = {
            "node_id": args.node_id, "cam_id": args.cam_id,
            "frame_size": {"width": width, "height": height},
            "max_track_gap_s": 1.0, "lanes": lanes, "no_park_zones": no_park_zones,
        }
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")
        print(f"Saved calibrated zones to {args.output}")
        print("Review the JSON and thresholds before treating detections as real incidents.")
        return 0
    except (OSError, ValueError, cv2.error, KeyboardInterrupt) as exc:
        cv2.destroyAllWindows()
        print(f"Calibration stopped: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
