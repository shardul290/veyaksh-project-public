# Task 2.7 — Evaluation and tracking record

Status: evaluation and tracking outputs were generated in Colab; Git merge request remains to be opened.

## Dataset and validation

- Training dataset: Task 2.6 merged DATS_2022 public data and the reviewed SHARDUL internal batch.
- DATS_2022 source split used for this integration: 1,677 train images from the prepared dataset. Its source is CC BY 4.0; cite Paranjape (2022), DOI 10.17632/nfc34n8svj.2.
- SHARDUL source: 463 reviewed frames were used for the Task 2.6 snapshot. Per-clip provenance/rights were recorded as unverified in the source register; confirm authorization before external distribution.
- Combined dataset: 2,140 images: 2,079 train and 61 validation. Source/split counts: DATS_2022 train 1,677; SHARDUL train 402; SHARDUL validation 61.
- Held-out validation clip: `muzaffarnagar_clip03`, all 61 annotated frames held out together using `metadata.csv` provenance.
- Evaluation: 61 images and 12,152 labeled instances. Aggregate precision 0.3962022125, recall 0.1992563237, mAP@50 0.1826558967, mAP@50–95 0.1256129618. Report these measured values as-is.

### Per-class validation metrics

| Class         | Instances | Precision | Recall    | AP@50     | AP@50–95  |
|---------------|----------:|----------:|----------:|----------:|----------:|
| `two_wheeler` | 3,065     | 0.314     | 0.228     | 0.218     | 0.133     |
| `auto`        | 411       | 0.358     | 0.353     | 0.260     | 0.184     |
| `e_rickshaw`  | 241       | 0.685     | 0.332     | 0.315     | 0.234     |
| `car`         | 2,346     | 0.669     | 0.384     | 0.389     | 0.265     |
| `bus_truck`   | 2,226     | 0.494     | 0.093     | 0.0916    | 0.0586    |
| `cart`        | 610       | 0.000     | 0.000     | 0.000     | 0.000     |
| `pedestrian`  | 3,253     | 0.253     | 0.00461   | 0.005     | 0.0045    |

The confusion-matrix plots and normalized plot were saved under `VEYAKSH_TRAFFIC_DATASET/models/task2_7_eval/heldout_clip_eval/` in Drive.

## Class mapping

Class IDs are fixed across the merged dataset and outputs:

| ID | Class |
|---:|---|
| 0 | `two_wheeler` |
| 1 | `auto` |
| 2 | `e_rickshaw` |
| 3 | `car` |
| 4 | `bus_truck` |
| 5 | `cart` |
| 6 | `pedestrian` |

Source aliases used in preparation include Bike/Motorcycle → `two_wheeler`; Rickshaw/Auto-rickshaw → `auto`; Bus/Truck/Tempo → `bus_truck`; Person → `pedestrian`. E-rickshaw remains distinct from auto when explicitly labeled.

## Training configuration

- Framework: Ultralytics YOLO.
- Model: YOLO11s (`yolo11s.pt` initialization).
- Training: 50 epochs maximum, early stopping patience 10, image size 640, batch 16 on GPU, two workers.
- Validation: the single complete held-out SHARDUL clip chosen using `metadata.csv`.
- Checkpoint: `best.pt` was saved by the Task 2.6 run and the user confirmed it was copied to Drive under `VEYAKSH_TRAFFIC_DATASET/models/`.

## Current Task 2.7 deliverables

- Aggregate and per-class metrics are in `VEYAKSH_TRAFFIC_DATASET/models/task2_7_eval/task2_7_metrics.csv`; the confusion-matrix plots are in the evaluation run directory above.
- Four ByteTrack JSONL exports are required: `data/test.mp4` and one campus clip, each using `yolo11n.pt` and the trained `best.pt`.
- `yolo11n.pt` is COCO-pretrained. Its available categories are mapped conservatively: bicycle/motorcycle → `two_wheeler`, car → `car`, bus/truck → `bus_truck`, person → `pedestrian`. It cannot emit reliable `auto`, `e_rickshaw`, or `cart` detections. The custom `best.pt` uses the seven VEYAKSH classes.
- The user confirmed both required videos were placed in the expected Drive folder and all four JSONL exports are present in Drive. The trained `best.pt` is present in `VEYAKSH_TRAFFIC_DATASET/models/`.
- The current Git remote is `https://gitlab.com/veyaksh-group1/survillance-edge.git`; the task text names `anant/...`. Confirm the intended repository/permissions before pushing or opening the MR.

## Known limitations and next improvements

- The observed aggregate scores are modest; the model should be used as a baseline/proposal source, not represented as production-ready. `cart` AP is zero and pedestrian recall/AP are very low on this held-out clip; `bus_truck` recall is also low.
- DATS_2022 has no e-rickshaw boxes in the prepared source counts and only 13 cart boxes, so those classes have weak or absent public supervision. SHARDUL annotations add internal examples, but their per-clip rights need confirmation.
- Review the confusion matrix and representative tracks for identity switches, missed/duplicate boxes, and class confusion (especially `auto` versus `e_rickshaw`) before downstream use.
- Recommended improvements: add diverse labeled clips, increase and balance `cart` and pedestrian examples, review missed small/far objects, improve annotation completeness, and retrain/evaluate on multiple held-out clips when data permits.
- Remaining delivery step: commit only the intended Task 2.7 artifacts on the `anant/...` working branch and open an MR targeting protected `development`.
