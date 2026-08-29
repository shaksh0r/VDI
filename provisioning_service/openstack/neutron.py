from __future__ import annotations

from typing import Any, Optional

import httpx

from .client import OpenStackClient

async def list_networks(client: OpenStackClient) -> dict[str, Any]:
    resp = await client.network_request("GET", "/networks")
    return resp.json()


async def list_subnets(client: OpenStackClient) -> dict[str, Any]:
    resp = await client.network_request("GET", "/subnets")
    return resp.json()



async def list_ports(
    client: OpenStackClient, *, device_id: Optional[str] = None) -> dict[str, Any]:
    path = "/ports"
    if device_id:
        path += f"?device_id={device_id}"
    resp = await client.network_request("GET", path)
    return resp.json()



async def list_floating_ips(client: OpenStackClient) -> dict[str, Any]:
    resp = await client.network_request("GET", "/floatingips")
    return resp.json()


async def create_floating_ip(
    client: OpenStackClient,
    *,
    floating_network_id: str,
    port_id: Optional[str] = None,
    description: str = "",) -> dict[str, Any]:
    fip: dict[str, Any] = {"floating_network_id": floating_network_id}
    if port_id:
        fip["port_id"] = port_id
    if description:
        fip["description"] = description

    payload = {"floatingip": fip}
    resp = await client.network_request("POST", "/floatingips", json=payload)
    return resp.json()


async def delete_floating_ip(client: OpenStackClient, fip_id: str) -> httpx.Response:
    return await client.network_request("DELETE", f"/floatingips/{fip_id}")


async def update_floating_ip(
    client: OpenStackClient, fip_id: str, *, port_id: Optional[str] = None) -> dict[str, Any]:
    fip: dict[str, Any] = {}
    if port_id is not None:
        fip["port_id"] = port_id

    payload = {"floatingip": fip}
    resp = await client.network_request(
        "PUT", f"/floatingips/{fip_id}", json=payload
    )
    return resp.json()


async def list_security_groups(client: OpenStackClient) -> dict[str, Any]:
    resp = await client.network_request("GET", "/security-groups")
    return resp.json()
