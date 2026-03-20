import httpx


async def get_networks(base_url: str, x_auth_token: str):
    async with httpx.AsyncClient() as client:
        response = await client.get(
            f"{base_url}/networks",
            headers={"X-Auth-Token": x_auth_token}
        )
        return response.json()


async def get_network(base_url: str, x_auth_token: str, network_id: str):
    async with httpx.AsyncClient() as client:
        response = await client.get(
            f"{base_url}/networks/{network_id}",
            headers={"X-Auth-Token": x_auth_token}
        )
        return response.json()


async def get_security_groups(base_url: str, x_auth_token: str):
    async with httpx.AsyncClient() as client:
        response = await client.get(
            f"{base_url}/security-groups",
            headers={"X-Auth-Token": x_auth_token}
        )
        return response.json()


async def create_network(base_url: str, x_auth_token: str, payload: dict):
    async with httpx.AsyncClient() as client:
        response = await client.post(
            f"{base_url}/networks",
            json=payload,
            headers={"X-Auth-Token": x_auth_token}
        )
        return response.json()


async def get_subnets(base_url: str, x_auth_token: str):
    async with httpx.AsyncClient() as client:
        response = await client.get(
            f"{base_url}/subnets",
            headers={"X-Auth-Token": x_auth_token}
        )
        return response.json()


async def attach_subnet_to_network(base_url: str, x_auth_token: str, payload: dict):
    async with httpx.AsyncClient() as client:
        response = await client.post(
            f"{base_url}/subnets",
            json=payload,
            headers={"X-Auth-Token": x_auth_token}
        )
        return response.json()


async def get_floating_ips(base_url: str, x_auth_token: str):
    async with httpx.AsyncClient() as client:
        response = await client.get(
            f"{base_url}/floatingips",
            headers={"X-Auth-Token": x_auth_token}
        )
        return response.json()


async def create_floating_ip(base_url: str, x_auth_token: str, payload: dict):
    async with httpx.AsyncClient() as client:
        response = await client.post(
            f"{base_url}/floatingips",
            json=payload,
            headers={"X-Auth-Token": x_auth_token}
        )
        return response.json()


async def get_ports(base_url: str, x_auth_token: str):
    async with httpx.AsyncClient() as client:
        response = await client.get(
            f"{base_url}/ports",
            headers={"X-Auth-Token": x_auth_token}
        )
        return response.json()


async def get_port_by_device(base_url: str, x_auth_token: str, instance_id: str):
    async with httpx.AsyncClient() as client:
        response = await client.get(
            f"{base_url}/ports",
            params={"device_id": instance_id},
            headers={"X-Auth-Token": x_auth_token}
        )
        return response.json()


async def attach_floating_ip(base_url: str, x_auth_token: str, floatingip_id: str, payload: dict):
    async with httpx.AsyncClient() as client:
        response = await client.put(
            f"{base_url}/floatingips/{floatingip_id}",
            json=payload,
            headers={"X-Auth-Token": x_auth_token}
        )
        return response.json()


async def create_router(base_url: str, x_auth_token: str, payload: dict):
    async with httpx.AsyncClient() as client:
        response = await client.post(
            f"{base_url}/routers",
            json=payload,
            headers={"X-Auth-Token": x_auth_token}
        )
        return response.json()


async def attach_subnet_to_router(base_url: str, x_auth_token: str, router_id: str, payload: dict):
    async with httpx.AsyncClient() as client:
        response = await client.put(
            f"{base_url}/routers/{router_id}/add_router_interface",
            json=payload,
            headers={"X-Auth-Token": x_auth_token}
        )
        return response.json()
