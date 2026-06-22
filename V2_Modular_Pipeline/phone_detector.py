# ═══════════════════════════════════════════
#  AEROFLEET V2 — PHONE USAGE DETECTOR
#  YOLOv8n for cell phone detection
# ═══════════════════════════════════════════
import cv2
import numpy as np
import os

try:
    from ultralytics import YOLO
except ImportError:
    YOLO = None
    print("[PhoneDetector] WARNING: ultralytics not installed. Install with: pip install ultralytics")


class PhoneDetector:
    """
    Uses YOLOv8n (nano) to detect cell phone usage while driving.
    COCO class 67 = 'cell phone'.
    Lightweight enough to run at 5 FPS on a Raspberry Pi 4.
    """

    CELL_PHONE_CLASS_ID = 67  # COCO dataset class for 'cell phone'

    def __init__(self, confidence_threshold=0.45, consecutive_frames=10, model_path=None):
        if YOLO is None:
            raise RuntimeError("ultralytics is required. Install: pip install ultralytics")

        self.confidence_threshold = confidence_threshold
        self.consecutive_frames = consecutive_frames
        self._phone_counter = 0
        self._last_detection = None

        # Load model — downloads yolov8n.pt automatically if not present
        if model_path and os.path.exists(model_path):
            self.model = YOLO(model_path)
        else:
            self.model = YOLO("yolov8n.pt")

        # Set model to inference mode with reduced image size for speed
        self.model.overrides["imgsz"] = 320  # Small input for speed
        self.model.overrides["verbose"] = False

        print(f"[PhoneDetector] Loaded YOLOv8n — threshold={confidence_threshold}, "
              f"consecutive={consecutive_frames}")

    def process_frame(self, frame):
        """
        Process a single BGR frame.
        Returns: dict with:
            'event': 'PHONE_USAGE' or None
            'phone_detected': bool
            'phone_confidence': float
            'phone_bbox': (x1,y1,x2,y2) or None
            'phone_counter': int
        """
        output = {
            "event": None,
            "phone_detected": False,
            "phone_confidence": 0.0,
            "phone_bbox": None,
            "phone_counter": self._phone_counter,
        }

        # Run inference
        results = self.model(frame, verbose=False)

        phone_found = False
        best_conf = 0.0
        best_box = None

        for result in results:
            if result.boxes is None:
                continue
            for box in result.boxes:
                cls_id = int(box.cls[0])
                conf = float(box.conf[0])
                if cls_id == self.CELL_PHONE_CLASS_ID and conf >= self.confidence_threshold:
                    phone_found = True
                    if conf > best_conf:
                        best_conf = conf
                        x1, y1, x2, y2 = box.xyxy[0].tolist()
                        best_box = (int(x1), int(y1), int(x2), int(y2))

        if phone_found:
            self._phone_counter += 1
            output["phone_detected"] = True
            output["phone_confidence"] = round(best_conf, 3)
            output["phone_bbox"] = best_box
            self._last_detection = best_box

            if self._phone_counter >= self.consecutive_frames:
                output["event"] = "PHONE_USAGE"
        else:
            self._phone_counter = 0

        output["phone_counter"] = self._phone_counter
        return output

    def reset_counter(self):
        self._phone_counter = 0
