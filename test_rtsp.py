from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import cv2

ROOT = Path(__file__).resolve().parent
DEFAULT_URL = "rtsp://localhost:8554/traffic"
DEFAULT_SOURCE = ROOT / "assets" / "traffic_demo.mp4"


def video_duration(path: Path) -> float | None:
    source = cv2.VideoCapture(str(path))
    if not source.isOpened():
        source.release()
        return None
    fps = source.get(cv2.CAP_PROP_FPS)
    frames = source.get(cv2.CAP_PROP_FRAME_COUNT)
    source.release()
    return frames / fps if fps > 0 and frames > 0 else None


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Verify RTSP connection, frame delivery, dimensions, and stream continuity."
    )
    parser.add_argument("--url", default=DEFAULT_URL)
    parser.add_argument("--source-video", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--seconds", type=float, default=30.0,
                        help="How long to monitor the stream (default: 30 seconds).")
    parser.add_argument("--open-timeout", type=int, default=15000,
                        help="Open timeout in milliseconds.")
    parser.add_argument("--stall-timeout", type=float, default=8.0,
                        help="Fail if frames stop arriving for this many seconds.")
    args = parser.parse_args()

    if args.seconds <= 0 or args.stall_timeout <= 0:
        parser.error("--seconds and --stall-timeout must be positive")

    source_duration = video_duration(args.source_video)
    if source_duration is not None and args.seconds < source_duration * 2:
        parser.error(
            f"--seconds must be at least twice the source duration ({source_duration:.2f} seconds) "
            "to check continuity across multiple loops"
        )

    print(f"Connecting to: {args.url}", flush=True)
    params = [
        cv2.CAP_PROP_OPEN_TIMEOUT_MSEC, args.open_timeout,
        cv2.CAP_PROP_READ_TIMEOUT_MSEC, int(args.stall_timeout * 1000),
    ]
    cap = cv2.VideoCapture(args.url, cv2.CAP_FFMPEG, params)
    if not cap.isOpened():
        print("FAIL: OpenCV could not open the RTSP stream.", file=sys.stderr)
        return 1
    print("PASS: RTSP connection opened with OpenCV FFmpeg backend.", flush=True)

    start = time.monotonic()
    last_frame_at = start
    received = 0
    dimensions: tuple[int, int] | None = None
    try:
        while time.monotonic() - start < args.seconds:
            ok, frame = cap.read()
            now = time.monotonic()
            if now - last_frame_at > args.stall_timeout:
                print(f"FAIL: Frame gap exceeded {args.stall_timeout:g} seconds.", file=sys.stderr)
                return 1
            if ok and frame is not None and frame.size:
                received += 1
                last_frame_at = now
                frame_height, frame_width = frame.shape[:2]
                current_dimensions = (frame_width, frame_height)
                if dimensions is None:
                    dimensions = current_dimensions
                    print(f"PASS: First frame received; dimensions {frame_width}x{frame_height}.", flush=True)
                elif current_dimensions != dimensions:
                    print(f"FAIL: Frame dimensions changed from {dimensions} to {current_dimensions}.", file=sys.stderr)
                    return 1
            else:
                time.sleep(0.02)
    except KeyboardInterrupt:
        print("Interrupted before the monitoring period completed.", file=sys.stderr)
        return 130
    finally:
        cap.release()

    elapsed = time.monotonic() - start
    if received == 0:
        print("FAIL: No video frames were received.", file=sys.stderr)
        return 1
    print(f"PASS: Stream kept delivering frames for {elapsed:.1f} seconds.")
    print(f"Frames received: {received} ({received / elapsed:.1f} FPS observed)")
    print(f"Frame dimensions: {dimensions[0]}x{dimensions[1]}")
    if source_duration is not None:
        print(f"Source clip duration: {source_duration:.2f} seconds; monitored about {elapsed / source_duration:.1f} clip durations.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
