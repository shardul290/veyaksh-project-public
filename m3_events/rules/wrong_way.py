from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from math import atan2, hypot, degrees
from typing import Any

from .geometry import angular_difference_deg, point_in_polygon


def boundary_distance_px(x: float, y: float, polygon: list[list[float]], width: int, height: int) -> float:
    """Distance from a pixel-space point to the nearest polygon edge."""
    best = float("inf")
    for index, start in enumerate(polygon):
        end = polygon[(index + 1) % len(polygon)]
        ax, ay = start[0] * width, start[1] * height
        bx, by = end[0] * width, end[1] * height
        dx, dy = bx - ax, by - ay
        denom = dx * dx + dy * dy
        t = 0.0 if denom == 0 else max(0.0, min(1.0, ((x - ax) * dx + (y - ay) * dy) / denom))
        best = min(best, hypot(x - (ax + t * dx), y - (ay + t * dy)))
    return best


@dataclass
class WrongWayState:
    points: deque[dict[str, Any]]
    violating_windows: int = 0
    emitted: bool = False


@dataclass
class WrongWayRule:
    frame_width: int
    frame_height: int
    max_gap_s: float = 1.0
    states: dict[tuple[int, str], WrongWayState] = field(default_factory=dict)

    def update(self, detection: dict[str, Any], zone: dict[str, Any]) -> dict[str, Any] | None:
        key = (detection["track_id"], zone["zone_id"])
        state = self.states.get(key)
        if state is None:
            state = WrongWayState(deque(maxlen=zone["min_observations"]))
            self.states[key] = state

        x1, y1, x2, y2 = detection["bbox"]
        x = (x1 + x2) / 2.0 / self.frame_width
        y = (y1 + y2) / 2.0 / self.frame_height
        boundary_margin = float(zone.get("boundary_margin_px", 0.0))
        near_boundary = boundary_margin > 0 and boundary_distance_px(
            x * self.frame_width, y * self.frame_height, zone["polygon"],
            self.frame_width, self.frame_height,
        ) < boundary_margin
        if not point_in_polygon(x, y, zone["polygon"]) or near_boundary:
            state.points.clear()
            state.violating_windows = 0
            state.emitted = False
            return None

        if state.points and detection["ts"] - state.points[-1]["ts"] > self.max_gap_s:
            state.points.clear()
            state.violating_windows = 0
            state.emitted = False

        state.points.append(detection)
        if len(state.points) < zone["min_observations"]:
            return None

        first, last = state.points[0], state.points[-1]
        elapsed = last["ts"] - first["ts"]
        if elapsed < zone["min_elapsed_s"]:
            return None

        first_x = (first["bbox"][0] + first["bbox"][2]) / 2.0
        first_y = (first["bbox"][1] + first["bbox"][3]) / 2.0
        last_x = (last["bbox"][0] + last["bbox"][2]) / 2.0
        last_y = (last["bbox"][1] + last["bbox"][3]) / 2.0
        dx, dy = last_x - first_x, last_y - first_y
        displacement_ratio = hypot(dx, dy) / hypot(self.frame_width, self.frame_height)
        if displacement_ratio < zone["min_displacement_ratio"]:
            state.violating_windows = 0
            return None

        heading = degrees(atan2(dy, dx)) % 360.0
        is_wrong = angular_difference_deg(heading, zone["allowed_dir_deg"]) > zone["tolerance_deg"]
        state.violating_windows = state.violating_windows + 1 if is_wrong else 0
        if not is_wrong:
            state.emitted = False
        if not is_wrong or state.emitted or state.violating_windows < zone["required_wrong_windows"]:
            return None

        state.emitted = True
        confidence = sum(point["conf"] for point in state.points) / len(state.points)
        return {
            "type": "wrong_way",
            "track_id": detection["track_id"],
            "class": detection["class"],
            "conf": confidence,
            "zone_id": zone["zone_id"],
            "frame": detection["frame"],
            "video_ts_s": detection["ts"],
            "bbox": list(detection["bbox"]),
        }
