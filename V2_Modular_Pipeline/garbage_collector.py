import os
import time
import asyncio
import logging
from config import LOCAL_STORAGE_DIR, PENDING_UPLOADS_DIR, MAX_STORAGE_MB, MAX_STORAGE_DAYS, GC_INTERVAL_SECONDS
from database import SessionLocal, Alert

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("GarbageCollector")

class GarbageCollector:
    def __init__(self):
        self.running = False
        self._task = None
        self.dirs_to_watch = [
            os.path.join(LOCAL_STORAGE_DIR, "alerts"),
            PENDING_UPLOADS_DIR
        ]

    def start(self):
        self.running = True
        self._task = asyncio.create_task(self._loop())
        logger.info("Garbage Collector started.")

    def stop(self):
        self.running = False
        if self._task:
            self._task.cancel()
        logger.info("Garbage Collector stopped.")

    async def _loop(self):
        while self.running:
            try:
                self._run_gc()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error in GC loop: {e}")
            await asyncio.sleep(GC_INTERVAL_SECONDS)

    def _run_gc(self):
        # 1. Gather all files
        all_files = []
        for d in self.dirs_to_watch:
            if not os.path.exists(d):
                continue
            for f in os.listdir(d):
                full_path = os.path.join(d, f)
                if os.path.isfile(full_path):
                    stat = os.stat(full_path)
                    all_files.append({
                        "path": full_path,
                        "size": stat.st_size,
                        "mtime": stat.st_mtime
                    })

        # 2. Time-based eviction
        now = time.time()
        max_age_seconds = MAX_STORAGE_DAYS * 24 * 3600
        
        files_to_delete = []
        kept_files = []
        
        for f in all_files:
            if (now - f["mtime"]) > max_age_seconds:
                files_to_delete.append(f)
            else:
                kept_files.append(f)

        # 3. Size-based eviction
        total_size_bytes = sum(f["size"] for f in kept_files)
        max_size_bytes = MAX_STORAGE_MB * 1024 * 1024
        
        if total_size_bytes > max_size_bytes:
            # Sort remaining files by oldest first
            kept_files.sort(key=lambda x: x["mtime"])
            
            for f in kept_files:
                files_to_delete.append(f)
                total_size_bytes -= f["size"]
                if total_size_bytes <= max_size_bytes:
                    break

        # 4. Perform deletions
        if not files_to_delete:
            return

        db = SessionLocal()
        try:
            for f in files_to_delete:
                file_path = f["path"]
                try:
                    os.remove(file_path)
                    logger.info(f"Deleted old file to reclaim space: {file_path}")
                    
                    # Update database if it's an alert clip
                    filename = os.path.basename(file_path)
                    alerts = db.query(Alert).filter(Alert.clip_path.like(f"%{filename}%")).all()
                    for a in alerts:
                        a.clip_path = None
                        
                except Exception as e:
                    logger.error(f"Failed to delete {file_path}: {e}")
            
            db.commit()
        except Exception as e:
            logger.error(f"DB Error during GC: {e}")
        finally:
            db.close()
