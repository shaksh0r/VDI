from __future__ import annotations

from typing import Any, Optional

from .client import OpenStackClient


async def list_servers(client: OpenStackClient) -> dict[str, Any]:
    resp = await client.compute_request("GET", "/servers")
    return resp.json()


async def list_servers_detail(client: OpenStackClient) -> dict[str, Any]:
    resp = await client.compute_request("GET", "/servers/detail")
    return resp.json()


async def get_server(client: OpenStackClient, server_id: str) -> dict[str, Any]:
    resp = await client.compute_request("GET", f"/servers/{server_id}")
    return resp.json()


async def create_server(
    client: OpenStackClient, payload: dict[str, Any]) -> dict[str, Any]:
    resp = await client.compute_request("POST", "/servers", json=payload)
    return resp.json()


async def delete_server(client: OpenStackClient, server_id: str) -> None:
    await client.compute_request("DELETE", f"/servers/{server_id}")


async def list_flavors(client: OpenStackClient) -> dict[str, Any]:
    resp = await client.compute_request("GET", "/flavors")
    return resp.json()


async def get_flavor(client: OpenStackClient, flavor_id: str) -> dict[str, Any]:
    resp = await client.compute_request("GET", f"/flavors/{flavor_id}")
    return resp.json()



async def list_keypairs(client: OpenStackClient) -> dict[str, Any]:
    resp = await client.compute_request("GET", "/os-keypairs")
    return resp.json()


async def list_server_interfaces(
    client: OpenStackClient, server_id: str) -> dict[str, Any]:
    resp = await client.compute_request(
        "GET", f"/servers/{server_id}/os-interface"
    )
    return resp.json()
