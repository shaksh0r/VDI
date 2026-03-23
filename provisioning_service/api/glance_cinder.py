from fastapi import APIRouter, Header

from ..logic.storage import (
    get_images,
    get_image,
    get_volumes,
    get_detailed_volumes,
    get_volume,
)

router = APIRouter()

IMAGE = "http://topcsglance.cloudlab.buet.ac.bd"
VOLUME = "http://topcscinder.cloudlab.buet.ac.bd/v3/09e9733314ee49e4bd34204a95b22662"


@router.get("/images")
async def list_images(x_auth_token: str = Header(...)):
    return await get_images(IMAGE, x_auth_token)


@router.get("/images/{image_id}")
async def list_detailed_image(image_id: str, x_auth_token: str = Header(...)):
    return await get_image(IMAGE, x_auth_token, image_id)


@router.get("/{project_id}/volumes")
async def list_volumes(project_id: str, x_auth_token: str = Header(...)):
    return await get_volumes(VOLUME, x_auth_token, project_id)


@router.get("/{project_id}/volumes/detail")
async def list_detailed_volumes(project_id: str, x_auth_token: str = Header(...)):
    return await get_detailed_volumes(VOLUME, x_auth_token, project_id)


@router.get("/{project_id}/volumes/{volume_id}")
async def list_detailed_volume(project_id: str, volume_id: str, x_auth_token: str = Header(...)):
    return await get_volume(VOLUME, x_auth_token, project_id, volume_id)