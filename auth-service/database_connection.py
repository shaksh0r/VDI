import os

import asyncpg
from fastapi import Request


async def create_database_pool(prefix: str = "DB"):
    pool = await asyncpg.create_pool(
        user=os.getenv(f"{prefix}_USER", "myuser"),
        password=os.getenv(f"{prefix}_PASSWORD", "mypassword"),
        database=os.getenv(f"{prefix}_NAME", "mydatabase"),
        host=os.getenv(f"{prefix}_HOST", "localhost"),
        port=int(os.getenv(f"{prefix}_PORT", "5432")),
        min_size=int(os.getenv(f"{prefix}_POOL_MIN", "5")),
        max_size=int(os.getenv(f"{prefix}_POOL_MAX", "20")),
    )

    return pool


async def get_db(request: Request):
    async with request.app.state.db_pool.acquire() as conn:
        yield conn
