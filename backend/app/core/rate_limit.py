import threading
import time
import uuid
from collections import defaultdict, deque
from typing import Iterable, Optional
from fastapi import HTTPException, status
from app.config import settings
from app.core.cache import cache_manager

# Atomic ZSET sliding-window rate limiter:
# - Eliminates fixed-window boundary bursts across window transitions.
# - Sets strict PEXPIRE on every call so keys always expire and Redis memory stays bounded.
_REDIS_SLIDING_WINDOW_LUA = """
local now_ms = tonumber(ARGV[1])
local window_ms = tonumber(ARGV[2])
local limit = tonumber(ARGV[3])
local member = ARGV[4]
local cutoff = now_ms - window_ms

redis.call('ZREMRANGEBYSCORE', KEYS[1], '-inf', cutoff)
local current = tonumber(redis.call('ZCARD', KEYS[1]))
if current >= limit then
    local oldest = redis.call('ZRANGE', KEYS[1], 0, 0, 'WITHSCORES')
    local retry_ms = window_ms
    if oldest and oldest[2] then
        retry_ms = math.max(1000, tonumber(oldest[2]) + window_ms - now_ms)
    end
    redis.call('PEXPIRE', KEYS[1], window_ms)
    return {0, current, math.ceil(retry_ms / 1000)}
end

redis.call('ZADD', KEYS[1], now_ms, member)
redis.call('PEXPIRE', KEYS[1], window_ms)
return {1, current + 1, 0}
"""

_MAX_MEMORY_BUCKETS = 50000


class RateLimiter:
    """
    Atomic Redis Lua ZSET sliding-window rate limiter with:
    - Elimination of fixed-window boundary bursts
    - Bounded/expiring keys in both Redis and in-memory fallback
    - Independent per-organization, per-user, per-IP, and per-endpoint enforcement
    - Fail-closed behavior in production when Redis is unavailable or misconfigured
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._buckets: dict[str, deque[float]] = defaultdict(deque)
        self._bucket_expiry: dict[str, float] = {}

    def _prune_memory_buckets_locked(self, now: float) -> None:
        if len(self._buckets) <= _MAX_MEMORY_BUCKETS // 2:
            return
        expired = [k for k, exp in self._bucket_expiry.items() if exp <= now or not self._buckets.get(k)]
        for k in expired:
            self._buckets.pop(k, None)
            self._bucket_expiry.pop(k, None)
        while len(self._buckets) >= _MAX_MEMORY_BUCKETS:
            oldest_key = min(self._bucket_expiry, key=self._bucket_expiry.get, default=None)
            if not oldest_key:
                break
            self._buckets.pop(oldest_key, None)
            self._bucket_expiry.pop(oldest_key, None)

    def check(self, key: str, limit: int, window_seconds: int = 60, *, now_override: Optional[float] = None) -> None:
        if not settings.RATE_LIMIT_ENABLED or limit <= 0:
            return

        now = float(now_override) if now_override is not None else time.time()
        window_sec = max(1, int(window_seconds))
        window_ms = window_sec * 1000
        now_ms = int(now * 1000)

        redis_configured = bool(settings.REDIS_URL)
        redis_client = getattr(cache_manager, "_redis", None)
        is_prod = settings.ENVIRONMENT == "production"
        must_fail_closed = is_prod and settings.RATE_LIMIT_FAIL_CLOSED_PROD

        if redis_client is not None:
            try:
                rkey = f"rl:sw:{key}"
                member = f"{now_ms}:{uuid.uuid4().hex[:12]}"
                res = redis_client.eval(
                    _REDIS_SLIDING_WINDOW_LUA,
                    1,
                    rkey,
                    now_ms,
                    window_ms,
                    int(limit),
                    member,
                )
                if not isinstance(res, (list, tuple)) or len(res) < 3:
                    raise RuntimeError("Unexpected Redis rate-limiter response")
                allowed = int(res[0])
                retry_after = max(1, int(res[2] or window_sec))
                if allowed == 0:
                    raise HTTPException(
                        status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                        detail="Rate limit exceeded. Please wait and try again.",
                        headers={"Retry-After": str(retry_after)},
                    )
                return
            except HTTPException:
                raise
            except Exception as exc:
                if must_fail_closed:
                    raise HTTPException(
                        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                        detail="Rate limiting service temporarily unavailable",
                    ) from exc
        elif must_fail_closed and (redis_configured or not settings.is_desktop_mode()):
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Rate limiting backend unavailable or unconfigured in production",
            )

        cutoff = now - window_sec
        with self._lock:
            self._prune_memory_buckets_locked(now)
            dq = self._buckets[key]
            while dq and dq[0] <= cutoff:
                dq.popleft()
            if len(dq) >= limit:
                retry_after = max(1, int(window_sec - (now - dq[0])))
                raise HTTPException(
                    status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                    detail="Rate limit exceeded. Please wait and try again.",
                    headers={"Retry-After": str(retry_after)},
                )
            dq.append(now)
            self._bucket_expiry[key] = now + window_sec

    def check_dimensions(
        self,
        dimensions: Iterable[tuple[str, int, int]],
        *,
        now_override: Optional[float] = None,
    ) -> None:
        for dim_key, dim_limit, dim_window in dimensions:
            self.check(dim_key, limit=dim_limit, window_seconds=dim_window, now_override=now_override)

    def reset(self) -> None:
        with self._lock:
            self._buckets.clear()
            self._bucket_expiry.clear()


rate_limiter = RateLimiter()
