import sys
import asyncio
import os

# FIX для Windows
if sys.platform == 'win32':
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

import asyncpg

_pool = None

async def init_pool() -> asyncpg.Pool:
    global _pool
    if _pool is not None and not _pool._closed:
        return _pool
    
    _pool = await asyncpg.create_pool(
        host=os.getenv("DB_HOST", "127.0.0.1"),
        port=int(os.getenv("DB_PORT", 5432)),
        user=os.getenv("DB_USER"),
        password=os.getenv("DB_PASS"),
        database=os.getenv("DB_NAME"),
        min_size=2,
        max_size=10,
        command_timeout=30,
        statement_cache_size=1024,
    )
    return _pool

async def close_pool():
    global _pool
    if _pool is not None and not _pool._closed:
        await _pool.close()
    _pool = None
