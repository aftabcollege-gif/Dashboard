import threading
import time
from collections import defaultdict, deque
from fastapi import HTTPException, status
from app.config import settings
from app.core.cache import cache_manager

_REDIS_SLIDING_WINDOW_LUA = """
local current = redis.call('INCR', KEYS[1])
if current == 1 then
    redis.call('EXPIRE', KEYS[1], tonumber(ARGV[1]))
end
local ttl = redis.call('TTL', KEYS[1])
if ttl < 0 then
    redis.call('EXPIRE', KEYS[1], tonumber(ARGV[1]))
    ttl = tonumber(ARGV[1])
end
return {current, ttl}
"""


class RateLimiter:
    """
    Atomic Redis Lua rate limiter when Redis is configured, with thread-safe in-memory
    fallback in non-production and fail-closed enforcement in production if configured Redis fails.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._buckets: dict[str, deque[float]] = defaultdict(deque)

    def check(self, key: str, limit: int, window_seconds: int = 60) -> None:
        if not settings.RATE_LIMIT_ENABLED or limit <= 0:
            return

        now = time.time()
        redis_configured = bool(settings.REDIS_URL)
        redis_client = getattr(cache_manager, "_redis", None)

        if redis_client is not None:
            try:
                rkey = f"rl:{key}"
                res = redis_client.eval(_REDIS_SLIDING_WINDOW_LUA, 1, rkey, int(window_seconds))
                count = int(res[0])
                ttl = int(res[1])
                if count > limit:
                    raise HTTPException(
                        status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                        detail="Rate limit exceeded. Please wait and try again.",
                        headers={"Retry-After": str(max(1, ttl or window_seconds))},
                    )
                return
            except HTTPException:
                raise
            except Exception as exc:
                if settings.ENVIRONMENT == "production" and settings.RATE_LIMIT_FAIL_CLOSED_PROD:
                    raise HTTPException(
                        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                        detail="Rate limiting service temporarily unavailable",
                    ) from exc
        elif redis_configured and settings.ENVIRONMENT == "production" and settings.RATE_LIMIT_FAIL_CLOSED_PROD:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Rate limiting backend unavailable in production",
            )

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
