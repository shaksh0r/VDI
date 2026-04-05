import httpx


async def scoped_auth(base_url: str, payload: dict):
    async with httpx.AsyncClient() as client:
        response = await client.post(
            f"{base_url}/v3/auth/tokens",
            json=payload
        )
        return response.json()


async def unscoped_auth(base_url: str, payload: dict):
    async with httpx.AsyncClient() as client:
        response = await client.post(
            f"{base_url}/v3/auth/tokens",
            json=payload
        )
        return response.json()
