"""Redis-based per-recipient rate limiter shared by API and worker."""
from datetime import datetime
from typing import Optional

import redis.asyncio as redis

from ..core.config import settings


class RateLimiter:
    def __init__(self, limit_per_hour: Optional[int] = None):
        self.limit = limit_per_hour or settings.NOTIFICATION_RATE_LIMIT_PER_HOUR
        self._client: Optional[redis.Redis] = None

    async def _get_client(self) -> redis.Redis:
        if self._client is None:
            self._client = redis.from_url(settings.REDIS_URL, decode_responses=True)
        return self._client

    async def allow(self, channel: str, recipient: str) -> bool:
        """Return True if the send may proceed. Uses per-hour rolling window."""
        client = await self._get_client()
        bucket = datetime.utcnow().strftime("%Y%m%d%H")
        key = f"notif:rl:{channel}:{recipient}:{bucket}"
        count = await client.incr(key)
        if count == 1:
            await client.expire(key, 3600)
        return count <= self.limit


rate_limiter = RateLimiter()
