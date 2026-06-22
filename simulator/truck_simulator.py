import time
import json
import random
import paho.mqtt.client as mqtt
import math

MQTT_HOST = "localhost"
MQTT_PORT = 1883

TRUCKS = [
    {"id": "TRK-001", "model": "Freightliner Cascadia"},
    {"id": "TRK-002", "model": "Volvo VNL"},
    {"id": "TRK-003", "model": "Peterbilt 579"}
]

PROFILES = {
    "Freightliner Cascadia": {"rpm": (1100, 1300), "coolant": (190, 210), "oil": (35, 45), "boost": (25, 35), "idle_fuel": 0.6},
    "Volvo VNL": {"rpm": (1050, 1250), "coolant": (195, 205), "oil": (40, 55), "boost": (28, 38), "idle_fuel": 0.7},
    "Peterbilt 579": {"rpm": (1150, 1400), "coolant": (190, 215), "oil": (35, 40), "boost": (30, 40), "idle_fuel": 0.8}
}

ROUTE = [
    (13.07, 77.80), # Hoskote, Bengaluru
    (14.68, 77.60), # Anantapur
    (17.38, 78.48), # Hyderabad
    (19.13, 79.52), # Adilabad
    (21.15, 79.08), # Nagpur
    (23.18, 79.98), # Jabalpur
    (25.45, 78.57), # Jhansi
    (27.18, 78.01), # Agra
    (28.61, 77.20)  # Delhi
]

def interpolate_position(route, progress_pct):
    if progress_pct >= 100:
        return route[-1]
    
    total_segments = len(route) - 1
    pct_per_segment = 100.0 / total_segments
    
    segment_idx = int(progress_pct // pct_per_segment)
    segment_pct = (progress_pct % pct_per_segment) / pct_per_segment
    
    if segment_idx >= total_segments:
        return route[-1]
        
    start_point = route[segment_idx]
    end_point = route[segment_idx + 1]
    
    lat = start_point[0] + (end_point[0] - start_point[0]) * segment_pct
    lng = start_point[1] + (end_point[1] - start_point[1]) * segment_pct
    return (lat, lng)

class Simulator:
    def __init__(self):
        self.client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, "simulator")
        self.client.on_connect = self.on_connect
        self.client.on_message = self.on_message
        self.truck_states = {}
        for t in TRUCKS:
            self.truck_states[t["id"]] = {
                "progress": random.uniform(0, 10),
                "state": "CRUISE",
                "fuel_pct": random.uniform(80, 100),
                "overrides": {}
            }

    def on_connect(self, client, userdata, flags, reason_code, properties):
        print("Simulator Connected to MQTT")
        client.subscribe("truck/+/command/override")

    def on_message(self, client, userdata, msg):
        try:
            topic_parts = msg.topic.split('/')
            if len(topic_parts) >= 4 and topic_parts[2] == "command" and topic_parts[3] == "override":
                truck_id = topic_parts[1]
                data = json.loads(msg.payload.decode())
                if truck_id in self.truck_states:
                    # Update overrides
                    for k, v in data.items():
                        if v is None or v == "":
                            self.truck_states[truck_id]["overrides"].pop(k, None)
                        else:
                            self.truck_states[truck_id]["overrides"][k] = float(v)
                    print(f"Applied override to {truck_id}: {data}")
        except Exception as e:
            print(f"Error processing override: {e}")

    def generate_base_telemetry(self, truck, state_obj):
        profile = PROFILES[truck["model"]]
        state = state_obj["state"]
        
        if state == "IDLE":
            return {
                "speed_kmh": 0,
                "rpm": 600 + random.uniform(-10, 10),
                "coolant_temp_f": profile["coolant"][0] - 10,
                "oil_pressure_psi": profile["oil"][0] - 5,
                "boost_pressure_psi": random.uniform(0, 2),
                "fuel_rate_gal_hr": profile["idle_fuel"],
                "brake_g": 0.0,
                "tyre_psi": 105.0 + random.uniform(-1, 1)
            }
        elif state == "CRUISE":
            return {
                "speed_kmh": random.uniform(70, 79),
                "rpm": random.uniform(profile["rpm"][0], profile["rpm"][1]),
                "coolant_temp_f": random.uniform(profile["coolant"][0], profile["coolant"][1]),
                "oil_pressure_psi": random.uniform(profile["oil"][0], profile["oil"][1]),
                "boost_pressure_psi": random.uniform(profile["boost"][0] - 5, profile["boost"][0]),
                "fuel_rate_gal_hr": profile["idle_fuel"] * 10,
                "brake_g": 0.0,
                "tyre_psi": 105.0 + random.uniform(-1, 1)
            }
        elif state == "SPEEDING":
            return {
                "speed_kmh": random.uniform(81, 95),
                "rpm": random.uniform(profile["rpm"][1] - 50, profile["rpm"][1] + 100),
                "coolant_temp_f": random.uniform(profile["coolant"][0] + 5, profile["coolant"][1] + 5),
                "oil_pressure_psi": random.uniform(profile["oil"][0] + 2, profile["oil"][1] + 5),
                "boost_pressure_psi": random.uniform(profile["boost"][0], profile["boost"][1]),
                "fuel_rate_gal_hr": profile["idle_fuel"] * 12,
                "brake_g": 0.0,
                "tyre_psi": 105.0 + random.uniform(-1, 1)
            }

    def start(self):
        print(f"Connecting to MQTT at {MQTT_HOST}:{MQTT_PORT}...")
        self.client.connect(MQTT_HOST, MQTT_PORT, 60)
        self.client.loop_start()
        
        while True:
            for truck in TRUCKS:
                t_id = truck["id"]
                state_obj = self.truck_states[t_id]
                
                # Randomly change driving state if no override active
                if random.random() < 0.05 and not state_obj["overrides"]:
                    state_obj["state"] = random.choice(["CRUISE", "CRUISE", "CRUISE", "SPEEDING", "IDLE"])
                
                # Reduce fuel slowly
                state_obj["fuel_pct"] -= 0.005
                if state_obj["fuel_pct"] < 0: state_obj["fuel_pct"] = 100.0
                
                # Advance route progress
                if state_obj["state"] != "IDLE":
                    speed_factor = 0.05 if state_obj["state"] == "SPEEDING" else 0.03
                    state_obj["progress"] += speed_factor
                    if state_obj["progress"] > 100:
                        state_obj["progress"] = 0 
                
                # Generate Telemetry
                data = self.generate_base_telemetry(truck, state_obj)
                data["fuel_pct"] = state_obj["fuel_pct"]
                
                # Apply Overrides (Injector)
                for k, v in state_obj["overrides"].items():
                    data[k] = v
                    
                # Physics linkage: If Brake G is high, drop speed and RPM
                if data.get("brake_g", 0) > 0.1:
                    decel = data["brake_g"] * 35.0 # e.g. 0.5g -> drop 17 km/h per tick
                    data["speed_kmh"] = max(0, data["speed_kmh"] - decel)
                    if data["speed_kmh"] == 0:
                        data["rpm"] = 600
                    else:
                        data["rpm"] = max(600, data["rpm"] - (decel * 20))

                # Update fuel_pct state if overridden
                if "fuel_pct" in state_obj["overrides"]:
                    state_obj["fuel_pct"] = data["fuel_pct"]
                
                data["truck_id"] = t_id
                data["model"] = truck["model"]
                data["state"] = state_obj["state"]
                data["progress_pct"] = state_obj["progress"]
                
                lat, lng = interpolate_position(ROUTE, state_obj["progress"])
                data["lat"] = lat
                data["lng"] = lng
                
                # Publish Telemetry
                self.client.publish(f"truck/{t_id}/telemetry", json.dumps(data))
                
            time.sleep(1)

if __name__ == "__main__":
    sim = Simulator()
    try:
        sim.start()
    except KeyboardInterrupt:
        print("Simulator stopped.")
