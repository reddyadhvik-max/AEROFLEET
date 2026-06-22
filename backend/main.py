from fastapi import FastAPI, UploadFile, File, HTTPException, BackgroundTasks
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import List, Optional
import os
import shutil
import datetime
import asyncpg

app = FastAPI(title="Aerofleet API")

# Setup CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Configuration
DB_HOST = os.getenv("DB_HOST", "localhost")
DB_PORT = os.getenv("DB_PORT", "5432")
DB_USER = os.getenv("DB_USER", "admin")
DB_PASS = os.getenv("DB_PASS", "adminpassword")
DB_NAME = os.getenv("DB_NAME", "aerofleet")
VIDEO_UPLOAD_DIR = os.path.join(os.path.dirname(__file__), "..", "videos")

os.makedirs(VIDEO_UPLOAD_DIR, exist_ok=True)

async def get_db():
    return await asyncpg.connect(user=DB_USER, password=DB_PASS, database=DB_NAME, host=DB_HOST, port=DB_PORT)

class AlertTrigger(BaseModel):
    truck_id: str
    type: str
    severity: str
    description: str
    speed: Optional[float] = None

@app.get("/api/trucks")
async def get_trucks():
    try:
        conn = await get_db()
        rows = await conn.fetch("SELECT DISTINCT truck_id, model FROM telemetry")
        await conn.close()
        return [{"id": row['truck_id'], "model": row['model']} for row in rows]
    except Exception as e:
        return {"error": str(e)}

@app.get("/api/telemetry/{truck_id}")
async def get_telemetry(truck_id: str, limit: int = 100):
    try:
        conn = await get_db()
        rows = await conn.fetch(
            "SELECT * FROM telemetry WHERE truck_id = $1 ORDER BY time DESC LIMIT $2", 
            truck_id, limit
        )
        await conn.close()
        return [dict(row) for row in rows]
    except Exception as e:
        return {"error": str(e)}

@app.get("/api/alerts")
async def get_alerts(limit: int = 50):
    try:
        conn = await get_db()
        rows = await conn.fetch("SELECT * FROM alerts ORDER BY time DESC LIMIT $1", limit)
        await conn.close()
        return [dict(row) for row in rows]
    except Exception as e:
        return {"error": str(e)}

@app.get("/api/telemetry/rollup/1m")
async def get_rollup_1m(truck_id: str):
    try:
        conn = await get_db()
        # Refresh the materialized view before query to ensure live data (only for prototype, in prod use continuous aggregate policies)
        await conn.execute("CALL refresh_continuous_aggregate('rollup_1m', NULL, NULL);")
        rows = await conn.fetch("SELECT * FROM rollup_1m WHERE truck_id = $1 ORDER BY bucket DESC LIMIT 1", truck_id)
        await conn.close()
        return dict(rows[0]) if rows else {}
    except Exception as e:
        return {"error": str(e)}

@app.get("/api/telemetry/rollup/1h")
async def get_rollup_1h(truck_id: str):
    try:
        conn = await get_db()
        await conn.execute("CALL refresh_continuous_aggregate('rollup_1h', NULL, NULL);")
        rows = await conn.fetch("SELECT * FROM rollup_1h WHERE truck_id = $1 ORDER BY bucket DESC LIMIT 1", truck_id)
        await conn.close()
        return dict(rows[0]) if rows else {}
    except Exception as e:
        return {"error": str(e)}

@app.post("/api/alerts/emergency")
async def trigger_emergency(alert: AlertTrigger):
    import paho.mqtt.publish as publish
    
    # Insert alert into database
    try:
        conn = await get_db()
        await conn.execute(
            "INSERT INTO alerts (time, truck_id, type, severity, description, speed_at_alert) VALUES ($1, $2, $3, $4, $5, $6)",
            datetime.datetime.now(datetime.timezone.utc), alert.truck_id, alert.type, alert.severity, alert.description, alert.speed
        )
        await conn.close()
    except Exception as e:
        print(f"Error saving alert: {e}")

    # Publish MQTT message to trigger camera buffer
    # Note: Using localhost for MQTT broker
    try:
        publish.single(f"truck/{alert.truck_id}/emergency/trigger", payload=alert.description, hostname="localhost", port=1883)
    except Exception as e:
        print(f"Failed to publish to MQTT: {e}")
        
    return {"status": "Emergency Alert Triggered"}

@app.post("/api/alerts/upload-video/{truck_id}")
async def upload_video(truck_id: str, file: UploadFile = File(...)):
    filename = f"emergency_{truck_id}_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}.mp4"
    filepath = os.path.join(VIDEO_UPLOAD_DIR, filename)
    
    with open(filepath, "wb") as buffer:
        shutil.copyfileobj(file.file, buffer)
        
    # Update latest active alert for this truck with the video path
    try:
        conn = await get_db()
        await conn.execute(
            "UPDATE alerts SET video_path = $1 WHERE truck_id = $2 AND video_path IS NULL AND time > NOW() - INTERVAL '5 minutes'",
            f"/videos/{filename}", truck_id
        )
        await conn.close()
    except Exception as e:
        print(f"DB update failed: {e}")

    # Notify dashboard via MQTT that video is ready
    try:
        import paho.mqtt.publish as publish
        publish.single(f"truck/{truck_id}/emergency/video_ready", payload=f"/videos/{filename}", hostname="localhost", port=1883)
    except Exception as e:
        pass

    return {"status": "uploaded", "url": f"/videos/{filename}"}

# Serve static files for videos
app.mount("/videos", StaticFiles(directory=VIDEO_UPLOAD_DIR), name="videos")

# Serve frontend static files
frontend_dir = os.path.join(os.path.dirname(__file__), "..", "frontend")
os.makedirs(frontend_dir, exist_ok=True)
app.mount("/", StaticFiles(directory=frontend_dir, html=True), name="frontend")
