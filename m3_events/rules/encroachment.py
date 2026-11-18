from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .geometry import point_in_polygon


@dataclass
class DwellState:
    entered_at: float
    last_seen_at: float
    confidence_sum: float
    observations: int = 1
    emitted: bool = False


@dataclass
class EncroachmentRule:
    frame_width: int
    frame_height: int
    max_gap_s: float = 1.0
    states: dict[tuple[int, str], DwellState] = field(default_factory=dict)

    def update(self, detection: dict[str, Any], zone: dict[str, Any]) -> dict[str, Any] | None:
        key = (detection["track_id"], zone["zone_id"])
        x1, y1, x2, y2 = detection["bbox"]
        x = (x1 + x2) / 2.0 / self.frame_width
        y = (y1 + y2) / 2.0 / self.frame_height
        if not point_in_polygon(x, y, zone["polygon"]):
            self.states.pop(key, None)
            return None

        state = self.states.get(key)
        if state is None or detection["ts"] - state.last_seen_at > min(self.max_gap_s, zone["max_gap_s"]):
            state = DwellState(
                entered_at=detection["ts"],
                last_seen_at=detection["ts"],
                confidence_sum=detection["conf"],
            )
            self.states[key] = state
            return None

        if detection["ts"] <= state.last_seen_at:
            return None
        state.last_seen_at = detection["ts"]
        state.confidence_sum += detection["conf"]
        state.observations += 1
        dwell_s = state.last_seen_at - state.entered_at
        if dwell_s < zone["max_dwell_s"] or state.emitted:
            return None

        state.emitted = True
        return {
            "type": "encroachment",
            "track_id": detection["track_id"],
            "class": detection["class"],
            "conf": state.confidence_sum / state.observations,
            "zone_id": zone["zone_id"],
            "frame": detection["frame"],
            "video_ts_s": detection["ts"],
            "bbox": list(detection["bbox"]),
        }
