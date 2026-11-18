"""Send the project's sample event JSON files to the local FastAPI service.

By default the script cycles through every sample event and sends one event
every three seconds until interrupted with Ctrl+C. Use --once to send each
sample once and exit.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any

import requests

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SAMPLE_DIR = PROJECT_ROOT / "m6_backend" / "sample_events"


def load_samples(sample_dir: Path) -> list[tuple[Path, dict[str, Any]]]:
    files = sorted(sample_dir.glob("*.json"))
    if not files:
        raise FileNotFoundError(f"No sample event JSON files found in {sample_dir}")

    samples: list[tuple[Path, dict[str, Any]]] = []
    for path in files:
        with path.open(encoding="utf-8") as sample_file:
            payload = json.load(sample_file)
        if not isinstance(payload, dict):
            raise ValueError(f"Sample event must contain a JSON object: {path}")
        samples.append((path, payload))
    return samples


def send_and_confirm(api_url: str, path: Path, payload: dict[str, Any]) -> int:
    response = requests.post(f"{api_url}/events", json=payload, timeout=15)
    response.raise_for_status()
    result = response.json()
    event_id = result.get("id")
    if not isinstance(event_id, int):
        raise RuntimeError(f"API did not return an integer event id for {path.name}: {result}")

    # The API uses id > since_id, so ask for records starting just before this id.
    response = requests.get(
        f"{api_url}/events",
        params={"since_id": max(0, event_id - 1), "limit": 500},
        timeout=15,
    )
    response.raise_for_status()
    events = response.json()
    if not isinstance(events, list) or not any(row.get("id") == event_id for row in events):
        raise RuntimeError(f"Event {event_id} from {path.name} was not returned by GET /events")
    return event_id


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--api-url", default="http://127.0.0.1:8000",
                        help="FastAPI base URL (default: http://127.0.0.1:8000)")
    parser.add_argument("--sample-dir", type=Path, default=DEFAULT_SAMPLE_DIR,
                        help="Directory containing sample event JSON files")
    parser.add_argument("--interval", type=float, default=3.0,
                        help="Seconds between event sends (default: 3)")
    parser.add_argument("--once", action="store_true",
                        help="Send every sample once, then exit instead of cycling forever")
    args = parser.parse_args()
    if args.interval <= 0:
        parser.error("--interval must be greater than zero")

    api_url = args.api_url.rstrip("/")
    try:
        samples = load_samples(args.sample_dir)
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        print(f"Could not load sample events: {exc}", file=sys.stderr)
        return 1

    print(f"Loaded {len(samples)} samples from {args.sample_dir}")
    print(f"Sending to {api_url}/events every {args.interval:g} seconds; Ctrl+C stops the simulator.")
    sent = 0
    next_send = time.monotonic()
    try:
        while True:
            for path, payload in samples:
                delay = next_send - time.monotonic()
                if delay > 0:
                    time.sleep(delay)
                event_id = send_and_confirm(api_url, path, payload)
                sent += 1
                print(
                    f"Sent and read back {path.name}: id={event_id}, "
                    f"type={payload.get('type')}, node_id={payload.get('node_id')} "
                    f"(total sent: {sent})",
                    flush=True,
                )
                next_send += args.interval
                if args.once and sent == len(samples):
                    print(f"Completed one pass: {sent} sample events were sent and read back.")
                    return 0
    except KeyboardInterrupt:
        print(f"\nStopped. {sent} events were sent and confirmed.")
        return 0
    except (requests.RequestException, ValueError, RuntimeError) as exc:
        print(f"Event simulator stopped after {sent} events: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
