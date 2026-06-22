# ═══════════════════════════════════════════
#  AEROFLEET V2 — EVENT MANAGER
#  Video clipping, local storage, cloud upload
# ═══════════════════════════════════════════
import cv2
import os
import time
import json
import threading
import shutil


class EventManager:
    """
    Handles alert events:
      1. Dumps the 60s rolling buffer to a local .mp4 file
      2. Trims a 30s clip from the middle of the buffer
      3. Attempts cloud upload (Firebase Storage)
      4. On failure, stores locally for later sync
    """

    def __init__(self, fps=5, local_storage_dir="local_storage",
                 pending_uploads_dir="local_storage/pending_uploads"):
        self.fps = fps
        self.local_storage_dir = local_storage_dir
        self.pending_uploads_dir = pending_uploads_dir
        self.alerts_dir = os.path.join(local_storage_dir, "alerts")
        self._upload_queue = []
        self._is_online = True
        self._camera = None  # Injected via set_camera() to avoid circular imports

        os.makedirs(self.alerts_dir, exist_ok=True)
        os.makedirs(self.pending_uploads_dir, exist_ok=True)

        # Start background sync thread
        self._sync_thread = threading.Thread(target=self._sync_loop, daemon=True)
        self._sync_thread.start()

    def set_camera(self, camera):
        """Inject camera reference to avoid circular imports with main.py."""
        self._camera = camera

    def trigger_alert(self, event_type, buffer_frames, truck_id="TRK-001",
                      driver_id=None, driver_name=None):
        """
        Handle an alert event asynchronously to capture future frames.
        Returns dict with expected clip paths and metadata.
        """
        timestamp_str = time.strftime("%Y%m%d_%H%M%S")
        event_id = f"{truck_id}_{event_type}_{timestamp_str}"
        event_timestamp = time.time()

        full_path = os.path.join(self.alerts_dir, f"{event_id}_60s.mp4")
        short_path = os.path.join(self.alerts_dir, f"{event_id}_20s.mp4")

        result = {
            "event_id": event_id,
            "event_type": event_type,
            "truck_id": truck_id,
            "driver_id": driver_id,
            "driver_name": driver_name,
            "timestamp": event_timestamp,
            "timestamp_str": timestamp_str,
            "full_clip_path": full_path,
            "short_clip_path": short_path,
            "uploaded": False,
        }

        # Save metadata immediately
        meta_path = os.path.join(self.alerts_dir, f"{event_id}_meta.json")
        with open(meta_path, "w") as f:
            json.dump(result, f, indent=2, default=str)

        # Spawn background thread to wait for "after" frames
        threading.Thread(target=self._generate_clips_async, args=(result,), daemon=True).start()

        return result

    def _generate_clips_async(self, metadata):
        """Wait for 30 seconds to capture the 'after' frames, then trim 20s and 60s clips."""
        event_time = metadata["timestamp"]
        event_id = metadata["event_id"]
        
        # Wait until 30 seconds have passed since the event
        time_to_wait = 30.0 - (time.time() - event_time)
        if time_to_wait > 0:
            time.sleep(time_to_wait)

        # Use injected camera reference instead of circular import from main
        if self._camera is None:
            print(f"[EventManager] No camera reference set for {metadata['event_type']}")
            return
        buffer_frames = self._camera.get_buffer_copy()
        
        if not buffer_frames:
            print(f"[EventManager] No frames in buffer for {metadata['event_type']}")
            return

        h, w, _ = buffer_frames[0][1].shape
        fourcc = cv2.VideoWriter_fourcc(*"mp4v")

        # Extract frames that fall roughly in [event_time - 30, event_time + 30] for 60s
        # and [event_time - 10, event_time + 10] for 20s
        frames_60s = []
        frames_20s = []
        
        for ts, frame in buffer_frames:
            if event_time - 30 <= ts <= event_time + 30:
                frames_60s.append(frame)
            if event_time - 10 <= ts <= event_time + 10:
                frames_20s.append(frame)

        # ── Save 60s clip ──
        if frames_60s:
            writer = cv2.VideoWriter(metadata["full_clip_path"], fourcc, self.fps, (w, h))
            for frame in frames_60s: writer.write(frame)
            writer.release()

        # ── Save 20s clip ──
        if frames_20s:
            writer = cv2.VideoWriter(metadata["short_clip_path"], fourcc, self.fps, (w, h))
            for frame in frames_20s: writer.write(frame)
            writer.release()

        print(f"[EventManager] Saved delayed clips: 60s→{metadata['full_clip_path']}, 20s→{metadata['short_clip_path']}")

        # ── Attempt cloud upload ──
        uploaded = self._upload_to_cloud(metadata["full_clip_path"], event_id, "60s")
        if uploaded:
            metadata["uploaded"] = True
            meta_path = os.path.join(self.alerts_dir, f"{event_id}_meta.json")
            with open(meta_path, "w") as f: json.dump(metadata, f, indent=2, default=str)
        else:
            self._queue_pending_upload(metadata["full_clip_path"], metadata["short_clip_path"], metadata)

        self._upload_to_cloud(metadata["short_clip_path"], event_id, "20s")

    def _upload_to_cloud(self, filepath, event_id, clip_type):
        """
        Upload to Firebase Storage (or any cloud).
        Returns True on success, False on failure.
        """
        try:
            # Check if firebase_admin is available
            import firebase_admin
            from firebase_admin import credentials, storage

            # Initialize if not already
            if not firebase_admin._apps:
                cred_path = os.getenv("FIREBASE_CREDENTIALS", "")
                if cred_path and os.path.exists(cred_path):
                    cred = credentials.Certificate(cred_path)
                    bucket_name = os.getenv("FIREBASE_BUCKET", "aerofleet-v2.appspot.com")
                    firebase_admin.initialize_app(cred, {"storageBucket": bucket_name})
                else:
                    print("[EventManager] Firebase credentials not configured, storing locally")
                    return False

            bucket = storage.bucket()
            blob_name = f"alerts/{event_id}/{clip_type}/{os.path.basename(filepath)}"
            blob = bucket.blob(blob_name)
            blob.upload_from_filename(filepath)
            blob.make_public()
            print(f"[EventManager] Uploaded to cloud: {blob.public_url}")
            self._is_online = True
            return True

        except ImportError:
            print("[EventManager] firebase_admin not installed — storing locally")
            return False
        except Exception as e:
            print(f"[EventManager] Cloud upload failed: {e} — storing locally")
            self._is_online = False
            return False

    def _queue_pending_upload(self, full_path, short_path, metadata):
        """Save files and metadata to pending_uploads for offline sync."""
        event_id = metadata["event_id"]
        pending_dir = os.path.join(self.pending_uploads_dir, event_id)
        os.makedirs(pending_dir, exist_ok=True)

        # Copy clips to pending dir
        if full_path and os.path.exists(full_path):
            shutil.copy2(full_path, os.path.join(pending_dir, os.path.basename(full_path)))
        if short_path and os.path.exists(short_path):
            shutil.copy2(short_path, os.path.join(pending_dir, os.path.basename(short_path)))

        # Save metadata
        meta_file = os.path.join(pending_dir, "metadata.json")
        with open(meta_file, "w") as f:
            json.dump(metadata, f, indent=2, default=str)

        print(f"[EventManager] Queued for offline sync: {event_id}")

    def _sync_loop(self):
        """Background thread: periodically tries to upload pending files."""
        while True:
            time.sleep(60)  # Check every 60 seconds
            self._sync_pending()

    def _sync_pending(self):
        """Try to upload all pending files."""
        if not os.path.exists(self.pending_uploads_dir):
            return

        pending_dirs = [
            d for d in os.listdir(self.pending_uploads_dir)
            if os.path.isdir(os.path.join(self.pending_uploads_dir, d))
        ]

        for event_dir_name in pending_dirs:
            event_dir = os.path.join(self.pending_uploads_dir, event_dir_name)
            meta_file = os.path.join(event_dir, "metadata.json")

            if not os.path.exists(meta_file):
                continue

            with open(meta_file, "r") as f:
                metadata = json.load(f)

            event_id = metadata.get("event_id", event_dir_name)
            success = True

            # Try uploading all mp4 files in this pending dir
            for fname in os.listdir(event_dir):
                if fname.endswith(".mp4"):
                    fpath = os.path.join(event_dir, fname)
                    clip_type = "60s" if "60s" in fname else "30s"
                    if not self._upload_to_cloud(fpath, event_id, clip_type):
                        success = False
                        break

            if success:
                # Clean up pending dir
                shutil.rmtree(event_dir, ignore_errors=True)
                print(f"[EventManager] Synced pending: {event_id}")

    def get_pending_count(self):
        """Return number of pending uploads."""
        if not os.path.exists(self.pending_uploads_dir):
            return 0
        return len([
            d for d in os.listdir(self.pending_uploads_dir)
            if os.path.isdir(os.path.join(self.pending_uploads_dir, d))
        ])

    def is_online(self):
        return self._is_online
