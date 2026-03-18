import asyncpg
from fastapi import Request


async def create_database_pool():
    pool = await asyncpg.create_pool(
        user="myuser",
        password="mypassword",
        database="mydatabase",
        host="localhost",
        min_size=10,
        max_size=20,
        port=5432
    )

    return pool


async def get_db(request:Request):
    async with request.app.state.db_pool.acquire() as conn:
        yield conn
