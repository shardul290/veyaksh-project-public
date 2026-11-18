from __future__ import annotations

import argparse
from pathlib import Path

import cv2


def inspect_video(video_path: Path) -> int:
    capture = cv2.VideoCapture(str(video_path))
    if not capture.isOpened():
        raise SystemExit(f"Could not open video: {video_path}")
    frame_count = int(capture.get(cv2.CAP_PROP_FRAME_COUNT))
    fps = capture.get(cv2.CAP_PROP_FPS)
    width = int(capture.get(cv2.CAP_PROP_FRAME_WIDTH))
    height = int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT))
    print(f"Video: {video_path}")
    print(f"Total frames: {frame_count}")
    print(f"FPS: {fps:g}")
    print(f"Resolution: {width}x{height}")
    read_count = 0
    while True:
        ok, _frame = capture.read()
        if not ok:
            break
        read_count += 1
    capture.release()
    print(f"Frames read: {read_count}")
    return read_count


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Inspect and read frames from a video using OpenCV")
    parser.add_argument("video", type=Path)
    args = parser.parse_args()
    inspect_video(args.video)
