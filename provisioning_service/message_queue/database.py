import asyncpg


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