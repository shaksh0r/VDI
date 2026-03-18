import httpx
from fastapi import APIRouter, Header

router = APIRouter()

IMAGE = "http://topcsglance.cloudlab.buet.ac.bd"
VOLUME = "http://topcscinder.cloudlab.buet.ac.bd/v3/09e9733314ee49e4bd34204a95b22662"



@router.get("/images")
async def list_images(x_auth_token: str = Header(...)):
    async with httpx.AsyncClient() as client:
        response = await client.get(
            f"{IMAGE}/images",
            headers={"X-Auth-Token": x_auth_token}
        )

        return response.json()


@router.get("/images/{image_id}")
async def list_detailed_image(image_id: str, x_auth_token: str = Header(...)):
    async with httpx.AsyncClient() as client:
        response = await client.get(
            f"{IMAGE}/images/{image_id}",
            headers={"X-Auth-Token": x_auth_token}
        )

        return response.json()


@router.get("/{project_id}/volumes")
async def list_volumes(project_id: str, x_auth_token: str = Header(...)):
    async with httpx.AsyncClient() as client:
        response = await client.get(
            f"{VOLUME}/{project_id}/volumes",
            headers={"X-Auth-Token": x_auth_token}
        )

        return response.json()


@router.get("/{project_id}/volumes/detail")
async def list_detailed_volumes(project_id: str, x_auth_token: str = Header(...)):
    async with httpx.AsyncClient() as client:
        response = await client.get(
            f"{VOLUME}/{project_id}/volumes/detail",
            headers={"X-Auth-Token": x_auth_token}
        )

        return response.json()


@router.get("/{project_id}/volumes/{volume_id}")
async def list_detailed_volume(project_id: str, volume_id: str, x_auth_token: str = Header(...)):
    async with httpx.AsyncClient() as client:
        response = await client.get(
            f"{VOLUME}/{project_id}/volumes/{volume_id}",
            headers={"X-Auth-Token": x_auth_token}
        )

        return response.json()
