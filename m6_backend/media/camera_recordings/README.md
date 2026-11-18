# Surveyed camera recordings

Add the new recordings to this folder using these exact filenames:

| File | Camera | Location |
| --- | --- | --- |
| `point_1.mp4` | N01 / C01 | 29°51'50.2"N 77°53'43.5"E (29.8639444444, 77.8954166667) |
| `point_2.mp4` | N02 / C02 | 29°51'53.8"N 77°54'03.0"E (29.8649444444, 77.9008333333) |

Use browser-compatible H.264 MP4 video. The CCTV panel discovers the files
automatically within two seconds, with no restart needed. Old demo clips are
not linked to these cameras.

Adding a recording makes it available for playback; detection is a separate
step. Before publishing detector events, calibrate each camera's zones and set
the actual timezone-aware recording start time. Do not invent this timestamp.
Set `start_ts` in `configs/cameras.json` once known; until then the dashboard
honestly shows "Not recorded" for the recording start.

For each camera, use its matching sender config in `m5_edge_agent/` with
`send_events.py --config ... --events <analysed-events.jsonl>`. Set
`source_start_ts` in that config first. The sender records each event's source
timestamp and exact video offset, plus a snapshot and event clip. Generated
evidence is kept in a separate folder for each camera.

The dashboard requires one click on Enable sound after each page load. New
events then play an alert tone. Blue markers blink red for unreviewed events.
False alerts leave the evidence queue but remain in History. Take action and
authority alert requests each retain a separate UTC audit timestamp, displayed
in IST. Authority requests are recorded locally, not delivered externally.
