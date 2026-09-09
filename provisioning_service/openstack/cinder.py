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


async def delete_volumes_for_server(
    client: OpenStackClient, server_id: str
) -> int:
    """Detach and delete every volume attached to a (now deleted) server.

    On some clouds the boot volume is NOT removed when a server is
    deleted, even with delete_on_termination=True — each destroyed VM
    silently eats one volume from the project quota. Call this after
    nova.delete_server so volumes never leak.
    """
    resp = await client.volume_request("GET", "/volumes/detail")
    volumes = resp.json().get("volumes", [])
    deleted = 0
    for volume in volumes:
        attachments = volume.get("attachments") or []
        if not any(a.get("server_id") == server_id for a in attachments):
            continue
        volume_id = volume["id"]
        for attachment in attachments:
            if attachment.get("server_id") != server_id:
                continue
            try:
                await client.volume_request(
                    "POST",
                    f"/volumes/{volume_id}/action",
                    json={"os-detach": {"attachment_id": attachment["attachment_id"]}},
                )
            except Exception:  # noqa: BLE001 — already-detached is fine
                pass
        try:
            await delete_volume(client, volume_id)
            deleted += 1
        except Exception:  # noqa: BLE001 — volume may already be gone
            pass
    return deleted
