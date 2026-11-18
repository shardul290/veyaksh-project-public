"""Run OpenCV Zoo NanoDet on every frame and save an annotated video/report."""

from __future__ import annotations

import argparse
import html
import json
import sys
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "models"))
from nanodet import NanoDet  # noqa: E402

CLASSES = (
    "person", "bicycle", "car", "motorcycle", "airplane", "bus", "train", "truck", "boat",
    "traffic light", "fire hydrant", "stop sign", "parking meter", "bench", "bird", "cat",
    "dog", "horse", "sheep", "cow", "elephant", "bear", "zebra", "giraffe", "backpack",
    "umbrella", "handbag", "tie", "suitcase", "frisbee", "skis", "snowboard", "sports ball",
    "kite", "baseball bat", "baseball glove", "skateboard", "surfboard", "tennis racket",
    "bottle", "wine glass", "cup", "fork", "knife", "spoon", "bowl", "banana", "apple",
    "sandwich", "orange", "broccoli", "carrot", "hot dog", "pizza", "donut", "cake", "chair",
    "couch", "potted plant", "bed", "dining table", "toilet", "tv", "laptop", "mouse", "remote",
    "keyboard", "cell phone", "microwave", "oven", "toaster", "sink", "refrigerator", "book",
    "clock", "vase", "scissors", "teddy bear", "hair drier", "toothbrush",
)
VEHICLE_IDS = {1, 2, 3, 5, 6, 7, 8}  # bicycle, car, motorcycle, bus, train, truck, boat
VEHICLE_NAMES = {i: CLASSES[i] for i in VEHICLE_IDS}
COLORS = {"car": (0, 220, 255), "motorcycle": (255, 180, 0), "bus": (255, 80, 180),
          "truck": (100, 255, 100), "bicycle": (200, 100, 255), "train": (255, 100, 100),
          "boat": (255, 255, 100)}


def letterbox(image: np.ndarray, size: int = 416):
    h, w = image.shape[:2]
    scale = min(size / w, size / h)
    nw, nh = round(w * scale), round(h * scale)
    resized = cv2.resize(image, (nw, nh), interpolation=cv2.INTER_AREA)
    left, top = (size - nw) // 2, (size - nh) // 2
    padded = np.zeros((size, size, 3), dtype=np.uint8)
    padded[top:top + nh, left:left + nw] = resized
    return cv2.cvtColor(padded, cv2.COLOR_BGR2RGB), (scale, left, top)


def render_frame(frame, detections, transform, frame_no, total, processing_fps, out_size):
    scale, left, top = transform
    h, w = frame.shape[:2]
    canvas = cv2.resize(frame, out_size, interpolation=cv2.INTER_AREA)
    sx, sy = out_size[0] / w, out_size[1] / h
    vehicle_count = 0
    for det in detections:
        x1, y1, x2, y2, confidence, class_id = det
        class_id = int(class_id)
        if class_id not in VEHICLE_IDS:
            continue
        vehicle_count += 1
        x1, x2 = (x1 - left) / scale, (x2 - left) / scale
        y1, y2 = (y1 - top) / scale, (y2 - top) / scale
        p1 = (int(max(0, min(out_size[0] - 1, x1 * sx))), int(max(0, min(out_size[1] - 1, y1 * sy))))
        p2 = (int(max(0, min(out_size[0] - 1, x2 * sx))), int(max(0, min(out_size[1] - 1, y2 * sy))))
        name = CLASSES[class_id]
        color = COLORS.get(name, (0, 255, 0))
        cv2.rectangle(canvas, p1, p2, color, 2)
        cv2.putText(canvas, f"{name} {float(confidence):.2f}", (p1[0], max(22, p1[1] - 7)),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.65, color, 2, cv2.LINE_AA)
    overlay = canvas.copy()
    cv2.rectangle(overlay, (0, 0), (canvas.shape[1], 46), (15, 22, 32), -1)
    cv2.addWeighted(overlay, 0.83, canvas, 0.17, 0, canvas)
    cv2.putText(canvas, f"VIDEO ANALYSIS   FRAME {frame_no}/{total}   VEHICLES {vehicle_count}   {processing_fps:.1f} FPS",
                (15, 31), cv2.FONT_HERSHEY_SIMPLEX, 0.62, (245, 248, 255), 2, cv2.LINE_AA)
    return canvas, vehicle_count


def write_html(path: Path, data: dict):
    counts = data["vehicle_detections_by_class"]
    maximum = max(counts.values(), default=1)
    bars = "\n".join(
        f'<div class="bar-row"><span>{html.escape(name.title())}</span>'
        f'<div class="track"><i style="width:{count / maximum * 100:.1f}%"></i></div><b>{count}</b></div>'
        for name, count in sorted(counts.items(), key=lambda item: (-item[1], item[0]))
    ) or '<p>No vehicle detections were found.</p>'
    doc = f'''<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width">
<title>Traffic video analysis</title><style>
body{{margin:0;background:#101722;color:#eaf0f8;font:16px/1.5 Segoe UI,Arial,sans-serif}}main{{max-width:1000px;margin:36px auto;padding:0 22px}}
h1{{font-size:32px;margin-bottom:4px}}.muted{{color:#aab8ca}}.grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:12px;margin:24px 0}}
.card,.panel{{background:#192536;border:1px solid #2a3a4f;border-radius:14px;padding:18px}}.value{{font-size:28px;font-weight:700;color:#7de0c3}}
.bar-row{{display:grid;grid-template-columns:130px 1fr 48px;gap:12px;align-items:center;margin:12px 0}}.track{{height:12px;background:#26364a;border-radius:8px;overflow:hidden}}
.track i{{display:block;height:100%;background:linear-gradient(90deg,#48c6a9,#69a9ff);border-radius:8px}}video{{width:min(100%,540px);max-height:75vh;background:#000;border-radius:12px}}
a{{color:#83c4ff}}.note{{border-left:4px solid #f3bd5b;padding:10px 14px;background:#262a2b}}
</style><main><h1>Traffic video analysis</h1><div class="muted">Frame-by-frame vehicle object detection · {html.escape(data['source_video'])}</div>
<div class="grid"><div class="card"><div class="muted">Frames processed</div><div class="value">{data['frames_processed']}</div></div>
<div class="card"><div class="muted">Video duration</div><div class="value">{data['video_duration_seconds']:.1f}s</div></div>
<div class="card"><div class="muted">Vehicle boxes</div><div class="value">{data['total_vehicle_detections']}</div></div>
<div class="card"><div class="muted">Processing speed</div><div class="value">{data['processing_fps']:.2f} fps</div></div></div>
<section class="panel"><h2>Annotated video</h2><video controls preload="metadata" src="../{html.escape(data['annotated_video_relative'])}"></video>
<p><a href="../{html.escape(data['annotated_video_relative'])}">Open/download the annotated MP4</a></p></section>
<section class="panel" style="margin-top:16px"><h2>Vehicle detection counts</h2>{bars}</section>
<p class="note"><b>Interpretation:</b> counts represent detected vehicle boxes across frames, so a vehicle appearing in many frames is counted repeatedly. These are object detections only; this report does not infer direction, violations, or wrong-way events.</p>
<p class="muted">Model: OpenCV Zoo NanoDet · Generated {html.escape(data['generated_at'])}</p></main></html>'''
    path.write_text(doc, encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("video", nargs="?", default="assets/traffic_demo.mp4")
    parser.add_argument("--output", default="assets/traffic_demo_analysis.mp4")
    parser.add_argument("--model", default="models/object_detection_nanodet_2022nov.onnx")
    parser.add_argument("--confidence", type=float, default=0.35)
    parser.add_argument("--width", type=int, default=960, help="Annotated output width; source aspect ratio is preserved")
    args = parser.parse_args()
    video, output, model_path = (ROOT / args.video).resolve(), (ROOT / args.output).resolve(), (ROOT / args.model).resolve()
    if not video.is_file():
        raise SystemExit(f"Input video not found: {video}")
    if not model_path.is_file():
        raise SystemExit(f"NanoDet model not found: {model_path}")
    cap = cv2.VideoCapture(str(video))
    if not cap.isOpened():
        raise SystemExit(f"OpenCV could not open input video: {video}")
    total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    source_fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    width, height = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)), int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    out_width = min(args.width, width)
    out_height = int(round(height * out_width / width))
    out_width += out_width % 2
    out_height += out_height % 2
    output.parent.mkdir(parents=True, exist_ok=True)
    writer = cv2.VideoWriter(str(output), cv2.VideoWriter_fourcc(*"mp4v"), source_fps, (out_width, out_height))
    if not writer.isOpened():
        cap.release()
        raise SystemExit(f"Could not create output MP4: {output}")
    detector = NanoDet(str(model_path), prob_threshold=args.confidence)
    class_counts: Counter[str] = Counter()
    frame_counts = []
    start = time.perf_counter()
    frame_no = 0
    print(f"Input: {video} ({total} frames, {source_fps:g} FPS, {width}x{height})", flush=True)
    print("Processing every frame with NanoDet on CPU...", flush=True)
    while True:
        ok, frame = cap.read()
        if not ok:
            break
        rgb_input, transform = letterbox(frame)
        detections = detector.infer(rgb_input)
        elapsed = time.perf_counter() - start
        render, frame_vehicle_count = render_frame(
            frame, detections, transform, frame_no + 1, total, (frame_no + 1) / max(elapsed, 1e-9),
            (out_width, out_height),
        )
        writer.write(render)
        for det in detections:
            class_id = int(det[-1])
            if class_id in VEHICLE_IDS:
                class_counts[CLASSES[class_id]] += 1
        frame_counts.append(frame_vehicle_count)
        frame_no += 1
        if frame_no == 1 or frame_no % 10 == 0 or frame_no == total:
            print(f"Processed {frame_no}/{total} frames; this frame: {frame_vehicle_count} vehicle(s); "
                  f"average: {frame_no / max(elapsed, 1e-9):.2f} fps", flush=True)
    cap.release()
    writer.release()
    elapsed = time.perf_counter() - start
    if frame_no == 0:
        raise SystemExit("No frames were decoded; no analysis report was produced.")
    report_dir = ROOT / "reports"
    report_dir.mkdir(exist_ok=True)
    report_json = report_dir / "traffic_analysis.json"
    report_html = report_dir / "traffic_analysis.html"
    data = {
        "source_video": str(video.relative_to(ROOT)),
        "annotated_video_relative": str(output.relative_to(ROOT)).replace("\\", "/"),
        "model": "OpenCV Zoo NanoDet (COCO 80-class model)",
        "confidence_threshold": args.confidence,
        "frames_processed": frame_no,
        "reported_source_frames": total,
        "source_fps": source_fps,
        "source_dimensions": {"width": width, "height": height},
        "output_dimensions": {"width": out_width, "height": out_height},
        "video_duration_seconds": frame_no / source_fps,
        "processing_seconds": elapsed,
        "processing_fps": frame_no / elapsed,
        "total_vehicle_detections": sum(class_counts.values()),
        "vehicle_detections_by_class": dict(class_counts),
        "frames_with_vehicle_detections": sum(n > 0 for n in frame_counts),
        "max_vehicles_in_one_frame": max(frame_counts, default=0),
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "scope_note": "Repeated per-frame object detections, not unique tracked vehicles or traffic violations.",
    }
    report_json.write_text(json.dumps(data, indent=2), encoding="utf-8")
    write_html(report_html, data)
    print(f"\nDone. Annotated video: {output}")
    print(f"Visual report: {report_html}")
    print(f"JSON data: {report_json}")
    print(f"Vehicle detections: {data['total_vehicle_detections']} across {frame_no} frames")


if __name__ == "__main__":
    main()
