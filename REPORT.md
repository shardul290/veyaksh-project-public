# Project verification report

## Environment

- Windows / PowerShell workspace, Python 3.11.9 project virtual environment
- OpenCV from the project `.venv`
- MediaMTX 1.21.3 installed through winget
- FFmpeg 9.0.2 (Gyan full build) installed through winget
- Input: `assets/traffic_demo.mp4`, H.264, 2160x3840, 30 FPS, 397 frames, 13.23 seconds

## Existing functionality

- M1 `ingest_video.py assets\\traffic_demo.mp4`: OpenCV read all 397 frames.
- M6 FastAPI remains in `m6_backend/main.py`; the existing SQLite database and event visualizer were not modified for RTSP. Three `wrong_way` records were already present.

## RTSP setup

Added `mediamtx.yml`, `test_rtsp.py`, and `RTSP_SETUP.md`.

- MediaMTX config validated with `mediamtx.exe --validate-conf=mediamtx.yml` — `configuration file is valid`.
- MediaMTX started from project root; RTSP/TCP listener came up on port 8554.
- Published original input to `rtsp://localhost:8554/traffic` with FFmpeg `-re -stream_loop -1`. The validated publisher transcodes to 640x1138 at 15 FPS to reduce client decode load.
- Ran `.venv\\Scripts\\python.exe test_rtsp.py` using actual `cv2.VideoCapture` against that RTSP URL.

Final test output:

```text
PASS: RTSP connection opened with OpenCV FFmpeg backend.
PASS: First frame received; dimensions 640x1138.
PASS: Stream kept delivering frames for 30.0 seconds.
Frames received: 479 (16.0 FPS observed)
Frame dimensions: 640x1138
Source clip duration: 13.23 seconds; monitored about 2.3 clip durations.
```

MediaMTX logs confirmed an H.264 stream published and read on path `traffic`. The final run had no `reader is too slow` warnings or OpenCV H.264 decode warnings.

## Tuning note

A first test used H.264 stream copy at the original 2160x3840 / 30 FPS. Connection and frames worked, but the MediaMTX log showed `reader is too slow, discarding ... frames` and OpenCV logged H.264 decode warnings. The final command resizes/rate-limits to 640x1138 / 15 FPS; a 30-second OpenCV run stayed continuous, exceeding two source-video cycles.

## Run instructions

See `RTSP_SETUP.md` for the winget install commands, three-terminal CMD/PowerShell procedure, and exact stream/test commands. Stop the server and publisher with Ctrl+C in their respective terminals.

