# M3 event rules

`event_engine.py` consumes one video's M2/ByteTrack JSONL export. It evaluates each tracked vehicle against that camera's lane and no-parking polygons, then writes the intermediate event JSONL consumed by `m5_edge_agent/send_events.py`.

## Configure camera zones

Use the calibration helper with a frame from the target camera. Draw one polygon per directional carriageway/lane group, set the normal permitted travel direction for each, and add a no-parking region if needed. Opposing traffic flows need separate polygons and headings; do not combine both directions into one ROI. The helper writes normalized coordinates and the source frame size:

```powershell
python -m m3_events.calibrate_zones --input data/test.mp4 --frame 0 --node-id N01 --cam-id C01 --output configs/zones/C01.json
```

For an image, pass its path to `--input`; for a video, use `--frame` to choose a clear frame. In the window, click polygon vertices and press Enter to finish; Backspace undoes the last point and R resets the polygon. For each heading, click a point on that lane and then a second point in the permitted direction. Answer yes to “Add another directional lane?” to calibrate the opposing flow. The tool then asks whether to add an encroachment polygon and its dwell threshold (15 seconds by default).

The example file is a template only. Review the generated values before running on real footage:

- Set `frame_size` to the source video resolution used to generate the track export.
- Draw each polygon around the actual lane or no-parking area. Coordinates are normalized fractions from 0 to 1, with `(0, 0)` at the upper-left.
- Set each lane's allowed heading in image coordinates: 0° right, 90° down, 180° left, 270° up.
- Set the camera identifiers and thresholds with the project lead. The example polygons and thresholds are illustrative only.

## Rules in this first pass

- `wrong_way` compares the vehicle-centre path over a sliding window with the lane's allowed heading. Defaults require at least 8 observations, 0.75 seconds of movement, displacement of at least 1.5% of the frame diagonal, and 3 consecutive wrong-heading windows. A track gap longer than `max_track_gap_s` resets its history.
- `min_track_class_consistency` can suppress tracks whose predicted class changes repeatedly. It is a track-quality filter, not a class correction; set it only when class flicker is causing unreliable track IDs.
- `boundary_margin_px` can make points close to a lane boundary ambiguous and exclude them from wrong-way decisions. This avoids treating a vehicle near a divider as belonging confidently to either flow.
- `encroachment` starts timing when a vehicle-centre enters a no-parking polygon. It emits once after `max_dwell_s`; a detection gap beyond the zone's `max_gap_s` restarts dwell timing. Leaving the polygon resets the rule.

These rules need calibrated zones and clip review. The detector's class confidence is not a calibrated event probability; tracking errors, occlusion, perspective, and sparse detections can still cause missed or false events.

## Run

The one-command pipeline first creates the YOLO11s/ByteTrack JSONL, then runs both M3 rules:

```powershell
python -m m3_events.run_video_pipeline --video data/test.mp4 --model models/best.pt --zones configs/zones/C01.json --output-dir runs/m3 --device 0
```

Use `--device cpu` if no supported GPU is available. Outputs are `<video>_tracks.jsonl` and `<video>_events.jsonl` under the output directory. The rule engine is offline and does not send anything to the backend.

To run the complete local demo, publish generated events to M6, and open the frontend, use:

```powershell
python scripts/run_detection_dashboard.py --video data/test.mp4 --model models/best.pt --zones configs/zones/C01.json --device cpu
```

This launcher starts the local API if needed, runs detection and rules, sends event evidence through M5, and opens `http://127.0.0.1:8000/`. It defaults to CPU, can reuse a previous tracking export with `--tracks runs/m3/test_tracks.jsonl`, and leaves the dashboard API running after the command exits. With no system FFmpeg executable, event links use the original local video and seek to the event time; when FFmpeg is installed, M5 creates short event clips instead.

This is a saved-video demo. Live RTSP inference is not yet connected to this launcher or the dashboard.

You can also run the two existing stages separately:

```powershell
python -m m3_events.event_engine --tracks tracks/campus_best.jsonl --zones configs/zones/C01.json --output data/events.jsonl
python m5_edge_agent/send_events.py --events data/events.jsonl
```

Before the M5 command, set its `source_video`, `source_start_ts`, `node_id`, and `cam_id` to match the same video and zone file. The event engine writes `frame` and `video_ts_s`; M5 turns those offsets into the timestamp, evidence clip, snapshot, and M6 event payload.

## Event class contract

The VEYAKSH detector taxonomy includes `bus_truck`, while the prior event schema did not. This branch adds that class to the event schema, M5 validation, and M6 API model so the custom detector's class name passes end to end. Confirm this contract addition with the project maintainer before merging or deploying it.
