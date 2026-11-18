# GStreamer development setup (Windows)

This development system has GStreamer 1.28.8 installed. The official MSVC x86_64 distribution is at:

```text
%LOCALAPPDATA%\Programs\gstreamer\1.0\msvc_x86_64
```

The installer configured `GSTREAMER_1_0_ROOT_MSVC_X86_64` and added its `bin` directory to the user PATH. It includes the core, base/good/bad plugin libraries, and the RTSP server runtime and development libraries.

## Python and OpenCV environment

The project's existing `.venv` uses the standard `opencv-python` wheel, whose build has GStreamer disabled. For Python GObject and OpenCV with a working GStreamer backend, use the MSYS2 **UCRT64** environment. The UCRT64 package set installed on this system contains:

- GStreamer core, base, good and bad plugins
- `gst-rtsp-server` and the Python GStreamer bindings
- Python GObject bindings (`python-gobject`)
- Python OpenCV (`python-opencv`), built with GStreamer video I/O

Open **MSYS2 UCRT64** from the Start menu. Avoid mixing its DLLs with the separate MSVC installation; the UCRT64 shell selects the matching libraries automatically. To check the stack:

```bash
python -c 'import gi, cv2; gi.require_version("Gst", "1.0"); gi.require_version("GstRtspServer", "1.0"); from gi.repository import Gst, GstRtspServer; Gst.init(None); print(gi.__version__, Gst.version_string(), GstRtspServer.RTSPServer.__name__, cv2.__version__)'
```

To verify OpenCV can receive frames through GStreamer:

```bash
python -c 'import cv2; cap=cv2.VideoCapture("videotestsrc num-buffers=2 ! videoconvert ! video/x-raw,format=BGR ! appsink", cv2.CAP_GSTREAMER); ok, frame=cap.read(); print("backend:", cap.getBackendName() if cap.isOpened() else "closed", "frame:", ok, None if frame is None else frame.shape); cap.release(); raise SystemExit(0 if ok else 1)'
```

The synthetic source should report the `GSTREAMER` backend and a `(240, 320, 3)` frame. Use this Python environment for code that needs GStreamer; installing the PyPI `opencv-python` package into it would replace the GStreamer-enabled OpenCV build.

The current `test_rtsp.py` checks a MediaMTX RTSP stream through OpenCV's FFmpeg backend. It remains suitable for the project's RTSP continuity check and is separate from the synthetic GStreamer-backend check above.
