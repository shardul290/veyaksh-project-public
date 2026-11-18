"""Serve a recorded video clip as a looping H.264 RTSP stream with GStreamer."""

from __future__ import annotations

import argparse
import logging
import signal
from pathlib import Path

import gi

gi.require_version("Gst", "1.0")
gi.require_version("GstPbutils", "1.0")
gi.require_version("GstRtspServer", "1.0")
from gi.repository import GLib, Gst, GstPbutils, GstRtspServer  # noqa: E402


ROOT = Path(__file__).resolve().parent
DEFAULT_INPUT_DIR = ROOT / "assets" / "rtsp_inputs"
LOG = logging.getLogger("gst_rtsp_server")


def video_dimensions(source: Path) -> tuple[int, int]:
    discoverer = GstPbutils.Discoverer.new(10 * Gst.SECOND)
    info = discoverer.discover_uri(source.as_uri())
    videos = info.get_video_streams()
    if not videos:
        raise ValueError(f"No video stream found in {source}")
    return videos[0].get_width(), videos[0].get_height()


def scaled_dimensions(width: int, height: int, max_dimension: int) -> tuple[int, int]:
    scale = min(1.0, max_dimension / max(width, height))
    out_width = max(2, int(width * scale) // 2 * 2)
    out_height = max(2, int(height * scale) // 2 * 2)
    return out_width, out_height


def make_pipeline(source: Path, width: int, height: int, fps: int, bitrate: int) -> str:
    uri = source.as_uri().replace('"', "%22")
    return (
        f'( uridecodebin uri="{uri}" '
        f"! queue ! d3d12download ! videoconvert ! videoscale ! videorate "
        f"! video/x-raw,width={width},height={height},framerate={fps}/1 "
        f"! identity sync=true "
        f"! x264enc tune=zerolatency speed-preset=ultrafast bitrate={bitrate} "
        f"key-int-max={fps * 2} bframes=0 ! h264parse config-interval=-1 "
        f"! rtph264pay name=pay0 pt=96 config-interval=1 )"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path, help="Recorded video file to stream")
    parser.add_argument("--host", default="0.0.0.0", help="Interface to bind (default: all interfaces)")
    parser.add_argument("--port", type=int, default=8554)
    parser.add_argument("--mount", default="/cam1", help="RTSP mount path (default: /cam1)")
    parser.add_argument("--max-dimension", type=int, default=1280,
                        help="Maximum encoded frame width or height (default: 1280)")
    parser.add_argument("--fps", type=int, default=30)
    parser.add_argument("--bitrate", type=int, default=4000,
                        help="H.264 encoder bitrate in kbit/s (default: 4000)")
    args = parser.parse_args()

    source = args.source.expanduser().resolve()
    if not source.is_file():
        parser.error(f"video file does not exist: {source}")
    if args.port < 1 or args.port > 65535 or args.fps < 1 or args.max_dimension < 2:
        parser.error("port, fps, and max-dimension must be within valid positive ranges")
    mount = "/" + args.mount.strip("/")

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    Gst.init(None)
    try:
        input_width, input_height = video_dimensions(source)
    except Exception as exc:
        LOG.error("Could not inspect input video %s: %s", source, exc)
        return 1
    width, height = scaled_dimensions(input_width, input_height, args.max_dimension)
    launch = make_pipeline(source, width, height, args.fps, args.bitrate)

    factory = GstRtspServer.RTSPMediaFactory()
    factory.set_launch(launch)
    factory.set_shared(True)
    factory.set_eos_shutdown(False)

    def rewind_pipeline(pipeline: Gst.Element) -> bool:
        if not pipeline.seek_simple(
            Gst.Format.TIME, Gst.SeekFlags.FLUSH | Gst.SeekFlags.KEY_UNIT, 0
        ):
            LOG.error("Could not seek to the beginning of %s", source)
        else:
            LOG.info("Clip reached EOS; restarted %s", source.name)
        return GLib.SOURCE_REMOVE

    def on_downstream_event(pad: Gst.Pad, probe: Gst.PadProbeInfo,
                            pipeline: Gst.Element) -> Gst.PadProbeReturn:
        event = probe.get_event()
        if event is not None and event.type == Gst.EventType.EOS:
            # Stop EOS reaching rtph264pay/RTSP so the media session remains live.
            GLib.idle_add(rewind_pipeline, pipeline)
            return Gst.PadProbeReturn.DROP
        return Gst.PadProbeReturn.OK

    def on_media_configure(_factory: GstRtspServer.RTSPMediaFactory,
                           media: GstRtspServer.RTSPMedia) -> None:
        LOG.info("RTSP media configured for %s", source.name)
        pipeline = media.get_element()
        payloader = pipeline.get_by_name("pay0")
        if payloader is None:
            LOG.error("RTSP media has no pay0 element; cannot loop input")
            return
        sink_pad = payloader.get_static_pad("sink")
        sink_pad.add_probe(
            Gst.PadProbeType.EVENT_DOWNSTREAM, on_downstream_event, pipeline
        )

    factory.connect("media-configure", on_media_configure)
    server = GstRtspServer.RTSPServer()
    server.set_address(args.host)
    server.set_service(str(args.port))
    mounts = server.get_mount_points()
    mounts.add_factory(mount, factory)

    loop = GLib.MainLoop()
    if server.attach(None) == 0:
        LOG.error("Failed to bind RTSP server to %s:%s", args.host, args.port)
        return 1

    endpoint_host = "localhost" if args.host in ("0.0.0.0", "::") else args.host
    print(f"Serving {source.name} ({input_width}x{input_height}) as {width}x{height} at {args.fps} FPS", flush=True)
    print(f"RTSP endpoint: rtsp://{endpoint_host}:{args.port}{mount}", flush=True)
    for signum in (signal.SIGINT, signal.SIGTERM):
        signal.signal(signum, lambda _signal, _frame: loop.quit())
    try:
        loop.run()
    finally:
        LOG.info("Stopping RTSP server")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
