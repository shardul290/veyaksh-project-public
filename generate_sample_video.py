from pathlib import Path
import cv2
import numpy as np

out = Path(__file__).resolve().parent / "assets" / "traffic_demo.mp4"
out.parent.mkdir(exist_ok=True)
width, height, fps, count = 640, 360, 24, 120
writer = cv2.VideoWriter(str(out), cv2.VideoWriter_fourcc(*"mp4v"), fps, (width, height))
if not writer.isOpened():
    raise RuntimeError("OpenCV could not initialize the MP4 writer")
for frame_no in range(count):
    frame = np.zeros((height, width, 3), dtype=np.uint8)
    frame[:] = (95, 145, 90)
    cv2.rectangle(frame, (0, 150), (width, height), (55, 55, 55), -1)
    cv2.line(frame, (320, 160), (320, height), (235, 235, 235), 3)
    for y in range(180, height, 70):
        cv2.line(frame, (318, y), (318, y + 32), (245, 220, 80), 4)
    # Two cars move in opposite directions along the road.
    x1 = (frame_no * 5) % (width + 100) - 50
    x2 = width - ((frame_no * 3) % (width + 100) - 50)
    cv2.rectangle(frame, (x1 - 24, 228), (x1 + 24, 275), (45, 70, 220), -1)
    cv2.rectangle(frame, (x2 - 24, 295), (x2 + 24, 335), (210, 135, 40), -1)
    cv2.putText(frame, "VIDEO INGEST DEMO", (18, 35), cv2.FONT_HERSHEY_SIMPLEX, 0.85, (255,255,255), 2)
    cv2.putText(frame, f"Frame {frame_no + 1:03d} / {count}", (18, 62), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (255,255,255), 1)
    writer.write(frame)
writer.release()
print(f"Created {out} ({count} frames at {fps} fps)")

