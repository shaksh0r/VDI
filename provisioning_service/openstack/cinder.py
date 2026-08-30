from __future__ import annotations

from typing import Any

from .client import OpenStackClient


async def list_volumes(client: OpenStackClient) -> dict[str, Any]:
    resp = await client.volume_request("GET", "/volumes")
    return resp.json()


async def list_volumes_detail(client: OpenStackClient) -> dict[str, Any]:
    resp = await client.volume_request("GET", "/volumes/detail")
    return resp.json()


async def get_volume(client: OpenStackClient, volume_id: str) -> dict[str, Any]:
    resp = await client.volume_request("GET", f"/volumes/{volume_id}")
    return resp.json()


async def create_volume(
    client: OpenStackClient, payload: dict[str, Any]) -> dict[str, Any]:
    resp = await client.volume_request("POST", "/volumes", json=payload)
    return resp.json()


async def delete_volume(client: OpenStackClient, volume_id: str) -> None:
    await client.volume_request("DELETE", f"/volumes/{volume_id}")
