# ═══════════════════════════════════════════
#  AEROFLEET V2 — CONFIGURATION
# ═══════════════════════════════════════════
import os

# ─── Camera & Frame Sampling ───
CAMERA_INDEX = 0
FPS_LIMIT = 15
CAMERA_WIDTH = 640
CAMERA_HEIGHT = 480
BUFFER_SECONDS = 60
BUFFER_SIZE = FPS_LIMIT * BUFFER_SECONDS  # 900 frames

# ─── Drowsiness Detection Thresholds ───
EAR_THRESHOLD = 0.21          # Eye Aspect Ratio below this = eyes closed
EAR_CONSECUTIVE_FRAMES = 45   # 3 seconds at 15 FPS
MAR_THRESHOLD = 0.6           # Mouth Aspect Ratio above this = yawning
MAR_CONSECUTIVE_FRAMES = 45   # 3 seconds at 15 FPS
HEAD_PITCH_THRESHOLD = 25     # degrees — head nod down

# ─── Phone Detection ───
PHONE_CONFIDENCE_THRESHOLD = 0.45
PHONE_CONSECUTIVE_FRAMES = 30  # 2 seconds at 15 FPS

# ─── Driver Certification / Face Recognition ───
FACE_RECOGNITION_TOLERANCE = 80.0
KNOWN_DRIVERS_DIR = os.path.join(os.path.dirname(__file__), "known_drivers")

# ─── Video Clip Settings ───
FULL_CLIP_SECONDS = 60
SHORT_CLIP_SECONDS = 30
ALERT_COOLDOWN_SECONDS = 15

# ─── Local Offline Storage & GC ───
LOCAL_STORAGE_DIR = os.path.join(os.path.dirname(__file__), "local_storage")
PENDING_UPLOADS_DIR = os.path.join(LOCAL_STORAGE_DIR, "pending_uploads")
MAX_STORAGE_MB = 500         # Max size of local_storage directory before GC kicks in
MAX_STORAGE_DAYS = 7         # Delete files older than this
GC_INTERVAL_SECONDS = 300    # Run GC every 5 minutes

# ─── Cloud (Firebase Storage — free tier compatible) ───
FIREBASE_CREDENTIALS_PATH = os.getenv("FIREBASE_CREDENTIALS", "")
FIREBASE_STORAGE_BUCKET = os.getenv("FIREBASE_BUCKET", "aerofleet-v2.appspot.com")

# ─── MQTT ───
MQTT_BROKER = os.getenv("MQTT_HOST", "localhost")
MQTT_PORT = int(os.getenv("MQTT_PORT", "1883"))
MQTT_TOPIC_ALERTS = "aerofleet/alerts"
MQTT_TOPIC_TELEMETRY = "aerofleet/telemetry"

# ─── Backend API ───
API_HOST = "0.0.0.0"
API_PORT = 8000

# ─── Truck Identity ───
TRUCK_ID = os.getenv("TRUCK_ID", "TRK-001")

# Ensure directories exist
os.makedirs(KNOWN_DRIVERS_DIR, exist_ok=True)
os.makedirs(LOCAL_STORAGE_DIR, exist_ok=True)
os.makedirs(PENDING_UPLOADS_DIR, exist_ok=True)
