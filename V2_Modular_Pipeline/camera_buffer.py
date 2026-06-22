# ═══════════════════════════════════════════
#  AEROFLEET V2 — CAMERA BUFFER
#  Thread-safe rolling buffer at 1-5 FPS
# ═══════════════════════════════════════════
import cv2
import time
import threading
from collections import deque


class CameraBuffer:
    """
    Captures video from a camera source at a target FPS,
    maintaining a rolling deque of (timestamp, frame) tuples.
    At 5 FPS x 60s = 300 frames in memory (~55 MB at 640x480 BGR).
    """

    def __init__(self, camera_index=0, target_fps=5, buffer_seconds=60,
                 width=640, height=480):
        self.camera_index = camera_index
        self.target_fps = target_fps
        self.frame_delay = 1.0 / target_fps
        self.buffer_size = target_fps * buffer_seconds
        self.width = width
        self.height = height

        self.frame_buffer = deque(maxlen=self.buffer_size)
        self.running = False
        self.cap = None
        self._thread = None
        self._lock = threading.Lock()
        self._frame_count = 0
        self._start_time = 0
        self._last_health_check = 0
        self.paused = False

    def start(self):
        """Open camera and begin capture thread."""
        self.cap = cv2.VideoCapture(self.camera_index)
        self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, self.width)
        self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self.height)
        # Try to reduce camera buffer to minimize latency
        self.cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)

        if not self.cap.isOpened():
            raise RuntimeError(f"Cannot open camera index {self.camera_index}")

        self.running = True
        self._start_time = time.time()
        self._thread = threading.Thread(target=self._capture_loop, daemon=True)
        self._thread.start()
        print(f"[CameraBuffer] Started — {self.width}x{self.height} @ {self.target_fps} FPS, "
              f"buffer={self.buffer_size} frames ({self.buffer_size // self.target_fps}s)")

    def _capture_loop(self):
        """Main capture loop running in background thread."""
        while self.running:
            loop_start = time.time()

            ret, frame = self.cap.read()
            if ret:
                # Resize if camera doesn't respect the set resolution
                h, w = frame.shape[:2]
                if w != self.width or h != self.height:
                    frame = cv2.resize(frame, (self.width, self.height))

                timestamp = time.time()
                with self._lock:
                    self.frame_buffer.append((timestamp, frame))
                    self._frame_count += 1
            else:
                # Camera read failed — attempt reconnection
                print("[CameraBuffer] Frame read failed, attempting reconnect...")
                self._reconnect()

            elapsed = time.time() - loop_start
            sleep_time = max(0, self.frame_delay - elapsed)
            if sleep_time > 0:
                time.sleep(sleep_time)

    def _reconnect(self):
        """Try to reopen the camera."""
        if self.cap:
            self.cap.release()
        time.sleep(1)
        self.cap = cv2.VideoCapture(self.camera_index)
        self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, self.width)
        self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self.height)
        self.cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)

    def get_latest_frame(self):
        """Return (timestamp, frame) of the most recent frame, or (None, None)."""
        with self._lock:
            if self.frame_buffer:
                return self.frame_buffer[-1]
        return None, None

    def get_buffer_copy(self):
        """Return a snapshot of the entire rolling buffer as a list."""
        with self._lock:
            return list(self.frame_buffer)

    def get_stats(self):
        """Return health/stats dict for the watchdog."""
        uptime = time.time() - self._start_time if self._start_time else 0
        actual_fps = self._frame_count / uptime if uptime > 0 else 0
        return {
            "running": self.running,
            "frame_count": self._frame_count,
            "buffer_length": len(self.frame_buffer),
            "buffer_capacity": self.buffer_size,
            "actual_fps": round(actual_fps, 2),
            "uptime_s": round(uptime, 1),
        }

    def is_healthy(self):
        """Quick health check: have we received a frame in the last 5 seconds?"""
        if self.paused:
            return True

        with self._lock:
            if not self.frame_buffer:
                return False
            last_ts = self.frame_buffer[-1][0]
            return (time.time() - last_ts) < 5.0

    def stop(self):
        """Gracefully stop the capture thread."""
        self.running = False
        if self._thread:
            self._thread.join(timeout=3)
        if self.cap:
            self.cap.release()
        print("[CameraBuffer] Stopped.")

    def pause(self):
        self.paused = True
        self.stop()
        print("[CameraBuffer] Paused by override.")

    def resume(self):
        self.paused = False
        self.start()
        print("[CameraBuffer] Resumed by override.")

    def flush_to_disk(self, filename="shutdown_buffer.mp4"):
        """Write the current buffer to disk (e.g., during emergency shutdown)."""
        with self._lock:
            frames = list(self.frame_buffer)
        if not frames: return
        import os
        import config
        filepath = os.path.join(config.LOCAL_STORAGE_DIR, filename)
        
        fourcc = cv2.VideoWriter_fourcc(*'mp4v')
        out = cv2.VideoWriter(filepath, fourcc, self.target_fps, (self.width, self.height))
        for ts, frame in frames:
            out.write(frame)
        out.release()
        print(f"[CameraBuffer] Flushed {len(frames)} frames to {filepath}")
