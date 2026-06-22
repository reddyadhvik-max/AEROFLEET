import asyncio
import json
import os
import time
import paho.mqtt.client as mqtt
import asyncpg
import datetime

DB_HOST = os.getenv("DB_HOST", "localhost")
DB_PORT = os.getenv("DB_PORT", "5432")
DB_USER = os.getenv("DB_USER", "admin")
DB_PASS = os.getenv("DB_PASS", "adminpassword")
DB_NAME = os.getenv("DB_NAME", "aerofleet")
MQTT_HOST = os.getenv("MQTT_HOST", "localhost")

# Thresholds
PROFILES = {
    "Freightliner Cascadia": {"coolant_max": 210, "oil_min": 35, "boost_max": 35},
    "Volvo VNL": {"coolant_max": 205, "oil_min": 40, "boost_max": 38},
    "Peterbilt 579": {"coolant_max": 215, "oil_min": 35, "boost_max": 40}
}
SPEED_LIMIT = 80
SPEED_BUFFER = 5

class StreamProcessor:
    def __init__(self):
        self.pool = None
        self.mqtt_client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, "stream_processor")
        self.mqtt_client.on_connect = self.on_connect
        self.mqtt_client.on_message = self.on_message
        self.loop = asyncio.get_event_loop()
        self.truck_state = {} # To track fuel levels

        
    async def connect_db(self):
        print(f"Connecting to TimescaleDB at {DB_HOST}...")
        try:
            self.pool = await asyncpg.create_pool(user=DB_USER, password=DB_PASS, database=DB_NAME, host=DB_HOST, port=DB_PORT, min_size=5, max_size=20)
            print("Connected to TimescaleDB.")
        except Exception as e:
            print(f"Failed to connect to DB: {e}")
            raise

    def start_mqtt(self):
        print(f"Connecting to MQTT broker at {MQTT_HOST}...")
        self.mqtt_client.connect(MQTT_HOST, 1883, 60)
        self.mqtt_client.loop_start()

    def on_connect(self, client, userdata, flags, reason_code, properties):
        print("MQTT Connected.")
        client.subscribe("truck/+/telemetry")
        client.subscribe("truck/+/event")

    def on_message(self, client, userdata, msg):
        try:
            data = json.loads(msg.payload.decode())
            topic = msg.topic
            
            if topic.endswith("/telemetry"):
                # Handle async DB insert in the event loop
                asyncio.run_coroutine_threadsafe(self.process_telemetry(data), self.loop)
            elif topic.endswith("/event"):
                # Manual events (like signal jumps)
                asyncio.run_coroutine_threadsafe(self.process_event(data), self.loop)
        except Exception as e:
            print(f"Error processing message: {e}")

    async def process_telemetry(self, data):
        if not self.pool:
            return
            
        truck_id = data.get("truck_id")
        model = data.get("model")
        speed = data.get("speed_kmh", 0)
        rpm = data.get("rpm", 0)
        coolant = data.get("coolant_temp_f", 0)
        oil = data.get("oil_pressure_psi", 0)
        boost = data.get("boost_pressure_psi", 0)
        fuel_pct = data.get("fuel_pct", 100.0)
        brake_g = data.get("brake_g", 0.0)
        tyre_psi = data.get("tyre_psi", 105.0)
        
        # 1. Insert Telemetry
        try:
            async with self.pool.acquire() as conn:
                await conn.execute("""
                    INSERT INTO telemetry (time, truck_id, model, speed_kmh, rpm, coolant_temp_f, oil_pressure_psi, boost_pressure_psi, fuel_rate_gal_hr, fuel_pct, brake_g, tyre_psi, lat, lng, progress_pct)
                    VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15)
                """, datetime.datetime.now(datetime.timezone.utc), truck_id, model, speed, rpm,
                   coolant, oil, boost, data.get("fuel_rate_gal_hr", 0), fuel_pct, brake_g, tyre_psi, data.get("lat", 0), data.get("lng", 0), data.get("progress_pct", 0))
        except Exception as e:
            print(f"Error inserting telemetry: {e}")
            
        # 2. Check Anomalies
        alerts = []
        profile = PROFILES.get(model)
        
        if profile:
            if coolant > profile["coolant_max"]:
                alerts.append(("Overheating", "HIGH", f"Coolant Temp {coolant}°F exceeds max {profile['coolant_max']}°F"))
            if oil < profile["oil_min"] and speed > 0: # Only check oil pressure if engine is running
                alerts.append(("Low Oil Pressure", "HIGH", f"Oil Pressure {oil} psi below min {profile['oil_min']} psi"))
            if boost > profile["boost_max"]:
                alerts.append(("Overboost", "MEDIUM", f"Boost Pressure {boost} psi exceeds max {profile['boost_max']} psi"))
                
        # 3. Check Speed Limits & New OBD-II metrics
        if speed > SPEED_LIMIT:
            if speed <= (SPEED_LIMIT + SPEED_BUFFER):
                alerts.append(("Speed Warning", "LOW", f"Speed {speed:.1f} km/h slightly exceeds limit {SPEED_LIMIT} km/h (Onboard Warning)"))
            else:
                alerts.append(("Speed Violation", "CRITICAL", f"Speed {speed:.1f} km/h severely exceeds limit {SPEED_LIMIT} km/h"))

        if brake_g > 0.4:
            alerts.append(("Harsh Braking", "CRITICAL", f"Harsh braking detected with {brake_g:.2f}g force"))

        if tyre_psi < 90:
            alerts.append(("Low Tyre Pressure", "HIGH", f"Tyre pressure {tyre_psi:.1f} psi is dangerously low"))

        # Fuel Theft Check
        if truck_id not in self.truck_state:
            self.truck_state[truck_id] = {"fuel": fuel_pct, "time": time.time()}
        else:
            prev = self.truck_state[truck_id]
            # check if drop is > 10% in short period (to avoid false positives over long hauls, we just check absolute drop since last reading since it's a fast stream)
            if prev["fuel"] - fuel_pct > 10.0:
                alerts.append(("Suspected Fuel Leak / Theft", "CRITICAL", f"Fuel level dropped abnormally by {(prev['fuel'] - fuel_pct):.1f}%"))
            self.truck_state[truck_id] = {"fuel": fuel_pct, "time": time.time()}

        # 4. Insert Alerts
        for alert_type, severity, desc in alerts:
            try:
                async with self.pool.acquire() as conn:
                    await conn.execute("""
                        INSERT INTO alerts (time, truck_id, type, severity, description, speed_at_alert)
                        VALUES ($1, $2, $3, $4, $5, $6)
                    """, datetime.datetime.now(datetime.timezone.utc), truck_id, alert_type, severity, desc, speed)
                
                # If Critical, automatically trigger camera buffer via MQTT
                if severity == "CRITICAL" or alert_type in ["Overheating", "Low Oil Pressure"]:
                    self.mqtt_client.publish(f"truck/{truck_id}/emergency/trigger", payload=json.dumps({"reason": desc}))
            except Exception as e:
                print(f"Error inserting alert: {e}")

    async def process_event(self, data):
        if not self.pool:
            return
        
        truck_id = data.get("truck_id")
        event_type = data.get("event")
        
        if event_type == "signal_jump" or event_type == "erratic_driving":
            desc = f"Driver violation detected: {event_type.replace('_', ' ').title()}"
            async with self.pool.acquire() as conn:
                await conn.execute("""
                    INSERT INTO alerts (time, truck_id, type, severity, description)
                    VALUES ($1, $2, $3, $4, $5)
                """, datetime.datetime.now(datetime.timezone.utc), truck_id, "Driver Violation", "CRITICAL", desc)
            
            self.mqtt_client.publish(f"truck/{truck_id}/emergency/trigger", payload=json.dumps({"reason": desc}))

async def main():
    processor = StreamProcessor()
    await processor.connect_db()
    processor.start_mqtt()
    
    print("Stream Processor running...")
    while True:
        await asyncio.sleep(1)

if __name__ == "__main__":
    asyncio.run(main())
