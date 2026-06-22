# ═══════════════════════════════════════════
#  AEROFLEET V2 — WATCHDOG SYSTEM
#  Monitors all pipeline components and restarts on failure
# ═══════════════════════════════════════════
import time
import threading
import os
import json


class Watchdog:
    """
    Monitors the health of all pipeline components.
    If any component is unhealthy for more than `max_failures` consecutive checks,
    the watchdog invokes the component's restart callback.

    Components register themselves with:
      watchdog.register("camera", health_check_fn, restart_fn)
    """

    def __init__(self, check_interval=5, max_failures=3, log_dir="local_storage"):
        self.check_interval = check_interval
        self.max_failures = max_failures
        self.log_dir = log_dir
        self._components = {}
        self._failure_counts = {}
        self._restart_counts = {}
        self._running = False
        self._thread = None

        os.makedirs(log_dir, exist_ok=True)

    def register(self, name, health_check_fn, restart_fn):
        """
        Register a component to monitor.
        health_check_fn: callable() -> bool (True = healthy)
        restart_fn: callable() -> None (restart the component)
        """
        self._components[name] = {
            "health_check": health_check_fn,
            "restart": restart_fn,
        }
        self._failure_counts[name] = 0
        self._restart_counts[name] = 0
        print(f"[Watchdog] Registered component: {name}")

    def start(self):
        """Start the watchdog monitoring loop."""
        self._running = True
        self._thread = threading.Thread(target=self._monitor_loop, daemon=True)
        self._thread.start()
        print(f"[Watchdog] Started — interval={self.check_interval}s, "
              f"max_failures={self.max_failures}")

    def _monitor_loop(self):
        while self._running:
            time.sleep(self.check_interval)
            self._check_all()

    def _check_all(self):
        for name, comp in self._components.items():
            try:
                is_healthy = comp["health_check"]()
            except Exception as e:
                is_healthy = False
                print(f"[Watchdog] Health check error for {name}: {e}")

            if is_healthy:
                if self._failure_counts[name] > 0:
                    print(f"[Watchdog] {name} recovered after {self._failure_counts[name]} failures")
                self._failure_counts[name] = 0
            else:
                self._failure_counts[name] += 1
                print(f"[Watchdog] {name} unhealthy "
                      f"({self._failure_counts[name]}/{self.max_failures})")

                if self._failure_counts[name] >= self.max_failures:
                    print(f"[Watchdog] RESTARTING {name}...")
                    try:
                        comp["restart"]()
                        self._restart_counts[name] += 1
                        self._failure_counts[name] = 0
                        self._log_event(name, "RESTART")
                    except Exception as e:
                        print(f"[Watchdog] Failed to restart {name}: {e}")
                        self._log_event(name, "RESTART_FAILED", str(e))

    def _log_event(self, component, event_type, detail=""):
        """Log watchdog events to disk."""
        log_file = os.path.join(self.log_dir, "watchdog.log")
        entry = {
            "time": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "component": component,
            "event": event_type,
            "detail": detail,
            "restart_count": self._restart_counts.get(component, 0),
        }
        with open(log_file, "a") as f:
            f.write(json.dumps(entry) + "\n")

    def get_status(self):
        """Return health status of all components."""
        status = {}
        for name in self._components:
            try:
                is_healthy = self._components[name]["health_check"]()
            except Exception:
                is_healthy = False

            status[name] = {
                "healthy": is_healthy,
                "failure_count": self._failure_counts.get(name, 0),
                "restart_count": self._restart_counts.get(name, 0),
            }
        return status

    def stop(self):
        self._running = False
        if self._thread:
            self._thread.join(timeout=3)
        print("[Watchdog] Stopped.")
