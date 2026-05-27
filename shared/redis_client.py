import os
import redis.asyncio as aioredis

_redis: aioredis.Redis | None = None


async def init_redis() -> aioredis.Redis:
    global _redis
    if _redis is not None:
        try:
            await _redis.ping()
            return _redis
        except Exception:
            _redis = None
    _redis = aioredis.Redis(
        host=os.getenv("REDIS_HOST", "127.0.0.1"),
        port=int(os.getenv("REDIS_PORT", 6379)),
        db=int(os.getenv("REDIS_DB", 0)),
        decode_responses=True,
        health_check_interval=30,
    )
    await _redis.ping()
    return _redis


async def close_redis():
    global _redis
    if _redis is not None:
        try:
            await _redis.close()
        except Exception:
            pass
    _redis = None
