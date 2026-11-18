"""Extract deduplicated, timestamped JPEG frames from video using GStreamer + OpenCV.

Run with the project's MSYS2 UCRT64 Python, which provides GI/GStreamer and
GStreamer-enabled OpenCV (see GSTREAMER_SETUP.md).
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import re
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import cv2
import gi
import numpy as np

gi.require_version("Gst", "1.0")
gi.require_version("GstApp", "1.0")
gi.require_version("GstVideo", "1.0")
from gi.repository import Gst, GstVideo  # noqa: E402


Gst.init(None)
METADATA_FIELDS = [
    "city", "clip", "timestamp", "filename", "source", "source_timestamp_ms",
    "frame_number", "sha256",
]


@dataclass
class ExtractStats:
    saved: int = 0
    duplicates: int = 0
    sampled_out: int = 0


def safe_part(value: str) -> str:
    """Keep filename components portable while retaining readable identifiers."""
    value = re.sub(r"[^A-Za-z0-9_-]+", "", value.strip())
    if not value:
        raise ValueError("city and clip identifiers must contain letters or digits")
    return value


def infer_ids(source: str) -> tuple[str, str]:
    stem = Path(source).stem
    match = re.match(r"(?P<city>[A-Za-z][A-Za-z0-9-]*)_(?P<clip>.+)", stem)
    if match:
        return safe_part(match.group("city").lower()), safe_part(match.group("clip"))
    return "unknown", safe_part(stem)


def make_pipeline(source: str, is_rtsp: bool, latency_ms: int) -> Gst.Pipeline:
    sink = ' ! videoconvert ! video/x-raw,format=BGR ! appsink name=framesink sync=false max-buffers=4 drop=false'
    if is_rtsp:
        launch = f'rtspsrc location="{source}" protocols=tcp latency={latency_ms} ! decodebin{sink}'
    else:
        uri = Path(source).resolve().as_uri()
        launch = f'uridecodebin uri="{uri}" ! videoconvert ! video/x-raw,format=BGR ! appsink name=framesink sync=false max-buffers=4 drop=false'
    pipeline = Gst.parse_launch(launch)
    if not isinstance(pipeline, Gst.Pipeline):
        raise RuntimeError("GStreamer did not create a pipeline")
    return pipeline


def perceptual_hash(frame) -> int:
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    small = cv2.resize(gray, (32, 32), interpolation=cv2.INTER_AREA).astype("float32")
    dct = cv2.dct(small)[:8, :8]
    bits = dct.flatten()[1:] >= float(dct.flatten()[1:].mean())
    result = 0
    for bit in bits:
        result = (result << 1) | int(bit)
    return result


def frame_digest(frame) -> str:
    return hashlib.sha256(memoryview(frame)).hexdigest()


def source_timestamp(pts: int, base_utc: datetime | None) -> str:
    seconds = pts / Gst.SECOND
    if base_utc is None:
        return f"{seconds:.3f}s"
    return datetime.fromtimestamp(base_utc.timestamp() + seconds, timezone.utc).isoformat(timespec="milliseconds")


def extract(source: str, out_dir: Path, city: str, clip: str, interval: float,
            duplicate_distance: int, latency_ms: int, max_frames: int | None) -> ExtractStats:
    is_rtsp = source.lower().startswith(("rtsp://", "rtsps://"))
    if not is_rtsp and not Path(source).is_file():
        raise FileNotFoundError(f"video file does not exist: {source}")
    out_dir.mkdir(parents=True, exist_ok=True)
    metadata_path = out_dir / f"{city}_{clip}_metadata.csv"
    existing_numbers = [
        int(match.group(1))
        for path in out_dir.glob(f"{city}_{clip}_*.jpg")
        if (match := re.fullmatch(rf"{re.escape(city)}_{re.escape(clip)}_(\d+)\.jpg", path.name))
    ]
    last_frame_no = max(existing_numbers, default=0)
    pipeline = make_pipeline(source, is_rtsp, latency_ms)
    sink = pipeline.get_by_name("framesink")
    if sink is None:
        raise RuntimeError("GStreamer appsink was not created")

    stats = ExtractStats()
    previous_hash: int | None = None
    next_sample_s = 0.0
    base_utc = datetime.now(timezone.utc) if is_rtsp else None
    started = time.monotonic()
    try:
        if pipeline.set_state(Gst.State.PLAYING) == Gst.StateChangeReturn.FAILURE:
            raise RuntimeError("Could not start GStreamer pipeline")
        with metadata_path.open("a", newline="", encoding="utf-8") as csv_file:
            writer = csv.DictWriter(csv_file, fieldnames=METADATA_FIELDS)
            if csv_file.tell() == 0:
                writer.writeheader()
            while max_frames is None or stats.saved < max_frames:
                # GstAppSink exposes this GObject signal across GI versions;
                # some versions do not provide a try_pull_sample() method.
                sample = sink.emit("try-pull-sample", 2 * Gst.SECOND)
                if sample is None:
                    message = pipeline.get_bus().pop_filtered(Gst.MessageType.ERROR | Gst.MessageType.EOS)
                    if message is not None:
                        if message.type == Gst.MessageType.ERROR:
                            error, debug = message.parse_error()
                            raise RuntimeError(f"GStreamer error: {error.message} ({debug})")
                        break
                    if is_rtsp and time.monotonic() - started > 10:
                        raise TimeoutError("No RTSP frame received within 10 seconds")
                    continue

                buffer = sample.get_buffer()
                pts = buffer.pts
                if pts == Gst.CLOCK_TIME_NONE:
                    continue
                seconds = pts / Gst.SECOND
                if seconds + 0.0005 < next_sample_s:
                    stats.sampled_out += 1
                    continue
                next_sample_s = seconds + interval

                caps = sample.get_caps().get_structure(0)
                width, height = caps.get_value("width"), caps.get_value("height")
                video_info = GstVideo.VideoInfo.new_from_caps(sample.get_caps())
                ok, mapped = buffer.map(Gst.MapFlags.READ)
                if not ok:
                    continue
                try:
                    frame = memoryview(mapped.data)
                    # GStreamer BGR rows may have padding; honor the negotiated stride.
                    stride = video_info.stride[0]
                    image = np.ndarray((height, width, 3), dtype=np.uint8, buffer=frame, strides=(stride, 3, 1)).copy()
                finally:
                    buffer.unmap(mapped)

                current_hash = perceptual_hash(image)
                if previous_hash is not None and (current_hash ^ previous_hash).bit_count() <= duplicate_distance:
                    stats.duplicates += 1
                    continue
                previous_hash = current_hash
                frame_no = last_frame_no + stats.saved + 1
                filename = f"{city}_{clip}_{frame_no:04d}.jpg"
                target = out_dir / filename
                if not cv2.imwrite(str(target), image, [cv2.IMWRITE_JPEG_QUALITY, 95]):
                    raise OSError(f"Could not write JPEG: {target}")
                writer.writerow({
                    "city": city, "clip": clip, "timestamp": source_timestamp(pts, base_utc),
                    "filename": filename, "source": source, "source_timestamp_ms": f"{seconds * 1000:.3f}",
                    "frame_number": frame_no, "sha256": frame_digest(image),
                })
                csv_file.flush()
                stats.saved += 1
    finally:
        pipeline.set_state(Gst.State.NULL)
    return stats


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", help="Local video path or rtsp:// URL")
    parser.add_argument("--out", type=Path, default=Path("frames"), help="Output directory (default: ./frames)")
    parser.add_argument("--city", help="City identifier; inferred from city_clip.ext filenames")
    parser.add_argument("--clip", help="Original clip identifier; inferred from filename when possible")
    parser.add_argument("--interval", type=float, default=1.0, help="Minimum source-time gap between samples (seconds)")
    parser.add_argument("--duplicate-distance", type=int, default=4, help="Perceptual-hash distance threshold (0-63)")
    parser.add_argument("--latency-ms", type=int, default=150, help="RTSP jitter buffer latency")
    parser.add_argument("--max-frames", type=int, help="Stop after saving this many frames (useful for live RTSP)")
    args = parser.parse_args()
    if args.interval <= 0 or not 0 <= args.duplicate_distance <= 63 or args.latency_ms < 0:
        parser.error("interval must be positive, duplicate-distance 0-63, and latency-ms nonnegative")
    inferred_city, inferred_clip = infer_ids(args.source)
    city = safe_part(args.city or inferred_city).lower()
    clip = safe_part(args.clip or inferred_clip)
    try:
        stats = extract(args.source, args.out, city, clip, args.interval,
                        args.duplicate_distance, args.latency_ms, args.max_frames)
    except Exception as exc:
        print(f"Frame extraction failed: {exc}", file=sys.stderr)
        return 1
    print(f"Saved {stats.saved} frames; skipped {stats.duplicates} near-duplicates and "
          f"{stats.sampled_out} frames outside sampling intervals. Metadata: "
          f"{args.out / f'{city}{clip}_metadata.csv'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
