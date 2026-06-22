import cv2
import collections
import time
import threading
import os
import paho.mqtt.client as mqtt
import requests
import json

# Configuration
MQTT_HOST = "localhost"
MQTT_PORT = 1883
BUFFER_DURATION_SEC = 30
POST_TRIGGER_DURATION_SEC = 30
FPS = 10.0 # Standardize FPS
CAMERA_INDEX = 0

# Desktop path for buffer
DESKTOP_DIR = os.path.join(os.path.expanduser("~"), "OneDrive", "Desktop")
BUFFER_DIR = os.path.join(DESKTOP_DIR, "AEROFLEET_camera_buffer")
os.makedirs(BUFFER_DIR, exist_ok=True)

class CameraBuffer:
    def __init__(self):
        self.frame_buffer = collections.deque(maxlen=int(BUFFER_DURATION_SEC * FPS))
        self.trigger_event = threading.Event()
        self.post_frames = []
        self.triggered_truck_id = None
        self.is_running = True
        
        # MQTT Setup
        self.mqtt_client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, "camera_buffer")
        self.mqtt_client.on_connect = self.on_connect
        self.mqtt_client.on_message = self.on_message
        
    def on_connect(self, client, userdata, flags, reason_code, properties):
        print(f"Connected to MQTT broker at {MQTT_HOST}")
        client.subscribe("truck/+/emergency/trigger")
        
    def on_message(self, client, userdata, msg):
        topic = msg.topic
        print(f"Emergency trigger received on topic: {topic}")
        truck_id = topic.split("/")[1]
        
        # Trigger recording
        if not self.trigger_event.is_set():
            self.triggered_truck_id = truck_id
            self.trigger_event.set()

    def capture_loop(self):
        cap = cv2.VideoCapture(CAMERA_INDEX)
        
        if not cap.isOpened():
            print(f"Error: Could not open camera at index {CAMERA_INDEX}.")
            return
            
        print("Camera capture started. Buffering...")
        
        frame_time = 1.0 / FPS
        
        while self.is_running:
            start_t = time.time()
            ret, frame = cap.read()
            
            if not ret:
                print("Failed to grab frame.")
                time.sleep(0.1)
                continue
                
            if self.trigger_event.is_set():
                # Post-trigger recording phase
                self.post_frames.append(frame)
                if len(self.post_frames) >= int(POST_TRIGGER_DURATION_SEC * FPS):
                    # We have 30s post trigger. Stop capturing and compile video.
                    self.compile_and_upload()
                    # Reset state
                    self.trigger_event.clear()
                    self.post_frames = []
                    self.triggered_truck_id = None
            else:
                # Normal buffering phase
                self.frame_buffer.append(frame)
                
            elapsed = time.time() - start_t
            sleep_t = frame_time - elapsed
            if sleep_t > 0:
                time.sleep(sleep_t)
                
        cap.release()

    def compile_and_upload(self):
        print(f"Compiling emergency video for {self.triggered_truck_id}...")
        
        # Combine pre and post frames
        all_frames = list(self.frame_buffer) + self.post_frames
        if not all_frames:
            print("No frames to compile.")
            return
            
        height, width, layers = all_frames[0].shape
        size = (width, height)
        
        filename = f"emergency_local_{self.triggered_truck_id}_{int(time.time())}.webm"
        filepath = os.path.join(BUFFER_DIR, filename)
        
        out = cv2.VideoWriter(filepath, cv2.VideoWriter_fourcc(*'vp80'), FPS, size)
        for frame in all_frames:
            out.write(frame)
        out.release()
        
        print(f"Video saved to {filepath}. Uploading...")
        
        # Upload via HTTP POST to FastAPI backend
        try:
            url = f"http://localhost:8000/api/alerts/upload-video/{self.triggered_truck_id}"
            with open(filepath, 'rb') as f:
                files = {'file': (filename, f, 'video/mp4')}
                response = requests.post(url, files=files)
                if response.status_code == 200:
                    print("Upload successful:", response.json())
                else:
                    print(f"Upload failed: {response.status_code} {response.text}")
        except Exception as e:
            print(f"Failed to upload video: {e}")

    def start(self):
        self.mqtt_client.connect(MQTT_HOST, MQTT_PORT, 60)
        self.mqtt_client.loop_start()
        
        capture_thread = threading.Thread(target=self.capture_loop)
        capture_thread.start()
        
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            self.is_running = False
            capture_thread.join()
            self.mqtt_client.loop_stop()

if __name__ == "__main__":
    app = CameraBuffer()
    app.start()
