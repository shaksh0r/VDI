from fastapi import APIRouter

from models.keystone_models import ScopedAuthRequest, UnscopedAuthRequest
from ..logic.identity import scoped_auth, unscoped_auth

router = APIRouter()

IDENTITY = "http://topcskeystone.cloudlab.buet.ac.bd"


@router.post("/scoped_auth")
async def scoped_auth_route(request_body: ScopedAuthRequest):
    return await scoped_auth(IDENTITY, request_body.model_dump())


@router.post("/unscoped_auth")
async def unscoped_auth_route(request_body: UnscopedAuthRequest):
    return await unscoped_auth(IDENTITY, request_body.model_dump())