from fastapi import APIRouter, Header

from models.neutron_models import (
    CreateNetworkRequest,
    CreateSubnetRequest,
    CreateFloatingIPRequest,
    AttachFloatingIPRequest,
    CreateRouterRequest,
    AttachSubnetToRouterRequest,
)
from ..logic.network import (
    get_networks,
    get_network,
    get_security_groups,
    create_network,
    get_subnets,
    attach_subnet_to_network,
    get_floating_ips,
    create_floating_ip,
    get_ports,
    get_port_by_device,
    attach_floating_ip,
    create_router,
    attach_subnet_to_router,
)

router = APIRouter()

NETWORK = "http://topcsneutron.cloudlab.buet.ac.bd"


@router.get("/networks")
async def list_networks(x_auth_token: str = Header(...)):
    return await get_networks(NETWORK, x_auth_token)


@router.get("/networks/{network_id}")
async def list_detailed_network(network_id: str, x_auth_token: str = Header(...)):
    return await get_network(NETWORK, x_auth_token, network_id)


@router.get("/security-groups")
async def list_security_groups(x_auth_token: str = Header(...)):
    return await get_security_groups(NETWORK, x_auth_token)


@router.post("/networks")
async def create_network_route(request_body: CreateNetworkRequest, x_auth_token: str = Header(...)):
    return await create_network(NETWORK, x_auth_token, request_body.model_dump())


@router.get("/subnets")
async def list_subnets(x_auth_token: str = Header(...)):
    return await get_subnets(NETWORK, x_auth_token)


@router.post("/subnets")
async def attach_subnet_to_network_route(request_body: CreateSubnetRequest, x_auth_token: str = Header(...)):
    return await attach_subnet_to_network(NETWORK, x_auth_token, request_body.model_dump())


@router.get("/floatingips")
async def list_floating_ips(x_auth_token: str = Header(...)):
    return await get_floating_ips(NETWORK, x_auth_token)


@router.post("/floatingips")
async def create_floating_ip_route(request_body: CreateFloatingIPRequest, x_auth_token: str = Header(...)):
    return await create_floating_ip(NETWORK, x_auth_token, request_body.model_dump())


@router.get("/ports")
async def list_ports(x_auth_token: str = Header(...)):
    return await get_ports(NETWORK, x_auth_token)


@router.get("/ports/device")
async def list_specific_port(instance_id: str, x_auth_token: str = Header(...)):
    return await get_port_by_device(NETWORK, x_auth_token, instance_id)


@router.put("/floatingips/{floatingip_id}")
async def attach_floating_ip_route(floatingip_id: str, request_body: AttachFloatingIPRequest, x_auth_token: str = Header(...)):
    return await attach_floating_ip(NETWORK, x_auth_token, floatingip_id, request_body.model_dump())


@router.post("/routers")
async def create_router_route(request_body: CreateRouterRequest, x_auth_token: str = Header(...)):
    return await create_router(NETWORK, x_auth_token, request_body.model_dump())


@router.put("/routers/{router_id}/add_router_interface")
async def attach_subnet_to_router_route(router_id: str, request_body: AttachSubnetToRouterRequest, x_auth_token: str = Header(...)):
    return await attach_subnet_to_router(NETWORK, x_auth_token, router_id, request_body.model_dump())