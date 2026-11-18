# Shardul branch change log

This document summarizes the six commits that were on `shardul` and not yet on `development` when this file was prepared. The comparison base was `b4fa1ef` (`origin/development`); the branch head was `40a52e6`.

## Commits

| Commit | Subject | Summary |
| --- | --- | --- |
| `415b933` | Add video ingest, event API, and analysis | Added video ingestion and RTSP setup, initial FastAPI event handling, traffic analysis/visualization tools, model files, and project documentation. This historical commit also added generated MP4s, a project ZIP, and `auto.key`; later commits deleted those files from the branch's current tree. |
| `4359049` | Merge remote changes | Merged the remote README documentation updates into the branch. |
| `3f810e2` | Add frame extraction and ignore local datasets | Added GStreamer RTSP and frame-extraction scripts and setup documentation, added ignore rules for local footage/datasets, and deleted the earlier generated videos, archive, and `auto.key` from the current tree. |
| `7d0dafa` | Complete Day 1 sample data, database, API, and simulator | Added the event schema, 15 sample events, SQLite database setup and placeholder nodes, FastAPI endpoints, sample media, and the sample-event simulator. It also added a small synthetic MP4, which was later removed from the current tree. |
| `242463e` | Complete live event demo and dashboard details | Expanded the dashboard with the live map, event polling and pin blink, event details, type filter, sound controls, and a demo screenshot; refined the privacy page and synthetic clip. |
| `40a52e6` | Connect M5 event sender to M6 dashboard | Added the M5 JSONL sender, config, and five sample events; added event-frame snapshot storage and display, updated dashboard run instructions and ignore rules, and removed the synthetic MP4 from the current tree. |

## Branch result at `40a52e6`

- The branch combines the video-ingest/frame-extraction work, M6 sample data/API/database/dashboard work, and the M5-to-M6 sender integration.
- The five M5 sample events were sent to the API. Their snapshot and clip URLs returned HTTP 200, and generated clips were three seconds long.
- The current tree ignores local `.db` files, source videos, generated event evidence, and dataset archives. No video, video archive, or raw footage/dataset archive is tracked at the branch head.

## Git history note

Deleting a file in a later commit removes it from the current branch tree, but its earlier Git object remains in branch history. In particular, `415b933` contains video/archive blobs and `auto.key`; the later deletion commit does not erase those historical objects. This log does not rewrite branch history. If the team requires those objects removed from the repository itself, coordinate a history rewrite and key rotation with the repository maintainers before merging.
