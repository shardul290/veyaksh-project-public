from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

import cv2
import requests

ROOT = Path(__file__).resolve().parent


def fetch_events(api_url: str) -> list[dict[str, Any]]:
    response = requests.get(f"{api_url.rstrip('/')}/events", timeout=10)
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, list):
        raise ValueError("GET /events did not return a list")
    return payload


def render_overlay(input_path: Path, output_path: Path, events: list[dict[str, Any]]) -> None:
    cap = cv2.VideoCapture(str(input_path))
    if not cap.isOpened():
        raise SystemExit(f"Could not open video: {input_path}")

    width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    output_path.parent.mkdir(parents=True, exist_ok=True)
    writer = cv2.VideoWriter(
        str(output_path), cv2.VideoWriter_fourcc(*"mp4v"), fps, (width, height)
    )
    if not writer.isOpened():
        cap.release()
        raise SystemExit(f"Could not create output video: {output_path}")

    # Display the latest records so they remain legible on both portrait and landscape videos.
    visible_events = list(reversed(events[-4:]))
    panel_height = min(height - 1, max(230, 110 + len(visible_events) * 82))
    font = cv2.FONT_HERSHEY_SIMPLEX
    index = 0
    while True:
        ok, frame = cap.read()
        if not ok:
            break

        y0 = height - panel_height
        panel = frame[y0:height, :].copy()
        shade = panel.copy()
        cv2.rectangle(shade, (0, 0), (width, panel_height), (18, 22, 30), -1)
        cv2.addWeighted(shade, 0.84, panel, 0.16, 0, frame[y0:height, :])

        x = max(24, int(width * 0.035))
        title_scale = max(0.7, width / 1800)
        body_scale = max(0.55, width / 2300)
        cv2.putText(frame, "VIDEO EVENT CAPTURE", (x, y0 + 48), font, title_scale,
                    (245, 245, 245), 2, cv2.LINE_AA)
        cv2.putText(frame, "API RECORDS  |  DISPLAY OVERLAY ONLY", (x, y0 + 82), font,
                    body_scale, (180, 195, 210), 1, cv2.LINE_AA)

        if not visible_events:
            cv2.putText(frame, "No events returned by the API", (x, y0 + 145), font,
                        body_scale, (180, 195, 210), 1, cv2.LINE_AA)
        else:
            for row, event in enumerate(visible_events):
                y = y0 + 128 + row * 70
                event_id = event.get("id", "?")
                event_type = str(event.get("type", "unknown")).replace("_", " ").upper()
                created = str(event.get("created_at", ""))
                label = f"#{event_id}  {event_type}"
                cv2.circle(frame, (x + 10, y - 9), 9, (65, 85, 255), -1, cv2.LINE_AA)
                cv2.putText(frame, label, (x + 34, y), font, body_scale,
                            (255, 255, 255), 2, cv2.LINE_AA)
                if created:
                    cv2.putText(frame, created, (x + 34, y + 27), font,
                                max(0.45, body_scale * 0.7), (185, 195, 205),
                                1, cv2.LINE_AA)

        index += 1
        cv2.putText(frame, f"FRAME {index:04d}/{total_frames:04d}",
                    (x, max(28, y0 - 18)), font, max(0.45, body_scale * 0.7),
                    (255, 255, 255), 1, cv2.LINE_AA)
        writer.write(frame)

    cap.release()
    writer.release()
    print(f"Saved: {output_path}")
    print(f"Frames rendered: {index}")
    print(f"Events shown in overlay: {len(visible_events)}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Render events stored by the API as an overlay on a video"
    )
    parser.add_argument("--video", type=Path, default=ROOT / "assets" / "traffic_demo.mp4")
    parser.add_argument("--output", type=Path, default=ROOT / "assets" / "traffic_demo_events.mp4")
    parser.add_argument("--api", default="http://localhost:8000")
    args = parser.parse_args()
    try:
        api_events = fetch_events(args.api)
    except requests.RequestException as exc:
        raise SystemExit(f"Could not load events from {args.api}/events: {exc}") from exc
    render_overlay(args.video, args.output, api_events)
