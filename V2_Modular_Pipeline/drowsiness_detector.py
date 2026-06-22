# ═══════════════════════════════════════════
#  AEROFLEET V2 — DROWSINESS DETECTOR
#  MediaPipe Tasks API → Face Landmarks → EAR / MAR / Head Pose
# ═══════════════════════════════════════════
import cv2
import numpy as np
import os

try:
    import mediapipe as mp
    from mediapipe.tasks.python import BaseOptions
    from mediapipe.tasks.python.vision import (
        FaceLandmarker,
        FaceLandmarkerOptions,
        RunningMode,
    )
    MP_AVAILABLE = True
except ImportError:
    MP_AVAILABLE = False
    print("[DrowsinessDetector] WARNING: mediapipe not installed properly")


class DrowsinessDetector:
    """
    Uses MediaPipe Face Landmarker (Tasks API, v0.10.35+) to detect:
      1. Eye closure (EAR — Eye Aspect Ratio)
      2. Yawning (MAR — Mouth Aspect Ratio)
      3. Head nodding (pitch angle estimation)
    """

    # MediaPipe Face Mesh landmark indices (478 landmarks)
    LEFT_EYE = [33, 160, 158, 133, 153, 144]
    RIGHT_EYE = [362, 385, 387, 263, 373, 380]
    MOUTH = [78, 82, 13, 308, 87, 14]

    def __init__(self, ear_threshold=0.21, ear_frames=15,
                 head_pitch_threshold=25, pitch_frames=15,
                 model_path=None):
        if not MP_AVAILABLE:
            raise RuntimeError("mediapipe is required but not installed")

        self.ear_threshold = ear_threshold
        self.ear_frames = ear_frames
        self.head_pitch_threshold = head_pitch_threshold
        self.pitch_frames = pitch_frames

        # Counters
        self._drowsy_counter = 0
        self._pitch_counter = 0
        self._last_ear = 0.0
        self._face_detected = False

        # Resolve model path
        if model_path is None:
            model_path = os.path.join(os.path.dirname(__file__), "face_landmarker.task")

        if not os.path.exists(model_path):
            print(f"[DrowsinessDetector] Model not found at {model_path}, downloading...")
            import urllib.request
            url = "https://storage.googleapis.com/mediapipe-models/face_landmarker/face_landmarker/float16/latest/face_landmarker.task"
            urllib.request.urlretrieve(url, model_path)
            print("[DrowsinessDetector] Model downloaded.")

        # Initialize FaceLandmarker with IMAGE mode (synchronous)
        options = FaceLandmarkerOptions(
            base_options=BaseOptions(model_asset_path=model_path),
            running_mode=RunningMode.IMAGE,
            num_faces=1,
            min_face_detection_confidence=0.5,
            min_face_presence_confidence=0.5,
            min_tracking_confidence=0.5,
            output_face_blendshapes=False,
            output_facial_transformation_matrixes=False,
        )
        self.landmarker = FaceLandmarker.create_from_options(options)
        print("[DrowsinessDetector] Initialized with MediaPipe Tasks API")

    @staticmethod
    def _dist(p1, p2):
        return np.linalg.norm(np.array(p1) - np.array(p2))

    def _compute_ear(self, landmarks):
        """Eye Aspect Ratio = (|p2-p6| + |p3-p5|) / (2 * |p1-p4|)"""
        def ear_one_eye(indices):
            pts = [landmarks[i] for i in indices]
            v1 = self._dist(pts[1], pts[5])
            v2 = self._dist(pts[2], pts[4])
            h = self._dist(pts[0], pts[3])
            if h == 0:
                return 0.3
            return (v1 + v2) / (2.0 * h)

        left = ear_one_eye(self.LEFT_EYE)
        right = ear_one_eye(self.RIGHT_EYE)
        return (left + right) / 2.0



    def _estimate_head_pitch(self, landmarks, frame_shape):
        """Rough pitch estimation using nose tip (1) and chin (152)."""
        nose = landmarks[1]
        chin = landmarks[152]
        forehead = landmarks[10]

        nose_chin_dist = chin[1] - nose[1]
        forehead_nose_dist = nose[1] - forehead[1]

        if forehead_nose_dist == 0:
            return 0

        ratio = nose_chin_dist / forehead_nose_dist
        pitch_approx = (ratio - 1.1) * 40
        return pitch_approx

    def process_frame(self, frame):
        """
        Process a single BGR frame.
        Returns: dict with detection results and mesh_frame.
        """
        output = {
            "event": None,
            "ear": 0.0,
            "face_detected": False,
            "drowsy_counter": self._drowsy_counter,
            "pitch_counter": self._pitch_counter,
            "face_bbox": None,
            "mesh_frame": frame.copy()
        }

        # Convert BGR to RGB for MediaPipe
        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        mp_image = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb)

        # Detect face landmarks
        result = self.landmarker.detect(mp_image)

        if not result.face_landmarks or len(result.face_landmarks) == 0:
            self._face_detected = False
            return output

        face = result.face_landmarks[0]
        h, w, _ = frame.shape

        # Convert normalized landmarks to pixel coordinates
        landmarks = [(lm.x * w, lm.y * h) for lm in face]

        self._face_detected = True
        output["face_detected"] = True

        # ── Compute face bounding box ──
        xs = [p[0] for p in landmarks]
        ys = [p[1] for p in landmarks]
        x_min, x_max = max(0, min(xs)), min(w, max(xs))
        y_min, y_max = max(0, min(ys)), min(h, max(ys))
        pad = 20
        x_min = max(0, x_min - pad)
        y_min = max(0, y_min - pad)
        x_max = min(w, x_max + pad)
        y_max = min(h, y_max + pad)
        output["face_bbox"] = (int(x_min), int(y_min), int(x_max - x_min), int(y_max - y_min))

        # ── EAR ──
        ear = self._compute_ear(landmarks)
        self._last_ear = ear
        output["ear"] = round(ear, 3)

        # ── Check drowsiness ──
        if ear < self.ear_threshold:
            self._drowsy_counter += 1
            if self._drowsy_counter >= self.ear_frames:
                output["event"] = "DROWSINESS"
        else:
            self._drowsy_counter = 0

        # ── Check head pitch (Distraction) ──
        if output["event"] is None:
            pitch = self._estimate_head_pitch(landmarks, frame.shape)
            if abs(pitch) > self.head_pitch_threshold:
                self._pitch_counter += 1
                if self._pitch_counter >= self.pitch_frames:
                    output["event"] = "DISTRACTION"
            else:
                self._pitch_counter = 0
        else:
            self._pitch_counter = 0

        output["drowsy_counter"] = self._drowsy_counter
        output["pitch_counter"] = self._pitch_counter

        # ── Draw Face Mesh with OpenCV (no legacy mediapipe.solutions needed) ──
        mesh = output["mesh_frame"]

        # Define key contour groups using MediaPipe Face Mesh indices
        FACE_OVAL = [10, 338, 297, 332, 284, 251, 389, 356, 454, 323, 361, 288,
                     397, 365, 379, 378, 400, 377, 152, 148, 176, 149, 150, 136,
                     172, 58, 132, 93, 234, 127, 162, 21, 54, 103, 67, 109, 10]
        LEFT_EYE_CONTOUR = [33, 7, 163, 144, 145, 153, 154, 155, 133, 173, 157, 158, 159, 160, 161, 246, 33]
        RIGHT_EYE_CONTOUR = [362, 382, 381, 380, 374, 373, 390, 249, 263, 466, 388, 387, 386, 385, 384, 398, 362]
        LIPS_OUTER = [61, 146, 91, 181, 84, 17, 314, 405, 321, 375, 291, 409, 270, 269, 267, 0, 37, 39, 40, 185, 61]
        LEFT_EYEBROW = [70, 63, 105, 66, 107, 55, 65, 52, 53, 46]
        RIGHT_EYEBROW = [300, 293, 334, 296, 336, 285, 295, 282, 283, 276]

        contours = [
            (FACE_OVAL, (100, 100, 100)),       # Gray face outline
            (LEFT_EYE_CONTOUR, (0, 255, 128)),   # Green-cyan left eye
            (RIGHT_EYE_CONTOUR, (0, 255, 128)),  # Green-cyan right eye
            (LIPS_OUTER, (0, 128, 255)),          # Orange lips
            (LEFT_EYEBROW, (200, 200, 200)),      # Light gray eyebrow
            (RIGHT_EYEBROW, (200, 200, 200)),     # Light gray eyebrow
        ]

        for indices, color in contours:
            pts = []
            for idx in indices:
                if idx < len(landmarks):
                    pts.append((int(landmarks[idx][0]), int(landmarks[idx][1])))
            if len(pts) > 1:
                cv2.polylines(mesh, [np.array(pts)], isClosed=False, color=color, thickness=1, lineType=cv2.LINE_AA)

        # Draw small dots on key landmarks (eyes, nose tip, chin)
        key_points = [1, 4, 5, 6, 33, 133, 362, 263, 61, 291, 152, 10]
        for idx in key_points:
            if idx < len(landmarks):
                pt = (int(landmarks[idx][0]), int(landmarks[idx][1]))
                cv2.circle(mesh, pt, 2, (255, 255, 255), -1, cv2.LINE_AA)

        return output

    def reset_counters(self):
        self._drowsy_counter = 0
        self._pitch_counter = 0
