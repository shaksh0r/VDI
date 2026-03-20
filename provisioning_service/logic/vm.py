import httpx


def get_instances(base_url: str, x_auth_token: str):
    with httpx.Client() as client:
        response = client.get(
            f"{base_url}/servers",
            headers={"X-Auth-Token": x_auth_token}
        )
        return response.json()


async def get_detailed_instances(base_url: str, x_auth_token: str):
    async with httpx.AsyncClient() as client:
        response = await client.get(
            f"{base_url}/servers/detail",
            headers={"X-Auth-Token": x_auth_token}
        )
        return response.json()


async def get_instance(base_url: str, x_auth_token: str, instance_id: str):
    async with httpx.AsyncClient() as client:
        response = await client.get(
            f"{base_url}/servers/{instance_id}",
            headers={"X-Auth-Token": x_auth_token}
        )
        return response.json()


async def get_flavors(base_url: str, x_auth_token: str):
    async with httpx.AsyncClient() as client:
        response = await client.get(
            f"{base_url}/flavors",
            headers={"X-Auth-Token": x_auth_token}
        )
        return response.json()


async def get_flavor(base_url: str, x_auth_token: str, flavor_id: str):
    async with httpx.AsyncClient() as client:
        response = await client.get(
            f"{base_url}/flavors/{flavor_id}",
            headers={"X-Auth-Token": x_auth_token}
        )
        return response.json()


async def get_images(base_url: str, x_auth_token: str):
    async with httpx.AsyncClient() as client:
        response = await client.get(
            f"{base_url}/images",
            headers={"X-Auth-Token": x_auth_token}
        )
        return response.json()


async def get_detailed_images(base_url: str, x_auth_token: str):
    async with httpx.AsyncClient() as client:
        response = await client.get(
            f"{base_url}/images/detail",
            headers={"X-Auth-Token": x_auth_token}
        )
        return response.json()


async def get_image(base_url: str, x_auth_token: str, image_id: str):
    async with httpx.AsyncClient() as client:
        response = await client.get(
            f"{base_url}/images/{image_id}",
            headers={"X-Auth-Token": x_auth_token}
        )
        return response.json()


async def get_key_pairs(base_url: str, x_auth_token: str, project_id: str):
    async with httpx.AsyncClient() as client:
        response = await client.get(
            f"{base_url}/{project_id}/os-keypairs",
            headers={"X-Auth-Token": x_auth_token}
        )
        return response.json()


async def create_instance_local_storage(base_url: str, x_auth_token: str, payload: dict):
    async with httpx.AsyncClient() as client:
        response = await client.post(
            f"{base_url}/servers",
            json=payload,
            headers={"X-Auth-Token": x_auth_token}
        )
        return response.json()


async def create_instance_volume_storage(base_url: str, x_auth_token: str, payload: dict):
    async with httpx.AsyncClient() as client:
        response = await client.post(
            f"{base_url}/servers",
            json=payload,
            headers={"X-Auth-Token": x_auth_token}
        )
        return response.json()