import httpx
from fastapi import APIRouter

from models.keystone_models import ScopedAuthRequest, UnscopedAuthRequest

router = APIRouter()

IDENTITY = "http://topcskeystone.cloudlab.buet.ac.bd"


@router.post("/scoped_auth")
async def scoped_auth(request_body: ScopedAuthRequest):
    payload = request_body.model_dump()

    async with httpx.AsyncClient() as client:
        response = await client.post(
            f"{IDENTITY}/v3/auth/tokens",
            json=payload
        )

        return response.json()


@router.post("/unscoped_auth")
async def unscoped_auth(request_body: UnscopedAuthRequest):
    payload = request_body.model_dump()

    async with httpx.AsyncClient() as client:
        response = await client.post(
            f"{IDENTITY}/v3/auth/tokens",
            json=payload
        )

        return response.json()
