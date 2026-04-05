import asyncpg
from fastapi import Request

from provisioning_service.config_env import db_settings


async def create_database_pool():
    cfg = db_settings()
    pool = await asyncpg.create_pool(
        user=cfg["user"],
        password=cfg["password"],
        database=cfg["database"],
        host=cfg["host"],
        port=cfg["port"],
        min_size=cfg["min_size"],
        max_size=cfg["max_size"],
    )

    return pool


async def get_db(request:Request):
    async with request.app.state.db_pool.acquire() as conn:
        yield conn
