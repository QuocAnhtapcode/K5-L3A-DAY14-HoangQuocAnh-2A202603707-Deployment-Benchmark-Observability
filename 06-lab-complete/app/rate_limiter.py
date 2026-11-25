"""Redis sliding window shared across workers and app instances."""

from hashlib import sha256
from uuid import uuid4

from fastapi import HTTPException

from .config import settings
from .redis_client import r

# A score is the request time in milliseconds. Redis supplies the same clock
# to every worker and executes the quota check and write as one operation.
TAKE_SLOT = r.register_script("""
local clock = redis.call('TIME')
local now = clock[1] * 1000 + math.floor(clock[2] / 1000)
local window = 60000
local key = KEYS[1]
redis.call('ZREMRANGEBYSCORE', key, '-inf', now - window)
if redis.call('ZCARD', key) >= tonumber(ARGV[1]) then
    local oldest = redis.call('ZRANGE', key, 0, 0, 'WITHSCORES')
    return math.ceil((tonumber(oldest[2]) + window - now) / 1000)
end
redis.call('ZADD', key, now, ARGV[2])
redis.call('PEXPIRE', key, window)
return 0
""")


def check_rate_limit(key: str) -> None:
    fingerprint = sha256(key.encode()).hexdigest()
    retry_after = TAKE_SLOT(
        keys=[f"day14:rate:{fingerprint}"],
        args=[settings.rate_limit_per_minute, uuid4().hex],
    )
    if retry_after:
        raise HTTPException(
            status_code=429,
            detail="Rate limit exceeded",
            headers={"Retry-After": str(retry_after)},
        )
