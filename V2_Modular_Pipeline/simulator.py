import asyncio
import random
import time
import math
from database import SessionLocal, JourneyRecord, Alert, Driver

def haversine(lon1, lat1, lon2, lat2):
    R = 6371.0
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = math.sin(dlat / 2)**2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon / 2)**2
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return R * c

class SimulationEngine:
    def __init__(self, fleet_telemetry_ref):
        """
        fleet_telemetry_ref is a reference to the global fleet_telemetry dictionary in main.py
        so we can update it directly.
        """
        self.fleet_telemetry = fleet_telemetry_ref
        self.active_journeys = {}
        self.running = False
        self._task = None

    def start_journey(self, truck_id, route_coords):
        """Register a new journey for a truck."""
        self.active_journeys[truck_id] = {
            "route": route_coords,
            "current_index": 0,
            "fraction": 0.0,
            "last_tick": time.time(),
            "start_time": time.time()
        }
        
        # Ensure the truck exists in telemetry
        if truck_id not in self.fleet_telemetry:
            self.fleet_telemetry[truck_id] = {}
            
        self.fleet_telemetry[truck_id]["route"] = route_coords
        self.fleet_telemetry[truck_id]["active"] = True

    def end_journey(self, truck_id):
        """Halt a journey for a truck."""
        if truck_id in self.active_journeys:
            state = self.active_journeys[truck_id]
            route = state["route"]
            
            dist = 0.0
            if len(route) > 1:
                for i in range(len(route)-1):
                    dist += haversine(route[i][0], route[i][1], route[i+1][0], route[i+1][1])
                    
            db = SessionLocal()
            driver = db.query(Driver).filter(Driver.assigned_truck == truck_id).first()
            d_id = driver.id if driver else "UNKNOWN"
            
            start_t = state["start_time"]
            end_t = time.time()
            alerts_c = db.query(Alert).filter(Alert.truck_id == truck_id, Alert.timestamp >= start_t, Alert.timestamp <= end_t).count()
            
            db.add(JourneyRecord(
                truck_id=truck_id,
                driver_id=d_id,
                start_time=start_t,
                end_time=end_t,
                distance_km=dist,
                fuel_consumed=10.0,
                alerts_count=alerts_c
            ))
            db.commit()
            db.close()
            
            del self.active_journeys[truck_id]
        if truck_id in self.fleet_telemetry:
            self.fleet_telemetry[truck_id]["route"] = []
            self.fleet_telemetry[truck_id]["speed"] = 0
            self.fleet_telemetry[truck_id]["rpm"] = 0

    def start(self):
        """Start the background task loop."""
        self.running = True
        self._task = asyncio.create_task(self._loop())

    def stop(self):
        """Stop the background task."""
        self.running = False
        if self._task:
            self._task.cancel()

    async def _loop(self):
        """The main simulation loop ticking every 1 second."""
        while self.running:
            try:
                self._tick()
            except Exception as e:
                print(f"[SimulationEngine] Error in tick: {e}")
            await asyncio.sleep(1.0)

    def _tick(self):
        """Process one step of simulation for all active journeys."""
        completed_trucks = []
        
        for truck_id, state in self.active_journeys.items():
            route = state["route"]
            idx = state["current_index"]
            frac = state["fraction"]
            
            if idx >= len(route) - 1:
                completed_trucks.append(truck_id)
                continue
                
            # Advance fraction (equivalent to the frontend's smooth movement)
            frac += 0.2  # 20% along the segment per second
            if frac >= 1.0:
                frac = 0.0
                idx += 1
                if idx >= len(route) - 1:
                    completed_trucks.append(truck_id)
                    continue
                    
            state["current_index"] = idx
            state["fraction"] = frac
            
            # Interpolate coords
            p1 = route[idx]
            p2 = route[idx + 1]
            curr_lng = p1[0] + (p2[0] - p1[0]) * frac
            curr_lat = p1[1] + (p2[1] - p1[1]) * frac
            
            # Simulated telemetry values
            speed = 65 + random.randint(0, 5)
            rpm = 1200 + random.randint(0, 100)
            fuel = 100 - (idx / max(1, len(route))) * 10
            
            # Update the global dict
            if truck_id not in self.fleet_telemetry:
                self.fleet_telemetry[truck_id] = {}
                
            self.fleet_telemetry[truck_id].update({
                "lat": curr_lat,
                "lng": curr_lng,
                "speed": speed,
                "rpm": rpm,
                "fuel": fuel
            })

        for t in completed_trucks:
            self.end_journey(t)
