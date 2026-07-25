from __future__ import annotations

from typing import Any

from .client import OpenStackClient


async def list_images(client: OpenStackClient) -> dict[str, Any]:
    resp = await client.image_request("GET", "/v2/images")
    return resp.json()


async def get_image(client: OpenStackClient, image_id: str) -> dict[str, Any]:
    resp = await client.image_request("GET", f"/v2/images/{image_id}")
    return resp.json()
