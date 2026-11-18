"""Run an RTSP server from a clip and verify that a GStreamer client receives video."""

from __future__ import annotations

import argparse
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import gi

gi.require_version("Gst", "1.0")
gi.require_version("GstApp", "1.0")
from gi.repository import Gst, GstApp  # noqa: E402,F401


ROOT = Path(__file__).resolve().parent
INPUT_DIR = ROOT / "assets" / "rtsp_inputs"
SERVER = ROOT / "gst_rtsp_server.py"


def client_pipeline(url: str) -> Gst.Pipeline:
    launch = (
        f'rtspsrc location="{url}" protocols=tcp latency=150 '
        "! rtph264depay ! decodebin ! d3d12download ! videoconvert "
        "! video/x-raw,format=BGR "
        "! appsink name=framesink sync=false max-buffers=1 drop=true"
    )
    pipeline = Gst.parse_launch(launch)
    if not isinstance(pipeline, Gst.Pipeline):
        raise RuntimeError("GStreamer did not create a client pipeline")
    return pipeline


def message_error(pipeline: Gst.Pipeline) -> str | None:
    message = pipeline.get_bus().pop_filtered(Gst.MessageType.ERROR | Gst.MessageType.EOS)
    if message is None:
        return None
    if message.type == Gst.MessageType.ERROR:
        error, debug = message.parse_error()
        return f"{error.message} ({debug})" if debug else error.message
    # A media seek may produce an EOS on some decoder plugins; the frame-gap
    # check below determines whether the RTSP connection actually stopped.
    return None


def verify_clip(source: Path, seconds: float, stall_timeout: float, port: int) -> tuple[int, tuple[int, int] | None]:
    url = f"rtsp://127.0.0.1:{port}/cam1"
    server_log = tempfile.TemporaryFile(mode="w+b")
    process = subprocess.Popen(
        [sys.executable, str(SERVER), str(source), "--host", "127.0.0.1", "--port", str(port)],
        cwd=ROOT,
        stdout=server_log,
        stderr=subprocess.STDOUT,
    )
    pipeline: Gst.Pipeline | None = None
    try:
        deadline = time.monotonic() + 20.0
        while time.monotonic() < deadline:
            if process.poll() is not None:
                break
            try:
                with socket.create_connection(("127.0.0.1", port), timeout=0.2):
                    break
            except OSError:
                time.sleep(0.1)
        if process.poll() is not None:
            server_log.seek(0)
            detail = server_log.read().decode("utf-8", errors="replace").strip()
            raise RuntimeError(f"RTSP server exited ({process.returncode}): {detail}")

        pipeline = client_pipeline(url)
        sink = pipeline.get_by_name("framesink")
        if sink is None:
            raise RuntimeError("Could not find the RTSP client's appsink")
        if pipeline.set_state(Gst.State.PLAYING) == Gst.StateChangeReturn.FAILURE:
            raise RuntimeError("Could not start the RTSP client pipeline")

        first = sink.try_pull_sample(20 * Gst.SECOND)
        if first is None:
            detail = message_error(pipeline) or "Timed out waiting for the first RTSP frame"
            raise RuntimeError(detail)
        caps = first.get_caps().get_structure(0)
        width, height = caps.get_value("width"), caps.get_value("height")
        frame_size = (width, height)
        frames = 1
        started = time.monotonic()
        last_frame = started

        while time.monotonic() - started < seconds:
            sample = sink.try_pull_sample(Gst.SECOND)
            now = time.monotonic()
            if sample is None:
                detail = message_error(pipeline)
                if detail:
                    raise RuntimeError(detail)
                if now - last_frame > stall_timeout:
                    raise RuntimeError(f"No frame received for {stall_timeout:g} seconds")
                continue
            sample_caps = sample.get_caps().get_structure(0)
            current_size = (sample_caps.get_value("width"), sample_caps.get_value("height"))
            if current_size != frame_size:
                raise RuntimeError(f"Frame size changed from {frame_size} to {current_size}")
            frames += 1
            last_frame = now

        if frames < 2:
            raise RuntimeError("Fewer than two frames received")
        return frames, frame_size
    except Exception as exc:
        server_log.seek(0)
        detail = server_log.read().decode("utf-8", errors="replace").strip()
        if detail:
            raise RuntimeError(f"{exc}\nRTSP server log:\n{detail}") from exc
        raise
    finally:
        if pipeline is not None:
            pipeline.set_state(Gst.State.NULL)
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
        server_log.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--source", type=Path, help="Test one source clip")
    group.add_argument("--all", action="store_true", help=f"Test every file in {INPUT_DIR}")
    parser.add_argument("--seconds", type=float, default=30.0,
                        help="Streaming duration for one clip (default: 30 seconds; --all uses 3 seconds per clip)")
    parser.add_argument("--stall-timeout", type=float, default=8.0)
    parser.add_argument("--port", type=int, default=8554)
    args = parser.parse_args()
    if args.seconds <= 0 or args.stall_timeout <= 0:
        parser.error("seconds and stall-timeout must be positive")

    Gst.init(None)
    if args.all:
        clips = (
            sorted(path for path in INPUT_DIR.iterdir() if path.is_file() and not path.name.startswith("."))
            if INPUT_DIR.is_dir() else []
        )
        if not clips:
            parser.error(f"no files found in {INPUT_DIR}")
        duration = min(args.seconds, 3.0)
    else:
        source = args.source.expanduser().resolve()
        if not source.is_file():
            parser.error(f"video file does not exist: {source}")
        clips = [source]
        duration = args.seconds

    failures = 0
    for clip in clips:
        try:
            frames, size = verify_clip(clip, duration, args.stall_timeout, args.port)
            print(f"PASS: {clip.name} -> {frames} frames at {size[0]}x{size[1]} over {duration:g}s")
        except Exception as exc:
            failures += 1
            print(f"FAIL: {clip.name}: {exc}", file=sys.stderr)

    print(f"Result: {len(clips) - failures}/{len(clips)} clips passed")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
