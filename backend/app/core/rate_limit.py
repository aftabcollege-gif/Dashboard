import threading
import time
from collections import defaultdict, deque
from fastapi import HTTPException, status
from app.config import settings
from app.core.cache import cache_manager


class RateLimiter:
    """Sliding-window rate limiter backed by Redis when configured, else thread-safe in-memory."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._buckets: dict[str, deque[float]] = defaultdict(deque)

    def check(self, key: str, limit: int, window_seconds: int = 60) -> None:
        if not settings.RATE_LIMIT_ENABLED or limit <= 0:
            return

        now = time.time()
        redis_client = getattr(cache_manager, "_redis", None)
        if redis_client is not None:
            try:
                rkey = f"rl:{key}"
                pipe = redis_client.pipeline()
                pipe.incr(rkey)
                pipe.ttl(rkey)
                count, ttl = pipe.execute()
                if ttl is None or ttl < 0:
                    redis_client.expire(rkey, window_seconds)
                    ttl = window_seconds
                if int(count) > limit:
                    raise HTTPException(
                        status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                        detail="Rate limit exceeded. Please wait and try again.",
                        headers={"Retry-After": str(max(1, int(ttl or window_seconds)))},
                    )
                return
            except HTTPException:
                raise
            except Exception:
                pass

        cutoff = now - window_seconds
        with self._lock:
            dq = self._buckets[key]
            while dq and dq[0] <= cutoff:
                dq.popleft()
            if len(dq) >= limit:
                retry_after = max(1, int(window_seconds - (now - dq[0])))
                raise HTTPException(
                    status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                    detail="Rate limit exceeded. Please wait and try again.",
                    headers={"Retry-After": str(retry_after)},
                )
            dq.append(now)

    def reset(self) -> None:
        with self._lock:
            self._buckets.clear()


rate_limiter = RateLimiter()
