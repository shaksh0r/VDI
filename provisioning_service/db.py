import asyncpg
from fastapi import Request

from . import config


async def create_database_pool() -> asyncpg.Pool:
    pool = await asyncpg.create_pool(
        user=config.DB_USER,
        password=config.DB_PASSWORD,
        database=config.DB_NAME,
        host=config.DB_HOST,
        port=config.DB_PORT,
        min_size=config.DB_POOL_MIN,
        max_size=config.DB_POOL_MAX,
    )
    return pool


async def get_db(request: Request):
    async with request.app.state.db_pool.acquire() as conn:
        yield conn
