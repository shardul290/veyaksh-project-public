"""Evaluate a trained YOLO checkpoint and export aggregate/class-wise AP metrics.

Example (Colab):
  python m2_detect_track/evaluate_task2_7.py \
    --weights /content/drive/MyDrive/VEYAKSH_TRAFFIC_DATASET/models/best.pt \
    --data /content/drive/MyDrive/TrafficDetection/results/task2_6_final_yolo11s_v1/traffic_dataset/data.yaml \
    --output /content/drive/MyDrive/VEYAKSH_TRAFFIC_DATASET/models/task2_7_eval

The Ultralytics validation run also saves confusion_matrix.png and
confusion_matrix_normalized.png in its run directory.
"""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path

from ultralytics import YOLO


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--weights", required=True, type=Path)
    parser.add_argument("--data", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--device", default="0", help="GPU index or cpu")
    parser.add_argument("--imgsz", type=int, default=640)
    parser.add_argument("--batch", type=int, default=16)
    parser.add_argument("--validation-clip", default="", help="Exact source clip ID for the report")
    args = parser.parse_args()

    if not args.weights.is_file():
        parser.error(f"Checkpoint not found: {args.weights}")
    if not args.data.is_file():
        parser.error(f"Dataset YAML not found: {args.data}")

    args.output.mkdir(parents=True, exist_ok=True)
    model = YOLO(str(args.weights))
    result = model.val(
        data=str(args.data), split="val", imgsz=args.imgsz, batch=args.batch,
        device=args.device, plots=True, project=str(args.output),
        name="heldout_clip_eval", exist_ok=True, verbose=True,
    )

    names = model.names
    if isinstance(names, list):
        names = dict(enumerate(names))
    box = result.box
    rows: list[dict[str, object]] = [
        {"metric": "precision", "class_id": "all", "class_name": "all", "value": float(box.mp)},
        {"metric": "recall", "class_id": "all", "class_name": "all", "value": float(box.mr)},
        {"metric": "mAP50", "class_id": "all", "class_name": "all", "value": float(box.map50)},
        {"metric": "mAP50-95", "class_id": "all", "class_name": "all", "value": float(box.map)},
    ]
    class_ids = [int(x) for x in getattr(box, "ap_class_index", [])]
    ap50 = list(getattr(box, "ap50", []))
    ap = list(getattr(box, "ap", []))
    per_class_ap = {class_id: (float(ap50[pos]), float(ap[pos])) for pos, class_id in enumerate(class_ids)}
    for class_id in sorted(int(k) for k in names):
        class_name = str(names[class_id])
        values = per_class_ap.get(class_id)
        rows.append({"metric": "AP50", "class_id": class_id, "class_name": class_name, "value": values[0] if values else ""})
        rows.append({"metric": "AP50-95", "class_id": class_id, "class_name": class_name, "value": values[1] if values else ""})

    metrics_csv = args.output / "task2_7_metrics.csv"
    with metrics_csv.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=["metric", "class_id", "class_name", "value"])
        writer.writeheader()
        writer.writerows(rows)

    matrix = getattr(getattr(result, "confusion_matrix", None), "matrix", None)
    matrix_csv = None
    if matrix is not None:
        matrix_csv = args.output / "task2_7_confusion_matrix.csv"
        import numpy as np
        np.savetxt(matrix_csv, np.asarray(matrix), delimiter=",", fmt="%.6f")

    summary = {
        "weights": str(args.weights),
        "dataset_yaml": str(args.data),
        "validation_split": "val",
        "validation_clip": args.validation_clip or None,
        "precision": float(box.mp),
        "recall": float(box.mr),
        "mAP50": float(box.map50),
        "mAP50-95": float(box.map),
        "metrics_csv": str(metrics_csv),
        "confusion_matrix_csv": str(matrix_csv) if matrix_csv else None,
        "ultralytics_run_dir": str(getattr(result, "save_dir", args.output / "heldout_clip_eval")),
    }
    (args.output / "task2_7_eval_summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
