import httpx
from fastapi import APIRouter, Header

from models.neutron_models import (
    CreateNetworkRequest,
    CreateSubnetRequest,
    CreateFloatingIPRequest,
    AttachFloatingIPRequest,
    CreateRouterRequest,
    AttachSubnetToRouterRequest,
)

router = APIRouter()

NETWORK = "http://topcsneutron.cloudlab.buet.ac.bd"


@router.get("/networks")
async def list_networks(x_auth_token: str = Header(...)):
    async with httpx.AsyncClient() as client:
        response = await client.get(
            f"{NETWORK}/networks",
            headers={"X-Auth-Token": x_auth_token}
        )

        return response.json()


@router.get("/networks/{network_id}")
async def list_detailed_network(network_id: str, x_auth_token: str = Header(...)):
    async with httpx.AsyncClient() as client:
        response = await client.get(
            f"{NETWORK}/networks/{network_id}",
            headers={"X-Auth-Token": x_auth_token}
        )

        return response.json()


@router.get("/security-groups")
async def list_security_groups(x_auth_token: str = Header(...)):
    async with httpx.AsyncClient() as client:
        response = await client.get(
            f"{NETWORK}/security-groups",
            headers={"X-Auth-Token": x_auth_token}
        )

        return response.json()


@router.post("/networks")
async def create_network(request_body: CreateNetworkRequest, x_auth_token: str = Header(...)):
    payload = request_body.model_dump()

    async with httpx.AsyncClient() as client:
        response = await client.post(
            f"{NETWORK}/networks",
            json=payload,
            headers={"X-Auth-Token": x_auth_token}
        )

        return response.json()


@router.get("/subnets")
async def list_subnets(x_auth_token: str = Header(...)):
    async with httpx.AsyncClient() as client:
        response = await client.get(
            f"{NETWORK}/subnets",
            headers={"X-Auth-Token": x_auth_token}
        )

        return response.json()


@router.post("/subnets")
async def attach_subnet_to_network(request_body: CreateSubnetRequest, x_auth_token: str = Header(...)):
    payload = request_body.model_dump()

    async with httpx.AsyncClient() as client:
        response = await client.post(
            f"{NETWORK}/subnets",
            json=payload,
            headers={"X-Auth-Token": x_auth_token}
        )

        return response.json()


@router.get("/floatingips")
async def list_floating_ips(x_auth_token: str = Header(...)):
    async with httpx.AsyncClient() as client:
        response = await client.get(
            f"{NETWORK}/floatingips",
            headers={"X-Auth-Token": x_auth_token}
        )

        return response.json()


@router.post("/floatingips")
async def create_floating_ip(request_body: CreateFloatingIPRequest, x_auth_token: str = Header(...)):
    payload = request_body.model_dump()

    async with httpx.AsyncClient() as client:
        response = await client.post(
            f"{NETWORK}/floatingips",
            json=payload,
            headers={"X-Auth-Token": x_auth_token}
        )

        return response.json()


@router.get("/ports")
async def list_ports(x_auth_token: str = Header(...)):
    async with httpx.AsyncClient() as client:
        response = await client.get(
            f"{NETWORK}/ports",
            headers={"X-Auth-Token": x_auth_token}
        )

        return response.json()


@router.get("/ports/device")
async def list_specific_port(instance_id: str, x_auth_token: str = Header(...)):
    async with httpx.AsyncClient() as client:
        response = await client.get(
            f"{NETWORK}/ports",
            params={"device_id": instance_id},
            headers={"X-Auth-Token": x_auth_token}
        )

        return response.json()


@router.put("/floatingips/{floatingip_id}")
async def attach_floating_ip(floatingip_id: str, request_body: AttachFloatingIPRequest, x_auth_token: str = Header(...)):
    payload = request_body.model_dump()

    async with httpx.AsyncClient() as client:
        response = await client.put(
            f"{NETWORK}/floatingips/{floatingip_id}",
            json=payload,
            headers={"X-Auth-Token": x_auth_token}
        )

        return response.json()


@router.post("/routers")
async def create_router(request_body: CreateRouterRequest, x_auth_token: str = Header(...)):
    payload = request_body.model_dump()

    async with httpx.AsyncClient() as client:
        response = await client.post(
            f"{NETWORK}/routers",
            json=payload,
            headers={"X-Auth-Token": x_auth_token}
        )

        return response.json()


@router.put("/routers/{router_id}/add_router_interface")
async def attach_subnet_to_router(router_id: str, request_body: AttachSubnetToRouterRequest, x_auth_token: str = Header(...)):
    payload = request_body.model_dump()

    async with httpx.AsyncClient() as client:
        response = await client.put(
            f"{NETWORK}/routers/{router_id}/add_router_interface",
            json=payload,
            headers={"X-Auth-Token": x_auth_token}
        )

        return response.json()
