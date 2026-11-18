"""SQLite schema and initializer for the M6 nodes and events database."""

from __future__ import annotations

import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent / "veyaksh.db"

SCHEMA = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS nodes (
    node_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    lat REAL NOT NULL,
    lon REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    node_id TEXT NOT NULL,
    cam_id TEXT NOT NULL,
    ts TEXT NOT NULL,
    type TEXT NOT NULL,
    track_id TEXT,
    class TEXT NOT NULL,
    conf REAL,
    zone_id TEXT,
    plate_text TEXT,
    plate_conf REAL,
    plate_img TEXT,
    clip TEXT NOT NULL,
    snapshot TEXT,
    received_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (node_id) REFERENCES nodes(node_id)
);
"""

CAMERA_NODES = (
    ("N01", "Point 1", 29 + 51/60 + 50.2/3600, 77 + 53/60 + 43.5/3600),
    ("N02", "Point 2", 29 + 51/60 + 53.8/3600, 77 + 54/60 + 3/3600),
)


def initialize_database(db_path: Path | str = DB_PATH) -> Path:
    """Create the schema and seed placeholder nodes without replacing existing data."""
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)

    with sqlite3.connect(path) as connection:
        connection.executescript(SCHEMA)
        event_columns = {
            row[1] for row in connection.execute("PRAGMA table_info(events)").fetchall()
        }
        if "snapshot" not in event_columns:
            connection.execute("ALTER TABLE events ADD COLUMN snapshot TEXT")
        if "archived" not in event_columns:
            connection.execute("ALTER TABLE events ADD COLUMN archived INTEGER NOT NULL DEFAULT 0")
            # Footage from before the surveyed locations was declared invalid.
            # Retain its records for audit without attaching it to the new pins.
            connection.execute("UPDATE events SET archived = 1")
        for name, definition in {
            "status": "TEXT NOT NULL DEFAULT 'pending'",
            "status_at": "TEXT",
            "video_ts_s": "REAL",
        }.items():
            if name not in event_columns:
                connection.execute(f"ALTER TABLE events ADD COLUMN {name} {definition}")
        connection.executescript("""
            CREATE TABLE IF NOT EXISTS event_actions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                event_id INTEGER NOT NULL REFERENCES events(id),
                status TEXT NOT NULL,
                ts TEXT NOT NULL
            );
        """)
        connection.executemany(
            """INSERT INTO nodes (node_id, name, lat, lon) VALUES (?, ?, ?, ?)
               ON CONFLICT(node_id) DO UPDATE SET name = excluded.name,
               lat = excluded.lat, lon = excluded.lon""",
            CAMERA_NODES,
        )

    return path


if __name__ == "__main__":
    print(f"Initialized SQLite database: {initialize_database()}")
