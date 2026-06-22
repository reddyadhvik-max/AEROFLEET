# ═══════════════════════════════════════════
#  AEROFLEET V2 — MAIN PIPELINE
#  Ties together all modules with API server
# ═══════════════════════════════════════════
import time
import threading
import json
import os
import sys
import base64
import cv2
import numpy as np

from dotenv import load_dotenv
load_dotenv()

from contextlib import asynccontextmanager

from database import init_db, get_db, SessionLocal, Alert, User, Driver, Truck, JourneyRecord
from auth import create_access_token, get_current_user, get_password_hash, verify_password, TokenData, get_current_admin
from simulator import SimulationEngine
from garbage_collector import GarbageCollector
from fastapi import FastAPI, UploadFile, File, Form, HTTPException, Request, Depends
from fastapi.security import OAuth2PasswordRequestForm
from fastapi.staticfiles import StaticFiles
from fastapi.responses import JSONResponse, FileResponse, StreamingResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import uvicorn

import config
from camera_buffer import CameraBuffer
from drowsiness_detector import DrowsinessDetector
from phone_detector import PhoneDetector
from driver_certification import DriverCertification
from event_manager import EventManager
from watchdog import Watchdog


# ═══════════════════════════════════════════
#  LIFESPAN (replaces deprecated on_event)
# ═══════════════════════════════════════════
@asynccontextmanager
async def lifespan(application):
    global camera, drowsiness, phone_det, certifier, event_mgr, watchdog

    print("=" * 50)
    print("  AEROFLEET V2 — MODULAR PIPELINE")
    print("=" * 50)

    # Initialize database
    init_db()
    # Create default admin if not exists
    db = SessionLocal()
    admin = db.query(User).filter(User.username == "admin").first()
    if not admin:
        db.add(User(username="admin", hashed_password=get_password_hash("aerofleet2025"), role="admin"))
    
    # Pre-create driver accounts
    for i in range(1, 4):
        username = f"driver{i}"
        if not db.query(User).filter(User.username == username).first():
            db.add(User(username=username, hashed_password=get_password_hash("pass"), role="driver"))
    db.commit()
    db.close()

    # 1. Camera
    camera = CameraBuffer(
        camera_index=config.CAMERA_INDEX,
        target_fps=config.FPS_LIMIT,
        buffer_seconds=config.BUFFER_SECONDS,
        width=config.CAMERA_WIDTH,
        height=config.CAMERA_HEIGHT,
    )
    camera.start()

    # 2. Drowsiness Detector
    drowsiness = DrowsinessDetector(
        ear_threshold=config.EAR_THRESHOLD,
        ear_frames=config.EAR_CONSECUTIVE_FRAMES,
        head_pitch_threshold=25,
        pitch_frames=15,
    )

    # 3. Phone Detector
    try:
        phone_det = PhoneDetector(
            confidence_threshold=config.PHONE_CONFIDENCE_THRESHOLD,
            consecutive_frames=config.PHONE_CONSECUTIVE_FRAMES,
        )
    except RuntimeError:
        print("[Startup] Phone detector unavailable — running without it")
        from types import SimpleNamespace
        phone_det = SimpleNamespace(
            process_frame=lambda f: {"event": None, "phone_detected": False,
                                     "phone_confidence": 0, "phone_bbox": None, "phone_counter": 0},
            reset_counter=lambda: None,
        )

    # 4. Driver Certification
    certifier = DriverCertification(
        known_drivers_dir=config.KNOWN_DRIVERS_DIR,
        tolerance=config.FACE_RECOGNITION_TOLERANCE,
    )

    # 5. Event Manager
    event_mgr = EventManager(
        fps=config.FPS_LIMIT,
        local_storage_dir=config.LOCAL_STORAGE_DIR,
        pending_uploads_dir=config.PENDING_UPLOADS_DIR,
    )

    # 6. Watchdog
    watchdog = Watchdog(check_interval=5, max_failures=3, log_dir=config.LOCAL_STORAGE_DIR)
    watchdog.register("camera", camera.is_healthy, lambda: camera.start())

    # Register pipeline loop health
    def pipeline_healthy():
        return pipeline_state.get("running", False)
    def restart_pipeline():
        nonlocal pipeline_thread
        pipeline_state["running"] = False
        time.sleep(1)
        pipeline_thread = threading.Thread(target=pipeline_loop, daemon=True)
        pipeline_thread.start()

    # Register MQTT health
    def mqtt_healthy():
        return mqtt_client.is_connected()
    def restart_mqtt():
        try:
            mqtt_client.reconnect()
        except Exception as e:
            print(f"[Watchdog] MQTT reconnect failed: {e}")

    watchdog.register("pipeline", pipeline_healthy, restart_pipeline)
    watchdog.register("mqtt", mqtt_healthy, restart_mqtt)
    watchdog.start()

    # 6b. Inject camera into event manager (avoids circular imports)
    event_mgr.set_camera(camera)

    # 7. Start pipeline thread
    pipeline_thread = threading.Thread(target=pipeline_loop, daemon=True)
    pipeline_thread.start()

    print("[Startup] All systems operational.")
    simulator.start()
    gc.start()

    yield  # App runs here

    # Shutdown
    print("[Shutdown] Stopping pipeline...")
    gc.stop()
    simulator.stop()
    pipeline_state["running"] = False
    if watchdog:
        watchdog.stop()
    if camera:
        camera.stop()


# ═══════════════════════════════════════════
#  GLOBALS
# ═══════════════════════════════════════════
app = FastAPI(title="Aerofleet V2 API", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Pipeline components (initialized on startup)
camera = None
drowsiness = None
phone_det = None
certifier = None
event_mgr = None
watchdog = None

# Pipeline state
pipeline_lock = threading.Lock()
pipeline_state = {
    "running": False,
    "current_driver_id": None,
    "current_driver_name": None,
    "last_event": None,
    "last_ear": 0.0,
    "last_mar": 0.0,
    "face_detected": False,
    "phone_detected": False,
    "alerts_total": 0,
    "frames_processed": 0,
    "pipeline_fps": 0.0,
    "missed_checks": 0,
    "force_verify": False
}

last_mesh_frame = None

# Fleet tracking via MQTT and Simulator
fleet_telemetry = {}
simulator = SimulationEngine(fleet_telemetry)
gc = GarbageCollector()

import paho.mqtt.client as mqtt

def on_mqtt_connect(client, userdata, flags, rc):
    print(f"[MQTT] Connected with result code {rc}")
    client.subscribe("truck/+/telemetry")

def on_mqtt_message(client, userdata, msg):
    try:
        topic_parts = msg.topic.split('/')
        if len(topic_parts) >= 3 and topic_parts[2] == "telemetry":
            truck_id = topic_parts[1]
            data = json.loads(msg.payload.decode())
            fleet_telemetry[truck_id] = data
    except Exception as e:
        print(f"[MQTT] Error parsing telemetry: {e}")

mqtt_client = mqtt.Client()
mqtt_client.on_connect = on_mqtt_connect
mqtt_client.on_message = on_mqtt_message
try:
    mqtt_client.connect("localhost", 1883, 60)
    mqtt_client.loop_start()
except Exception as e:
    print(f"[MQTT] Warning: Could not connect to broker ({e})")



# No longer using in-memory alert history or hardcoded credentials


# ═══════════════════════════════════════════
#  PIPELINE INFERENCE LOOP
# ═══════════════════════════════════════════
def pipeline_loop():
    """Main inference loop running in a background thread."""
    global pipeline_state, last_mesh_frame

    print("[Pipeline] Starting inference loop...")
    pipeline_state["running"] = True

    last_event_time = 0
    frame_times = []

    # Wait for camera to produce frames
    retries = 0
    while camera.get_latest_frame()[1] is None and retries < 50:
        time.sleep(0.2)
        retries += 1

    if camera.get_latest_frame()[1] is None:
        print("[Pipeline] ERROR: Camera never produced a frame")
        pipeline_state["running"] = False
        return

    # Initial driver verification
    _, first_frame = camera.get_latest_frame()
    if first_frame is not None:
        is_known, d_id, d_name, conf, a_truck = certifier.verify_driver(first_frame)
        with pipeline_lock:
            if is_known:
                pipeline_state["missed_checks"] = 0
                if a_truck and a_truck != config.TRUCK_ID:
                    pipeline_state["current_driver_id"] = d_id
                    pipeline_state["current_driver_name"] = d_name + f" (Assigned to {a_truck})"
                    _trigger_alert("UNAUTHORIZED_DRIVER", f"Driver {d_name} is assigned to {a_truck}, not this truck.")
                else:
                    pipeline_state["current_driver_id"] = d_id
                    pipeline_state["current_driver_name"] = d_name
                    
                print(f"[Pipeline] Driver verified: {d_name} ({d_id}) confidence={conf}")
            else:
                pipeline_state["missed_checks"] = 1
                pipeline_state["current_driver_id"] = None
                pipeline_state["current_driver_name"] = "Unverified"
                print("[Pipeline] WARNING: Driver not recognized")
                _trigger_alert("UNAUTHORIZED_DRIVER", "Unrecognized driver at startup")

    last_ts = 0
    last_verify_time = time.time()

    while pipeline_state["running"]:
        try:
            ts, frame = camera.get_latest_frame()
            if ts is None or ts == last_ts:
                time.sleep(0.01)
                continue
            last_ts = ts

            frame_start = time.time()

            # ── 1. Drowsiness Detection ──
            drow_result = drowsiness.process_frame(frame)
            with pipeline_lock:
                pipeline_state["last_ear"] = drow_result["ear"]
                pipeline_state["face_detected"] = drow_result["face_detected"]
            last_mesh_frame = drow_result.get("mesh_frame")

            # ── 2. Phone Detection ──
            phone_result = phone_det.process_frame(frame)
            with pipeline_lock:
                pipeline_state["phone_detected"] = phone_result["phone_detected"]

            # ── 3. Check for events ──
            event = drow_result["event"] or phone_result.get("event")

            if event and (time.time() - last_event_time) > config.ALERT_COOLDOWN_SECONDS:
                last_event_time = time.time()
                desc = f"Detected: {event}"
                if event == "PHONE_USAGE":
                    desc = f"Phone usage detected (conf={phone_result['phone_confidence']})"
                elif event == "DROWSINESS":
                    desc = f"Drowsiness detected (EAR={drow_result['ear']})"
                elif event == "DISTRACTION":
                    desc = f"Driver distraction detected (Not looking at road)"

                _trigger_alert(event, desc)

                drowsiness.reset_counters()
                phone_det.reset_counter()

            #  4. Periodic re-verification (every 60 seconds or if forced) 
            force_check = False
            with pipeline_lock:
                if pipeline_state.get("force_verify", False):
                    force_check = True
                    pipeline_state["force_verify"] = False

            if force_check or (time.time() - last_verify_time > 30):
                last_verify_time = time.time()
                is_known, d_id, d_name, conf, a_truck = certifier.verify_driver(frame)
                with pipeline_lock:
                    if is_known:
                        pipeline_state["missed_checks"] = 0
                        if d_id != pipeline_state["current_driver_id"]:
                            pipeline_state["current_driver_id"] = d_id
                            
                            if a_truck and a_truck != config.TRUCK_ID:
                                pipeline_state["current_driver_name"] = d_name + f" (Assigned to {a_truck})"
                                _trigger_alert("UNAUTHORIZED_DRIVER", f"Driver {d_name} changed, but is assigned to {a_truck}")
                            else:
                                pipeline_state["current_driver_name"] = d_name
                                _add_alert_record("DRIVER_CHANGE", f"Driver changed to {d_name}", "MEDIUM")
                    else:
                        pipeline_state["missed_checks"] += 1
                        misses = pipeline_state["missed_checks"]
                        if misses >= 4:
                            pipeline_state["current_driver_name"] = "Unverified"
                            _trigger_alert("UNVERIFIED_DRIVER", "Driver face not recognized after 3 warnings.")
                            # Cap misses to avoid spamming alerts if we only want it once, or keep at 4 to maintain red state
                            pipeline_state["missed_checks"] = 4 

            # Track FPS
            with pipeline_lock:
                pipeline_state["frames_processed"] += 1
            frame_times.append(time.time() - frame_start)
            if len(frame_times) > 50:
                frame_times = frame_times[-50:]
            avg_time = sum(frame_times) / len(frame_times)
            with pipeline_lock:
                pipeline_state["pipeline_fps"] = round(1.0 / avg_time if avg_time > 0 else 0, 1)

            time.sleep(0.01)

        except Exception as e:
            print(f"[Pipeline] Error in loop: {e}")
            time.sleep(1)


def _trigger_alert(event_type, description):
    """Trigger a full alert with video clip capture."""
    with pipeline_lock:
        pipeline_state["last_event"] = event_type
        pipeline_state["alerts_total"] += 1

    # No longer copies the entire 55MB buffer — EventManager grabs it directly
    # via its injected camera reference when it needs to generate clips.
    result = event_mgr.trigger_alert(
        event_type=event_type,
        buffer_frames=None,  # Deprecated param — camera is injected
        truck_id=config.TRUCK_ID,
        driver_id=pipeline_state["current_driver_id"],
        driver_name=pipeline_state["current_driver_name"],
    )

    _add_alert_record(event_type, description,
                      "CRITICAL" if event_type == "DROWSINESS" else "HIGH",
                      result.get("short_clip_path"))

    print(f"[Pipeline] ALERT: {event_type} — {description}")


def _add_alert_record(event_type, description, severity, clip_path=None):
    """Add to database alert history."""
    db = SessionLocal()
    new_alert = Alert(
        type=event_type,
        severity=severity,
        description=description,
        truck_id=config.TRUCK_ID,
        driver_id=pipeline_state.get("current_driver_id"),
        driver_name=pipeline_state.get("current_driver_name"),
        time=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        timestamp=time.time(),
        clip_path=clip_path
    )
    db.add(new_alert)
    db.commit()
    db.close()


# ═══════════════════════════════════════════
#  REST API ENDPOINTS
# ═══════════════════════════════════════════

@app.get("/api/status")
async def get_status():
    """Pipeline status for the dashboard."""
    cam_stats = camera.get_stats() if camera else {}
    wd_status = watchdog.get_status() if watchdog else {}
    with pipeline_lock:
        ps_copy = dict(pipeline_state)
    return {
        "pipeline": ps_copy,
        "camera": cam_stats,
        "watchdog": wd_status,
        "pending_uploads": event_mgr.get_pending_count() if event_mgr else 0,
        "enrolled_drivers": len(certifier.known_encodings) if certifier else 0,
        "truck_id": config.TRUCK_ID,
    }

class LoginRequest(BaseModel):
    username: str
    password: str

@app.post("/api/login")
async def login(req: LoginRequest):
    db = SessionLocal()
    user = db.query(User).filter(User.username == req.username).first()
    db.close()
    
    if not user or not verify_password(req.password, user.hashed_password):
        raise HTTPException(status_code=401, detail="Invalid credentials")
            
    truck_id = None
    driver_name = "Unknown"
    if user.role == "driver":
        db = SessionLocal()
        driver = db.query(Driver).filter(Driver.id == user.username).first()
        if not driver or not driver.assigned_truck:
            db.close()
            raise HTTPException(status_code=403, detail="Driver is not assigned to any truck by the admin")
        truck_id = driver.assigned_truck
        driver_name = driver.name
        db.close()
        
        # Force a verification check on next frame since driver logged in
        with pipeline_lock:
            pipeline_state["force_verify"] = True
            pipeline_state["missed_checks"] = 0

    access_token = create_access_token(data={"sub": user.username, "role": user.role})
    redirect = "/admin.html" if user.role == "admin" else "/driver.html"
    return {"status": "ok", "role": user.role, "redirect": redirect, "truck_id": truck_id, "driver_name": driver_name, "access_token": access_token}

@app.get("/api/fleet/locations")
async def get_fleet_locations():
    """Returns telemetry of all active trucks."""
    db = SessionLocal()
    res = {}
    for t_id, t_data in fleet_telemetry.items():
        driver = db.query(Driver).filter(Driver.assigned_truck == t_id).first()
        t_data_copy = dict(t_data)
        t_data_copy["driver_name"] = driver.name if driver else "Unknown"
        res[t_id] = t_data_copy
    db.close()
    return res

class JourneyStartRequest(BaseModel):
    truck_id: str
    route_coords: list

@app.post("/api/fleet/start-journey")
async def start_journey(data: JourneyStartRequest, current_user: TokenData = Depends(get_current_user)):
    """Starts a server-side journey simulation."""
    simulator.start_journey(data.truck_id, data.route_coords)
    return {"status": "started", "truck_id": data.truck_id}

class JourneyEndRequest(BaseModel):
    truck_id: str

@app.post("/api/fleet/end-journey")
async def end_journey(data: JourneyEndRequest, current_user: TokenData = Depends(get_current_user)):
    """Halts the server-side journey simulation."""
    simulator.end_journey(data.truck_id)
    return {"status": "ended", "truck_id": data.truck_id}

class InjectAlertRequest(BaseModel):
    truck_id: str
    type: str
    severity: str
    description: str

@app.post("/api/inject-alert")
async def inject_alert(data: InjectAlertRequest, current_user: TokenData = Depends(get_current_admin)):
    """Manually inject an alert from the injector panel."""
    db = SessionLocal()
    new_alert = Alert(
        type=data.type,
        severity=data.severity,
        description=data.description,
        truck_id=data.truck_id,
        driver_id="SIMULATED",
        driver_name="Injector Sim",
        time=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        timestamp=time.time(),
        clip_path=None
    )
    db.add(new_alert)
    db.commit()
    db.refresh(new_alert)
    
    record = {
        "id": new_alert.id,
        "type": new_alert.type,
        "severity": new_alert.severity,
        "description": new_alert.description,
        "truck_id": new_alert.truck_id,
        "driver_id": new_alert.driver_id,
        "driver_name": new_alert.driver_name,
        "timestamp": new_alert.timestamp,
        "clip_path": new_alert.clip_path
    }
    db.close()
    return {"status": "ok", "alert": record}

@app.get("/api/analytics/summary")
async def get_analytics_summary(current_user: TokenData = Depends(get_current_admin)):
    db = SessionLocal()
    journeys = db.query(JourneyRecord).all()
    total_dist = sum(j.distance_km for j in journeys)
    total_fuel = sum(j.fuel_consumed for j in journeys)
    total_alerts = db.query(Alert).count()
    db.close()
    
    return {
        "total_distance_km": round(total_dist, 2),
        "total_fuel_consumed": round(total_fuel, 2),
        "total_alerts": total_alerts,
        "fleet_safety_score": max(0, 100 - (total_alerts * 2))
    }

@app.get("/api/analytics/drivers")
async def get_analytics_drivers(current_user: TokenData = Depends(get_current_admin)):
    db = SessionLocal()
    drivers = db.query(Driver).all()
    
    results = []
    for d in drivers:
        journeys = db.query(JourneyRecord).filter(JourneyRecord.driver_id == d.id).all()
        dist = sum(j.distance_km for j in journeys)
        alerts = db.query(Alert).filter(Alert.driver_id == d.id).count()
        
        risk = (alerts / (dist / 100.0)) if dist > 0 else 0
        
        results.append({
            "driver_id": d.id,
            "name": d.name,
            "distance_km": round(dist, 2),
            "alerts": alerts,
            "risk_score": round(risk, 2)
        })
        
    db.close()
    results.sort(key=lambda x: x["risk_score"], reverse=True)
    return results


@app.get("/api/alerts")
async def get_alerts(limit: int = 50, current_user: TokenData = Depends(get_current_user)):
    """Return recent alerts."""
    db = SessionLocal()
    alerts = db.query(Alert).order_by(Alert.id.desc()).limit(limit).all()
    result = []
    for a in alerts:
        result.append({
            "id": a.id,
            "type": a.type,
            "severity": a.severity,
            "description": a.description,
            "truck_id": a.truck_id,
            "driver_id": a.driver_id,
            "driver_name": a.driver_name,
            "time": a.time,
            "timestamp": a.timestamp,
            "clip_path": a.clip_path
        })
    db.close()
    return result

@app.get("/api/alerts/{event_id}/video/{clip_type}")
async def get_alert_video(event_id: str, clip_type: str):
    """
    clip_type must be '20s' or '60s'.
    Returns the video file if it exists locally.
    """
    if clip_type not in ["20s", "60s"]:
        raise HTTPException(status_code=400, detail="Invalid clip type")
        
    # The full path would be in the local_storage/alerts directory
    # since we name them {event_id}_20s.mp4 etc.
    filepath = os.path.join(event_mgr.alerts_dir, f"{event_id}_{clip_type}.mp4")
    
    if os.path.exists(filepath):
        from fastapi.responses import FileResponse
        return FileResponse(filepath, media_type="video/mp4")
    else:
        # Check if it was moved to pending_uploads due to offline mode
        pending_path = os.path.join(event_mgr.pending_uploads_dir, event_id, f"{event_id}_{clip_type}.mp4")
        if os.path.exists(pending_path):
            from fastapi.responses import FileResponse
            return FileResponse(pending_path, media_type="video/mp4")
            
        raise HTTPException(status_code=404, detail="Video file not found locally")


@app.get("/api/live-frame")
async def get_live_frame():
    """Return current camera frame as JPEG for dashboard live preview."""
    if camera is None:
        raise HTTPException(status_code=503, detail="Camera not initialized")
    ts, frame = camera.get_latest_frame()
    if frame is None:
        raise HTTPException(status_code=503, detail="No frame available")

    # Draw overlays
    overlay = frame.copy()

    # Draw face detection info
    if pipeline_state["face_detected"]:
        ear = pipeline_state["last_ear"]
        color = (0, 255, 0) if ear > config.EAR_THRESHOLD else (0, 0, 255)
        cv2.putText(overlay, f"EAR: {ear:.2f}", (10, 25),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.5, color, 1)

    if pipeline_state["phone_detected"]:
        cv2.putText(overlay, "PHONE DETECTED", (10, 45),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 0, 255), 2)

    driver_name = pipeline_state.get("current_driver_name", "Unknown")
    cv2.putText(overlay, f"Driver: {driver_name}", (10, overlay.shape[0] - 10),
                cv2.FONT_HERSHEY_SIMPLEX, 0.4, (200, 200, 200), 1)

    _, jpeg = cv2.imencode(".jpg", overlay, [cv2.IMWRITE_JPEG_QUALITY, 70])
    return StreamingResponse(
        iter([jpeg.tobytes()]),
        media_type="image/jpeg",
    )

@app.get("/api/live-frame-mesh")
async def get_live_frame_mesh():
    """Return frame with face mesh drawn."""
    global last_mesh_frame
    if last_mesh_frame is None:
        # Fallback to normal if mesh frame not ready
        if camera is None:
            raise HTTPException(status_code=503, detail="Camera not initialized")
        ts, frame = camera.get_latest_frame()
        if frame is None:
            raise HTTPException(status_code=503, detail="No frame available")
        last_mesh_frame = frame

    _, jpeg = cv2.imencode(".jpg", last_mesh_frame, [cv2.IMWRITE_JPEG_QUALITY, 70])
    return StreamingResponse(
        iter([jpeg.tobytes()]),
        media_type="image/jpeg",
    )

@app.get("/api/telemetry")
async def get_telemetry():
    """Return mock telemetry for driver dashboard."""
    import random
    speed = random.uniform(60, 85)
    rpm = random.uniform(1100, 1500)
    
    # Sometimes trigger a UI alert
    alert = None
    if random.random() < 0.02:
        alert = random.choice(["OVERSPEEDING", "HARD_BRAKING"])
        
    return {
        "speed": round(speed),
        "rpm": round(rpm),
        "alert": alert
    }


@app.get("/api/drivers")
async def get_drivers():
    """List enrolled drivers."""
    return certifier.get_enrolled_drivers()


@app.post("/api/drivers/enroll/camera")
async def enroll_driver_camera(request: Request, current_user: TokenData = Depends(get_current_admin)):
    """
    Enroll a new driver from webcam.
    """
    data = await request.json()

    driver_id = data.get("driver_id", "")
    name = data.get("name", "")
    image_b64 = data.get("image", "")
    password = data.get("password", "")
    assigned_truck = data.get("assigned_truck", None)

    if not driver_id or not name or not image_b64 or not password:
        raise HTTPException(status_code=400, detail="driver_id, name, password, and image are required")

    if assigned_truck:
        db = SessionLocal()
        count = db.query(Driver).filter(Driver.assigned_truck == assigned_truck, Driver.id != driver_id).count()
        db.close()
        if count >= 3:
            raise HTTPException(status_code=400, detail=f"Maximum 3 drivers allowed per truck. {assigned_truck} is full.")

    try:
        if "," in image_b64:
            image_b64 = image_b64.split(",")[1]
        img_bytes = base64.b64decode(image_b64)
        np_arr = np.frombuffer(img_bytes, np.uint8)
        frame = cv2.imdecode(np_arr, cv2.IMREAD_COLOR)
        if frame is None:
            raise ValueError("Invalid image")
    except Exception as e:
        raise HTTPException(status_code=400, detail="Invalid base64 image")

    result = certifier.enroll_driver(driver_id, name, frame, assigned_truck)
    if result is True:
        db = SessionLocal()
        existing_user = db.query(User).filter(User.username == driver_id).first()
        hashed_pw = get_password_hash(password)
        if existing_user:
            existing_user.hashed_password = hashed_pw
        else:
            db.add(User(username=driver_id, hashed_password=hashed_pw, role="driver"))
        db.commit()
        db.close()
        return {"status": "enrolled", "driver_id": driver_id, "name": name, "assigned_truck": assigned_truck}
    else:
        raise HTTPException(status_code=400, detail=result)

@app.post("/api/drivers/enroll/upload")
async def enroll_driver_upload(
    driver_id: str = Form(...),
    name: str = Form(...),
    password: str = Form(...),
    assigned_truck: str = Form(None),
    file: UploadFile = File(...),
    current_user: TokenData = Depends(get_current_admin)
):
    """Enroll a driver by uploading an image file."""
    if not driver_id or not name or not password:
        raise HTTPException(status_code=400, detail="driver_id, name, and password are required")
        
    if assigned_truck:
        db = SessionLocal()
        count = db.query(Driver).filter(Driver.assigned_truck == assigned_truck, Driver.id != driver_id).count()
        db.close()
        if count >= 3:
            raise HTTPException(status_code=400, detail=f"Maximum 3 drivers allowed per truck. {assigned_truck} is full.")
        
    try:
        contents = await file.read()
        nparr = np.frombuffer(contents, np.uint8)
        img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
        if img is None:
            raise ValueError("Could not decode image")
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Invalid file upload: {str(e)}")

    result = certifier.enroll_driver(driver_id, name, img, assigned_truck)
    if result is True:
        db = SessionLocal()
        existing_user = db.query(User).filter(User.username == driver_id).first()
        hashed_pw = get_password_hash(password)
        if existing_user:
            existing_user.hashed_password = hashed_pw
        else:
            db.add(User(username=driver_id, hashed_password=hashed_pw, role="driver"))
        db.commit()
        db.close()
        return {"status": "enrolled", "driver_id": driver_id, "name": name, "assigned_truck": assigned_truck}
    else:
        raise HTTPException(status_code=400, detail=result)


@app.delete("/api/drivers/{driver_id}")
async def remove_driver(driver_id: str, current_user: TokenData = Depends(get_current_admin)):
    """Remove a driver. Requires admin auth."""
    certifier.remove_driver(driver_id)
    return {"status": "removed", "driver_id": driver_id}

class ReassignRequest(BaseModel):
    assigned_truck: str

@app.put("/api/drivers/{driver_id}/reassign")
async def reassign_driver(driver_id: str, data: ReassignRequest, current_user: TokenData = Depends(get_current_admin)):
    db = SessionLocal()
    
    if data.assigned_truck:
        count = db.query(Driver).filter(Driver.assigned_truck == data.assigned_truck, Driver.id != driver_id).count()
        if count >= 3:
            db.close()
            raise HTTPException(status_code=400, detail=f"Maximum 3 drivers allowed per truck. {data.assigned_truck} is full.")
            
    driver = db.query(Driver).filter(Driver.id == driver_id).first()
    if not driver:
        db.close()
        raise HTTPException(status_code=404, detail="Driver not found")
    driver.assigned_truck = data.assigned_truck
    db.commit()
    db.close()
    return {"status": "ok", "assigned_truck": data.assigned_truck}

@app.get("/api/trucks")
async def get_trucks(current_user: TokenData = Depends(get_current_admin)):
    db = SessionLocal()
    trucks = db.query(Truck).all()
    db.close()
    return [t.id for t in trucks]

@app.post("/api/trucks")
async def add_truck(current_user: TokenData = Depends(get_current_admin)):
    db = SessionLocal()
    count = db.query(Truck).count()
    next_id = f"TRK-{count+1:03d}"
    db.add(Truck(id=next_id))
    db.commit()
    db.close()
    return {"status": "ok", "truck_id": next_id}

class ResetVerificationRequest(BaseModel):
    password: str

@app.post("/api/trucks/{truck_id}/reset_verification")
async def reset_verification(truck_id: str, data: ResetVerificationRequest, current_user: TokenData = Depends(get_current_admin)):
    """Reset the driver verification missed_checks count. Requires admin password confirmation."""
    db = SessionLocal()
    admin = db.query(User).filter(User.username == current_user.username).first()
    db.close()
    
    if not admin or not verify_password(data.password, admin.hashed_password):
        raise HTTPException(status_code=403, detail="Invalid admin password")
        
    if truck_id != config.TRUCK_ID:
        raise HTTPException(status_code=400, detail=f"This edge device only controls {config.TRUCK_ID}")
        
    with pipeline_lock:
        pipeline_state["missed_checks"] = 0
        pipeline_state["force_verify"] = True
        
    return {"status": "ok"}


@app.get("/api/drivers/{driver_id}/thumbnail")
async def get_driver_thumbnail(driver_id: str):
    """Return driver's face thumbnail."""
    path = certifier.get_driver_thumbnail(driver_id)
    if path and os.path.exists(path):
        return FileResponse(path, media_type="image/jpeg")
    raise HTTPException(status_code=404, detail="Thumbnail not found")


@app.get("/api/clips/{filename}")
async def get_clip(filename: str):
    """Serve a video clip."""
    clip_path = os.path.join(config.LOCAL_STORAGE_DIR, "alerts", filename)
    if os.path.exists(clip_path):
        return FileResponse(clip_path, media_type="video/mp4")
    raise HTTPException(status_code=404, detail="Clip not found")

@app.post("/api/system/shutdown")
async def system_shutdown(current_user: TokenData = Depends(get_current_admin)):
    """Kill switch to gracefully shut down the entire system."""
    print("[Shutdown] Kill switch activated via Admin portal!")
    
    def _do_shutdown():
        time.sleep(1)
        if camera:
            camera.flush_to_disk("emergency_shutdown_clip.mp4")
            camera.stop()
        os._exit(0)
        
    threading.Thread(target=_do_shutdown, daemon=True).start()
    return {"status": "shutting down"}


@app.post('/api/system/camera/pause')
async def pause_camera(current_user: TokenData = Depends(get_current_admin)):
    if camera:
        camera.pause()
    return {'status': 'paused'}

@app.post('/api/system/camera/resume')
async def resume_camera(current_user: TokenData = Depends(get_current_admin)):
    if camera:
        camera.resume()
    return {'status': 'resumed'}


# Serve frontend

frontend_dir = os.path.join(os.path.dirname(__file__), "dashboard")
if os.path.exists(frontend_dir):
    app.mount("/", StaticFiles(directory=frontend_dir, html=True), name="dashboard")


# ═══════════════════════════════════════════
#  ENTRY POINT
# ═══════════════════════════════════════════

if __name__ == "__main__":
    uvicorn.run(
        "main:app",
        host=config.API_HOST,
        port=config.API_PORT,
        reload=False,
        log_level="info",
    )

# ==============================================================================
# Testing Override API (Single-PC Camera Hardware Lock)

@app.post('/api/driver/force_verify')
async def manual_force_verify():
    with pipeline_lock:
        pipeline_state['force_verify'] = True
    return {'status': 'ok'}
