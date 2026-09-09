import asyncpg
from fastapi import Request

from . import config


async def create_database_pool(
    min_size: int | None = None, max_size: int | None = None
) -> asyncpg.Pool:
    pool = await asyncpg.create_pool(
        user=config.DB_USER,
        password=config.DB_PASSWORD,
        database=config.DB_NAME,
        host=config.DB_HOST,
        port=config.DB_PORT,
        min_size=min_size if min_size is not None else config.DB_POOL_MIN,
        max_size=max_size if max_size is not None else config.DB_POOL_MAX,
    )
    return pool


async def get_db(request: Request):
    async with request.app.state.db_pool.acquire() as conn:
        yield conn


async def get_pool(request: Request):
    """Yield the asyncpg pool itself (not a checked-out connection).

    Long operations (e.g. the claim wait loop, which polls for up to
    CLAIM_QUEUE_TIMEOUT_SECONDS) must not hold a pooled connection for
    their whole duration — that ties up pool slots and can exhaust the
    pool when many students wait concurrently. These should acquire a
    connection per attempt instead.
    """
    yield request.app.state.db_pool
