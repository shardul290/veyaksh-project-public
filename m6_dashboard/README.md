# VEYAKSH traffic operations dashboard

For manual startup, run `start_dashboard.ps1` from the project root in PowerShell.
The launcher pins the project path and checks for the standard dashboard before
reusing a running server. Open its printed URL; press Ctrl+Shift+R if an older
browser tab still displays the Sentinel page.

Run from the repository root:

```powershell
.\.venv\Scripts\python.exe -m uvicorn m6_backend.main:app --host 127.0.0.1 --port 8000
```

Open http://127.0.0.1:8000/ for the integrated map, CCTV recordings and evidence
trail, or http://127.0.0.1:8000/history for the permanent event record. Restart
an older backend after updating this code. The API and dashboard share an origin.

## New surveyed recordings

Add browser-compatible H.264 MP4 files at these exact paths:

- `m6_backend/media/camera_recordings/point_1.mp4`: N01 / C01,
  29°51'50.2"N 77°53'43.5"E.
- `m6_backend/media/camera_recordings/point_2.mp4`: N02 / C02,
  29°51'53.8"N 77°54'03.0"E.

The CCTV panel discovers added recordings within two seconds without a restart.
Click either blue map marker or the corresponding camera selector. The separate
video upload button has been removed. `configs/cameras.json` supports multiple
saved recordings per camera; set the actual timezone-aware `start_ts` when known.
See `m6_backend/media/camera_recordings/README.md` for more details.

The old clips are invalid for these surveyed locations. On first upgrade,
existing events become archived legacy records in History. They do not activate
the new camera pins, appear in the evidence queue, or expose their old footage.
No historical records are deleted. Only the two surveyed nodes appear on the map.

## Analyse and publish events

Adding a recording enables playback; it does not run inference. Run the existing
YOLO/ByteTrack and M3 rule pipeline on the new footage with zones calibrated for
each camera. Publish the resulting JSONL events through M5 with the matching
configuration. Set `source_start_ts` to the actual recording start time first;
the placeholder intentionally prevents publishing events with invented times.

```powershell
.\.venv\Scripts\python.exe m5_edge_agent/send_events.py --config m5_edge_agent/point_1.config.json --events runs/point_1_events.jsonl
.\.venv\Scripts\python.exe m5_edge_agent/send_events.py --config m5_edge_agent/point_2.config.json --events runs/point_2_events.jsonl
```

The default `m5_edge_agent/config.json` also targets Point 1. Existing sample
events and old zone examples are not analyses of the new recordings. Do not use
them as real evidence for these locations. The sender extracts snapshots and
three-second clips using FFmpeg, or links the source at the event offset when
FFmpeg is unavailable. Evidence filenames include a recording/event fingerprint
so a later recording does not overwrite earlier clips.

## Review workflow

Click Enable sound once after each page load to allow browser audio. Newly
received events play a tone. Camera markers remain blue until their location
has an event awaiting review, then blink red until all its active events are
reviewed. Polling runs every two seconds. Reduced-motion preferences disable
animation while retaining the red alert indicator.

Review evidence at the bottom of Overview:

- False alert saves a decision and removes the event from the evidence trail.
- Take action saves a decision and opens response options.
- Alert authorities saves a timestamped local request. No external delivery
  service or authority contact is configured; no message is sent.

All events remain in History with their source clip timestamp, exact video
offset (when provided), current status and decision timestamp. The activity trail
retains the separate timestamps for each action. Review timestamps are stored in
UTC and displayed in IST; the source timestamp is preserved unchanged. Duplicate
submissions of the same review do not create duplicate audit entries. Conflicting
reviews return an error rather than overwriting an existing decision.

## Verification and data

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
node --check m6_dashboard/dashboard.js
```

Tests use an isolated database and temporary HTTP server. Local socket access
must be allowed. APIs include `/health`, `/nodes`, `/cameras`, `/events`,
`/events-summary`, `/events/{id}/actions` and `PATCH /events/{id}/status`.
`/docs` provides request schemas. `/events` supports status, active-only,
node/type and ID pagination filters. The former `/map` route opens Overview.

Database files, recorded videos and generated evidence remain local and ignored
by Git. Set `EVENTS_DB_PATH` to change the database location. Keep evidence under
`m6_backend/media/` for serving.
