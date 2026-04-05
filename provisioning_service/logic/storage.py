import httpx


async def get_images(image_url: str, x_auth_token: str):
    async with httpx.AsyncClient() as client:
        response = await client.get(
            f"{image_url}/images",
            headers={"X-Auth-Token": x_auth_token}
        )
        return response.json()


async def get_image(image_url: str, x_auth_token: str, image_id: str):
    async with httpx.AsyncClient() as client:
        response = await client.get(
            f"{image_url}/images/{image_id}",
            headers={"X-Auth-Token": x_auth_token}
        )
        return response.json()


async def get_volumes(volume_url: str, x_auth_token: str, project_id: str):
    async with httpx.AsyncClient() as client:
        response = await client.get(
            f"{volume_url}/{project_id}/volumes",
            headers={"X-Auth-Token": x_auth_token}
        )
        return response.json()


async def get_detailed_volumes(volume_url: str, x_auth_token: str, project_id: str):
    async with httpx.AsyncClient() as client:
        response = await client.get(
            f"{volume_url}/{project_id}/volumes/detail",
            headers={"X-Auth-Token": x_auth_token}
        )
        return response.json()


async def get_volume(volume_url: str, x_auth_token: str, project_id: str, volume_id: str):
    async with httpx.AsyncClient() as client:
        response = await client.get(
            f"{volume_url}/{project_id}/volumes/{volume_id}",
            headers={"X-Auth-Token": x_auth_token}
        )
        return response.json()
