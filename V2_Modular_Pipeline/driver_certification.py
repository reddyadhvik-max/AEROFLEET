# ═══════════════════════════════════════════
#  AEROFLEET V2 — DRIVER CERTIFICATION
#  Face embedding matching for multi-driver support
# ═══════════════════════════════════════════
import cv2
import numpy as np
import os
import json
import time
from database import SessionLocal, Driver

class DriverCertification:
    """
    Manages a local database of known driver faces using OpenCV LBPH Face Recognizer.
    This avoids the heavy C++ build tools required by dlib/face_recognition.
    """

    def __init__(self, known_drivers_dir="known_drivers", tolerance=80.0):
        self.known_drivers_dir = known_drivers_dir
        self.tolerance = tolerance # LBPH distance threshold (lower is better, <80 is good)
        self.known_encodings = {}  # {driver_id: {"name": str, "label": int}}
        self.current_driver_id = None
        self.current_driver_name = None
        self._last_verification_time = 0
        
        self.face_cascade = cv2.CascadeClassifier(cv2.data.haarcascades + 'haarcascade_frontalface_default.xml')
        self.recognizer = cv2.face.LBPHFaceRecognizer_create()

        os.makedirs(self.known_drivers_dir, exist_ok=True)
        self._load_known_drivers()

    def _load_known_drivers(self):
        """Load all known driver images and train the recognizer."""
        self.known_encodings = {}
        db = SessionLocal()
        drivers = db.query(Driver).all()
        
        faces = []
        labels = []
        label_counter = 1
        
        for driver in drivers:
            thumb_file = driver.encoding_path
            if os.path.exists(thumb_file):
                img = cv2.imread(thumb_file, cv2.IMREAD_GRAYSCALE)
                if img is not None:
                    faces.append(img)
                    labels.append(label_counter)
                    self.known_encodings[driver.id] = {
                        "name": driver.name,
                        "label": label_counter,
                        "assigned_truck": driver.assigned_truck
                    }
                    print(f"[DriverCert] Loaded driver: {driver.name} ({driver.id})")
                    label_counter += 1

        if faces:
            self.recognizer.train(faces, np.array(labels))
            print(f"[DriverCert] Trained recognizer with {len(faces)} faces.")
        else:
            print("[DriverCert] No valid face images found to train.")
            
        db.close()
        print(f"[DriverCert] {len(self.known_encodings)} drivers loaded from database")

    def enroll_driver(self, driver_id, name, face_image_bgr, assigned_truck=None):
        """
        Enroll a new driver from a BGR face image.
        Detects face, saves crop, and retrains recognizer.
        """
        gray = cv2.cvtColor(face_image_bgr, cv2.COLOR_BGR2GRAY)
        faces = self.face_cascade.detectMultiScale(gray, 1.1, 4)
        
        if len(faces) == 0:
            return "No face detected in the image"
            
        # Get largest face
        faces = sorted(faces, key=lambda x: x[2]*x[3], reverse=True)
        x, y, w, h = faces[0]
        face_crop = gray[y:y+h, x:x+w]
        
        # Optionally resize to a standard size for LBPH
        face_crop = cv2.resize(face_crop, (200, 200))

        # Save face thumbnail
        thumb_file = os.path.join(self.known_drivers_dir, f"{driver_id}.jpg")
        cv2.imwrite(thumb_file, face_crop)

        # Update database
        db = SessionLocal()
        existing = db.query(Driver).filter(Driver.id == driver_id).first()
        if existing:
            existing.name = name
            existing.assigned_truck = assigned_truck
            existing.encoding_path = thumb_file
        else:
            new_driver = Driver(
                id=driver_id,
                name=name,
                assigned_truck=assigned_truck,
                encoding_path=thumb_file
            )
            db.add(new_driver)
        db.commit()
        db.close()

        # Retrain
        self._load_known_drivers()
        print(f"[DriverCert] Enrolled driver: {name} ({driver_id})")
        return True

    def remove_driver(self, driver_id):
        """Remove a driver from the local database."""
        db = SessionLocal()
        driver = db.query(Driver).filter(Driver.id == driver_id).first()
        if driver:
            if os.path.exists(driver.encoding_path):
                os.remove(driver.encoding_path)
            db.delete(driver)
            db.commit()
        db.close()

        self._load_known_drivers()
        
        if self.current_driver_id == driver_id:
            self.current_driver_id = None
            self.current_driver_name = None
        print(f"[DriverCert] Removed driver: {driver_id}")

    def verify_driver(self, frame_bgr):
        """
        Identify the driver in the given BGR frame.
        Returns: (is_known, driver_id, driver_name, confidence, assigned_truck)
        """
        if not self.known_encodings:
            return False, None, None, 0.0, None

        gray = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2GRAY)
        faces = self.face_cascade.detectMultiScale(gray, 1.1, 4)
        
        if len(faces) == 0:
            return False, None, None, 0.0, None

        faces = sorted(faces, key=lambda x: x[2]*x[3], reverse=True)
        x, y, w, h = faces[0]
        face_crop = gray[y:y+h, x:x+w]
        face_crop = cv2.resize(face_crop, (200, 200))

        try:
            label, distance = self.recognizer.predict(face_crop)
        except cv2.error:
            # Model not trained or other error
            return False, None, None, 0.0, None

        if distance < self.tolerance:
            # Find driver by label
            for d_id, data in self.known_encodings.items():
                if data["label"] == label:
                    self.current_driver_id = d_id
                    self.current_driver_name = data["name"]
                    self._last_verification_time = time.time()
                    
                    # Convert distance to a 0-1 confidence score roughly
                    confidence = max(0, 1.0 - (distance / 150.0))
                    return True, d_id, data["name"], round(confidence, 3), data.get("assigned_truck")

        return False, None, None, 0.0, None

    def get_enrolled_drivers(self):
        """Return list of enrolled driver dicts."""
        db = SessionLocal()
        drivers = db.query(Driver).all()
        result = []
        for d in drivers:
            result.append({
                "id": d.id,
                "name": d.name,
                "assigned_truck": d.assigned_truck
            })
        db.close()
        return result

    def get_driver_thumbnail(self, driver_id):
        """Return path to driver's thumbnail image, or None."""
        thumb_path = os.path.join(self.known_drivers_dir, f"{driver_id}.jpg")
        if os.path.exists(thumb_path):
            return thumb_path
        return None
