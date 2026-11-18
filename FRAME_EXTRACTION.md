# M1 frame extraction

`extract_frames.py` extracts one frame per source-time interval from a local video or RTSP stream. GStreamer decodes the input and supplies each frame's presentation timestamp (PTS); OpenCV computes a perceptual hash to skip near-identical samples and writes JPEGs.

Run it from the project directory with the MSYS2 UCRT64 Python described in [GSTREAMER_SETUP.md](GSTREAMER_SETUP.md). The project's standard `.venv` uses an OpenCV wheel without GStreamer and does not include the Python GStreamer bindings.

```bash
python extract_frames.py assets/rtsp_inputs/dehradun_clip01.mp4 --out assets/extracted_frames
python extract_frames.py rtsp://127.0.0.1:8554/cam1 --city dehradun --clip clip01 --max-frames 60 --out assets/extracted_frames
```

When a local filename starts with `city_clip` (for example `roorkee_clip01.mp4`), the identifiers are inferred. For RTSP or other filenames, pass `--city` and `--clip`. Frame names use `{city}_{clip}_{frameno:04d}.jpg`, for example `roorkee_clip01_0001.jpg`. Existing matching files are detected so numbering continues instead of overwriting them.

The corresponding `{city}_{clip}_metadata.csv` records `city`, `clip`, `timestamp`, and `filename`, plus the original source path, PTS in milliseconds, frame number, and SHA-256 digest. Local file `timestamp` values use the video's elapsed presentation time (for example `12.000s`). RTSP values are UTC estimates based on when extraction starts plus stream PTS; RTSP itself commonly supplies elapsed media time rather than a wall-clock capture timestamp. For camera capture time, the camera and stream must provide synchronized clock metadata.

`--interval` defaults to 1 second. It is measured on source PTS rather than processing wall time, so delayed processing does not shift file-video samples. `--duplicate-distance` defaults to a perceptual-hash Hamming distance of 4; samples at or below the threshold compared with the previous saved frame are skipped. Tune the threshold for the camera and scene. `--max-frames` is useful for bounded RTSP runs; without it, a live stream runs until interrupted.
