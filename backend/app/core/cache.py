import hashlib
import json
import threading
import time
from typing import Any, Optional
from datetime import datetime, timedelta, timezone
from sqlalchemy.orm import Session
from app.config import settings
from app.models.performance import CacheMetadata

try:
    import redis as redis_lib
except ImportError:
    redis_lib = None


class SmartCacheManager:
    """
    Layer 3 Smart Cache & Materialized Result Cache.
    Supports Redis (when REDIS_URL is configured) + thread-safe In-Memory LRU fallback.
    Cache keys bind (tenant_id, dashboard_id, widget_id, dataset_id, data_version,
    query_hash, filter_hash, permission_context_hash) to guarantee zero cross-user leakage.
    """

    def __init__(self, max_items: int = 2000):
        self._lock = threading.RLock()
        self._store: dict[str, dict[str, Any]] = {}
        self._max_items = max_items
        self._hits = 0
        self._misses = 0
        self._invalidations = 0
        self._redis = None
        if settings.REDIS_URL and redis_lib is not None:
            try:
                client = redis_lib.Redis.from_url(settings.REDIS_URL, decode_responses=True)
                client.ping()
                self._redis = client
            except Exception:
                self._redis = None

    @staticmethod
    def hash_dict(payload: Any) -> str:
        raw = json.dumps(payload, sort_keys=True, default=str)
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]

    @staticmethod
    def build_key(
        organization_id: str,
        dataset_id: str,
        data_version: int,
        query_hash: str,
        filter_hash: str,
        permission_hash: str,
        dashboard_id: Optional[str] = None,
        widget_id: Optional[str] = None,
    ) -> str:
        parts = [
            f"org:{organization_id}",
            f"dash:{dashboard_id or '-'}",
            f"w:{widget_id or '-'}",
            f"ds:{dataset_id}",
            f"v:{data_version}",
            f"q:{query_hash}",
            f"f:{filter_hash}",
            f"p:{permission_hash}",
        ]
        return "|".join(parts)

    def get(self, key: str, db: Optional[Session] = None) -> Optional[Any]:
        if self._redis is not None:
            try:
                val = self._redis.get(key)
                if val is not None:
                    with self._lock:
                        self._hits += 1
                    return json.loads(val)
            except Exception:
                pass

        now = time.time()
        with self._lock:
            entry = self._store.get(key)
            if entry is not None:
                if entry["expires_at"] > now:
                    self._hits += 1
                    entry["hits"] += 1
                    if db is not None:
                        meta = db.query(CacheMetadata).filter(CacheMetadata.cache_key == key).first()
                        if meta:
                            meta.hit_count += 1
                    return entry["value"]
                else:
                    del self._store[key]
            self._misses += 1
            return None

    def set(
        self,
        key: str,
        value: Any,
        ttl_seconds: Optional[int] = None,
        meta_info: Optional[dict] = None,
        db: Optional[Session] = None,
    ) -> None:
        ttl = ttl_seconds or settings.CACHE_TTL_SECONDS
        if self._redis is not None:
            try:
                self._redis.setex(key, ttl, json.dumps(value, default=str))
            except Exception:
                pass

        with self._lock:
            if len(self._store) >= self._max_items:
                oldest_key = min(self._store, key=lambda k: self._store[k]["created_at"])
                del self._store[oldest_key]
            self._store[key] = {
                "value": value,
                "expires_at": time.time() + ttl,
                "created_at": time.time(),
                "hits": 0,
                "meta": meta_info or {},
            }

        if db is not None and meta_info:
            try:
                existing = db.query(CacheMetadata).filter(CacheMetadata.cache_key == key).first()
                exp_dt = datetime.now(timezone.utc) + timedelta(seconds=ttl)
                if existing:
                    existing.expires_at = exp_dt
                    existing.data_version = meta_info.get("data_version", 1)
                else:
                    db.add(
                        CacheMetadata(
                            cache_key=key,
                            organization_id=meta_info.get("organization_id", "default"),
                            dashboard_id=meta_info.get("dashboard_id"),
                            widget_id=meta_info.get("widget_id"),
                            dataset_id=meta_info.get("dataset_id"),
                            data_version=meta_info.get("data_version", 1),
                            permission_hash=meta_info.get("permission_hash", ""),
                            filter_hash=meta_info.get("filter_hash", ""),
                            expires_at=exp_dt,
                        )
                    )
                db.commit()
            except Exception:
                db.rollback()

    def invalidate_prefix(self, contains_token: str) -> int:
        removed = 0
        if self._redis is not None:
            try:
                for k in self._redis.scan_iter(f"*{contains_token}*"):
                    self._redis.delete(k)
                    removed += 1
            except Exception:
                pass

        with self._lock:
            keys_to_del = [k for k in self._store if contains_token in k]
            for k in keys_to_del:
                del self._store[k]
                removed += 1
            self._invalidations += removed
        return removed

    def invalidate_dataset(self, dataset_id: str) -> int:
        return self.invalidate_prefix(f"ds:{dataset_id}|")

    def invalidate_dashboard(self, dashboard_id: str) -> int:
        return self.invalidate_prefix(f"dash:{dashboard_id}|")

    def invalidate_all(self) -> int:
        if self._redis is not None:
            try:
                self._redis.flushdb()
            except Exception:
                pass
        with self._lock:
            count = len(self._store)
            self._store.clear()
            self._invalidations += count
            return count

    def stats(self) -> dict:
        with self._lock:
            total = self._hits + self._misses
            hit_ratio = round((self._hits / total) * 100, 2) if total > 0 else 0.0
            return {
                "backend": "redis+memory" if self._redis else "memory",
                "entries_count": len(self._store),
                "hits": self._hits,
                "misses": self._misses,
                "hit_ratio_percent": hit_ratio,
                "invalidations": self._invalidations,
            }


cache_manager = SmartCacheManager()
