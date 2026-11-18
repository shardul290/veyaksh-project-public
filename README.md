# Video Ingest and Event Capture (M1 + M6)

A minimal Python 3.11 project that reads video frames with OpenCV and stores submitted events in SQLite through FastAPI.

## Setup (Windows PowerShell)

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
```

## M1: inspect and read video frames

A short sample clip is included at `assets/traffic_demo.mp4`:

```powershell
python ingest_video.py assets/traffic_demo.mp4
```

## M6: run the API

From this project directory:

```powershell
uvicorn m6_backend.main:app --reload
```

Submit and retrieve an event from a second terminal:

```powershell
curl.exe -X POST http://localhost:8000/events -H "Content-Type: application/json" -d '{"type":"wrong_way"}'
curl.exe http://localhost:8000/events
```

Events are persisted to `events.sqlite3`. Set `EVENTS_DB_PATH` to use another SQLite file. API docs: `http://localhost:8000/docs`.

RTSP status and failure details are in `REPORT.md`.

## Visualize saved API events on the video

With the API running and events stored, open a second terminal in this directory and run:

```powershell
.\.venv\Scripts\python.exe visualize_events.py
```

The script reads `GET /events` from `http://localhost:8000`, overlays the latest four event records on the video, and writes `assets/traffic_demo_events.mp4`. These are recorded API events displayed over the footage; they are not detections inferred from the video.

## Frame-by-frame vehicle analysis preview

The analysis stage runs the NanoDet object detector on every source frame and creates a separate annotated MP4 plus a visual HTML/JSON report. This is object detection only; it does not identify unique tracked vehicles or infer wrong-way violations.

From the project directory, run:

```powershell
.\.venv\Scripts\python.exe analyze_video.py
```

Outputs:

- `assets/traffic_demo_analysis.mp4` — frame-by-frame preview with vehicle boxes, labels, confidence, frame number, and processing speed.
- `reports/traffic_analysis.html` — visual summary with embedded video and counts by detected vehicle class.
- `reports/traffic_analysis.json` — machine-readable analysis details.

The included NanoDet ONNX model and its OpenCV Zoo helper/license are in `models/`. CPU processing can take a few minutes for the full 397-frame clip. You can use another input video with `python analyze_video.py path\to\video.mp4`.

## RTSP simulated stream

See [RTSP_SETUP.md](RTSP_SETUP.md) for MediaMTX, FFmpeg looped publishing, and the OpenCV continuity check.

# VEYAKSH

**See. Sense. Understand.**
Edge-AI road-safety video analytics for Indian roads.

VEYAKSH reads video from a camera (or a file), detects and tracks vehicles, flags unsafe events such as wrong-way driving, and shows them on a map. Video is processed where it is captured; only a small event record (about 1 KB of JSON, a plate crop and a short clip) is sent onward. Raw video is not uploaded or stored.

We are building the **software layer first**, on a laptop. Hardware (Jetson / Hailo box) comes later, after benchmarking and after the first customer pilot.

---

## 1. The big picture

```
Camera / video file / RTSP
        |
        v
  M1 Ingest  ->  M2 Detect + Track  ->  M3 Event engine  ->  M4 ANPR  ->  M5 Edge agent
   frames          objects with IDs        events             plate         JSON + clip
                                              ^                              |
                                              |                              v
                                 zone config (M7 writes it)          M6 Backend + Dashboard
                                                                      (database, map, alert)
```

Modules talk to each other **only through two contracts**:
- `schema/event.schema.json`: the shape of an event.
- `configs/zones/<cam_id>.json`: lanes, allowed direction and no-park zones for a camera.

Because of this, each person can build and test their module alone. Do not change a contract without discussing it first.

---

## 2. One example, end to end

A two-wheeler enters the junction against the flow of traffic.

1. **M1** pulls the frame from the camera and keeps the last few seconds in a buffer.
2. **M2** sees "a two-wheeler at this position" and gives it ID 41. In the next frames it stays ID 41 and we get its path.
3. **M3** reads the zone config. Lane Z1 allows travel at 90 degrees; ID 41 is moving at about 270 degrees for several frames. It emits a `wrong_way` event.
4. **M4** crops the number plate from the event frame and reads it: `UK08XXXX`, 71 percent confidence.
5. **M5** packs the event JSON, the plate image and the short clip, and sends them to the backend. If the network is down it queues and retries. Raw video is deleted.
6. **M6** stores the event. On the dashboard map, the junction blinks red; clicking it plays the clip.
7. **M7** is what created the zone config in the first place, by letting someone draw the lane on a camera frame.

---

## 3. Modules in detail

### M1: Ingest
**What it does:** gets frames from any source and keeps a short rolling video buffer.
**Input:** RTSP URL, video file, USB or phone camera.
**Output:** a stream of frames (with timestamps) and the last 3 to 5 seconds of video, so an event can save a clip of what just happened.
**Why it exists:** every other module should not care where video comes from. Swapping a file for a real camera must be a config change, not a code change.
**Tech:** OpenCV, GStreamer, ffmpeg, MediaMTX (to fake an RTSP stream from a file).
**Week 1:** read a video file and print frame count; loop a video as a fake RTSP stream and read it. If MediaMTX blocks you, read the file directly and report it.
**Done when:** the same code reads both a file and an RTSP URL.

### M2: Detect + Track
**What it does:** finds vehicles and people in each frame and follows each one with a stable ID.
**Input:** frames from M1.
**Output:** for each frame, a list of objects: `track_id`, `class`, `bbox` (box position).
**Why it exists:** events are about behaviour over time (direction, dwell). That needs identity, not just per-frame detection.
**Tech:** the selected VEYAKSH YOLO11s `best.pt` checkpoint with Ultralytics ByteTrack for detection and tracking. Keep inference behind one adapter so the event rules consume a stable track format. `yolo11n.pt` remains the COCO baseline used for Task 2.7 comparison; its classes do not cover the full VEYAKSH taxonomy.
**Week 1:** run on the test video, save an overlay video (boxes + IDs), and list where Indian traffic breaks it (auto-rickshaw, e-rickshaw, cart, two-wheeler).
**Done when:** overlay video exists and a failure list is written in `DECISIONS.md`.

### M3: Event engine (rule plugins)
**What it does:** turns tracks plus zone config into events.
**Input:** tracks from M2 and the zone config.
**Output:** intermediate rule events; M5 adds the ISO timestamp and evidence paths to produce the payload in `schema/event.schema.json`.
**Why it exists:** this is the "intelligence". Each feature is an independent stateful rule in `m3_events/rules/`; the engine feeds it tracked objects and camera zones. New features can be added without rewriting the detector.
**Implemented rules:**
- `wrong_way`: a vehicle track moves outside a lane's allowed direction and tolerance over a sustained path.
- `encroachment`: a vehicle track remains inside a no-parking polygon beyond its configured dwell time.

The offline rule engine reads the per-detection JSONL produced by M2/ByteTrack and writes intermediate event JSONL for the M5 sender. Camera-specific polygons and thresholds live in `configs/zones/`; `configs/zones/C01.example.json` is only a template and must be calibrated to the actual camera before its events are treated as meaningful. Use `python -m m3_events.calibrate_zones --input <camera-frame-or-video> --output configs/zones/C01.json` to draw them. Polygon coordinates are normalized from 0 to 1. Heading convention: 0° right, 90° down, 180° left, 270° up in image coordinates.

Run the complete detector → ByteTrack → rule pipeline for one clip:

```powershell
python -m m3_events.run_video_pipeline --video data/test.mp4 --model models/best.pt --zones configs/zones/C01.json --output-dir runs/m3
```

This writes track and intermediate event JSONL files under `runs/m3/`; it does not upload events. To run only the rules over an existing track export:

```powershell
python -m m3_events.event_engine --tracks tracks/campus_best.jsonl --zones configs/zones/C01.json --output data/events.jsonl
```

To forward those events, set `source_video`, `source_start_ts`, `node_id`, and `cam_id` in `m5_edge_agent/config.json` to match the same clip and camera, then run:

```powershell
python m5_edge_agent/send_events.py --events data/events.jsonl
```

For the complete local saved-video demo (detector → ByteTrack → M3 rules → M5 → M6 dashboard), run:

```powershell
python scripts/run_detection_dashboard.py --video data/test.mp4 --model models/best.pt --zones configs/zones/C01.json --device cpu
```

The launcher starts the API if needed and opens the dashboard. Pass `--tracks <existing-tracks.jsonl>` to reuse tracking results. Live RTSP inference is not yet connected to this demo.

The event engine supplies rule decisions, not calibrated camera zones. Validate zone geometry and thresholds against a representative clip before using alerts operationally.
The emitted `conf` is the mean detector confidence over the track segment used by the rule; it is not a calibrated probability that a traffic violation occurred.

### M4: ANPR (number plate reading)
**What it does:** finds the plate on an event frame and reads its text.
**Input:** the event frame and the vehicle's box.
**Output:** `plate.text`, `plate.conf`, and a plate crop image. The plate can be null; an event without a plate is still valid.
**Why it exists:** a plate makes an event actionable for authorities. We do not issue challans; we provide evidence and a plate feed.
**Tech:** a plate detector plus an open-source OCR.
**Week 1:** spike only. Measure the read rate on Indian plates and the minimum plate width in pixels needed for a reliable read. That tells us the camera resolution and angle we require. Decision on Nov 7: M4 is v1 or v2.
**Done when:** the numbers and the decision are in `DECISIONS.md`.

### M5: Edge agent
**What it does:** packages events and sends them to the backend reliably.
**Input:** event, plate image, clip from M3, M4 and M1.
**Output:** an HTTPS POST to the backend; a local retry queue; raw clips purged after sending.
**Why it exists:** field networks drop. The device must not lose events and must not keep video.
**Tech:** Python, a small SQLite queue.
**Week 1:** build event to JSON, plate crop and clip save, POST to the backend, retry on failure.
**Done when:** with the backend stopped, events queue; once it is back, they arrive.

### M6: Backend + Dashboard
**What it does:** receives events, stores them, and shows them on a map.
**Input:** event JSON, plate image, clip.
**Output:** database rows; a Leaflet map where the node blinks red on a new event; click opens the clip.
**Why it exists:** the operator should not watch 1000 video screens. The dashboard stays quiet until something happens.
**Tech:** FastAPI, SQLite (Postgres later), Leaflet + OpenStreetMap.
**Week 1:** `POST /events` and `GET /events`, then a map that shows each event as a point with a red blink and a clip view.
**Done when:** a posted event appears on the map and its clip plays.

### M7: Calibration tool
**What it does:** lets a person draw lanes, set the allowed direction, and mark no-park polygons on a camera frame, then writes the zone config.
**Input:** one frame from a camera.
**Output:** `configs/zones/<cam_id>.json`.
**Why it exists:** every camera sees a different road. Hand-writing coordinates does not scale beyond a pilot.
**Week 1:** **not built.** We write the config by hand (see `configs/zones/C2.json`). Planned for Week 3 or later; whoever is free picks it up.

---

## 4. Event and zone formats

Event (`schema/event.schema.json`):
```json
{
  "node_id": "N01", "cam_id": "C2", "ts": "2026-11-05T10:22:31+05:30",
  "type": "wrong_way", "track_id": 41, "class": "two_wheeler", "conf": 0.87,
  "zone_id": "Z1",
  "plate": {"text": "UK08XXXX", "conf": 0.71, "img": "p_41.jpg"},
  "clip": "c_41.mp4"
}
```
`type`: `wrong_way`, `encroachment`.
`class`: `car`, `two_wheeler`, `auto`, `e_rickshaw`, `bus_truck`, `cart`, `pedestrian`, `other`.

Zone config (`configs/zones/C2.json`) holds lane polygons with `allowed_dir_deg` and `tol_deg`, and no-park polygons with `max_dwell_s`.

---

## 5. Week 1 plan (Nov 1 to 8)

| Day | Focus |
|---|---|
| Sun Nov 1 | Repo, contracts, environments |
| Mon Nov 2 | M2 runs on a clip; M6 endpoint up; M1 reads a file |
| Tue Nov 3 | M2 overlay video; M1 RTSP; M6 endpoint stores events; ANPR spike starts |
| Wed Nov 4 | Golden test clip chosen and labelled; zone config for C2; M6 map |
| Thu Nov 5 | First end-to-end demo with a hardcoded wrong-way rule |
| Fri Nov 6 | Rule driven by zone config; clip view; retry queue |
| Sat Nov 7 | Precision/recall, FPS and latency table; ANPR decision |
| Sun Nov 8 | Update this doc with real numbers; plan Week 2 |

**Not in v1:** e-challan generation, emergency-vehicle signal override, siren detection, fleet management, over-the-air updates.

---

## 6. Working rules
- `main` always runs. One branch per module (`m2/<topic>`); merge through a merge request.
- No merge to `main` unless the module ran on the golden test clip (`data/golden/`).
- Every spike ends with an entry in `DECISIONS.md`: what you tried, the number, the decision.
- Daily standup at 10:30: done, next, blocked. Push by 6 PM. Escalate any blocker over 30 minutes.
- `schema/` and `configs/zones/` are contracts. Change them only after discussion.

## 7. Quick start
```
python -m venv .venv
.venv\Scripts\activate          # Windows;  Mac/Linux: source .venv/bin/activate
pip install -r requirements.txt
python m2_detect_track/export_tracks_jsonl.py --video data/test.mp4 --model models/best.pt --kind veyaksh --output tracks/test_best.jsonl
uvicorn m6_backend.main:app --reload
```

## 8. Repo layout
```
docs/architecture.md     full architecture and Week 1 definition of done
DECISIONS.md             log of experiments and decisions
schema/                  event contract
configs/zones/           per-camera zone configs
data/golden/             golden test clip and labels
m1_ingest/ ... m6_dashboard/   one folder per module
scripts/run_pipeline.py  single entry point, wired up as modules land
```
