import threading
import time
from typing import Any, Dict, Optional


class LocalCache:
    def __init__(self, default_ttl: Optional[int] = None, cleanup_interval: int = 10):
        """
        :param default_ttl: default time-to-live in seconds (None = no expiry)
        :param cleanup_interval: background cleanup frequency in seconds
        """
        self.store: Dict[str, tuple[Any, Optional[float]]] = {}
        self.default_ttl = default_ttl
        self.cleanup_interval = cleanup_interval
        self._lock = threading.RLock()
        self._stop_event = threading.Event()

        self._cleaner_thread = threading.Thread(target=self._cleanup_loop, daemon=True)
        self._cleaner_thread.start()

    def set(self, key: str, value: Any, ttl: Optional[int] = None):
        """Set a value with optional TTL (seconds)."""
        expire_at = None

        if ttl is None:
            ttl = self.default_ttl

        if ttl is not None:
            expire_at = time.time() + ttl

        with self._lock:
            self.store[key] = (value, expire_at)

    def get(self, key: str, default: Any = None) -> Any:
        """Get value if exists and not expired."""
        with self._lock:
            item = self.store.get(key)
            if not item:
                return default

            value, expire_at = item

            if expire_at is not None and time.time() > expire_at:
                del self.store[key]
                return default

            return value

    def delete(self, key: str) -> bool:
        """Delete a key."""
        with self._lock:
            return self.store.pop(key, None) is not None

    def exists(self, key: str) -> bool:
        """Check existence without returning value."""
        return self.get(key, default=None) is not None

    def ttl(self, key: str) -> Optional[float]:
        """Return remaining TTL in seconds."""
        with self._lock:
            item = self.store.get(key)
            if not item:
                return None

            _, expire_at = item
            if expire_at is None:
                return None

            remaining = expire_at - time.time()
            return max(0, remaining)

    def clear(self):
        """Clear entire cache."""
        with self._lock:
            self.store.clear()

    def keys(self):
        """Return all non-expired keys."""
        self._purge_expired()
        with self._lock:
            return list(self.store.keys())

    def _purge_expired(self):
        now = time.time()
        with self._lock:
            expired_keys = [
                k for k, (_, exp) in self.store.items() if exp is not None and exp < now
            ]
            for k in expired_keys:
                del self.store[k]

    def _cleanup_loop(self):
        while not self._stop_event.is_set():
            time.sleep(self.cleanup_interval)
            self._purge_expired()

    def stop(self):
        """Stop background cleanup thread."""
        self._stop_event.set()
        self._cleaner_thread.join()


local_cache = LocalCache()
