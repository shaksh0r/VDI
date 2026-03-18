import httpx
from fastapi import APIRouter, Header

import os
from dotenv import load_dotenv

from models.nova_models import CreateInstanceLocalStorageRequest, CreateInstanceVolumeStorageRequest

router = APIRouter()

COMPUTE = "http://topcsnova.cloudlab.buet.ac.bd/v2.1"

load_dotenv()
x_auth_token = os.getenv("openstack_token")

@router.get("/servers")
async def list_instances():
    async with httpx.AsyncClient() as client:
        response = await client.get(
            f"{COMPUTE}/servers",
            headers={"X-Auth-Token": x_auth_token}
        )

        return response.json()


@router.get("/servers/detail")
async def list_detailed_instances():
    async with httpx.AsyncClient() as client:
        response = await client.get(
            f"{COMPUTE}/servers/detail",
            headers={"X-Auth-Token": x_auth_token}
        )

        return response.json()


@router.get("/servers/{instance_id}")
async def detailed_instance(instance_id: str):
    async with httpx.AsyncClient() as client:
        response = await client.get(
            f"{COMPUTE}/servers/{instance_id}",
            headers={"X-Auth-Token": x_auth_token}
        )

        return response.json()


@router.get("/flavors")
async def list_flavors():
    async with httpx.AsyncClient() as client:
        response = await client.get(
            f"{COMPUTE}/flavors",
            headers={"X-Auth-Token": x_auth_token}
        )

        return response.json()


@router.get("/flavors/{flavor_id}")
async def list_detailed_flavor(flavor_id: str):
    async with httpx.AsyncClient() as client:
        response = await client.get(
            f"{COMPUTE}/flavors/{flavor_id}",
            headers={"X-Auth-Token": x_auth_token}
        )

        return response.json()


@router.get("/images")
async def list_images():
    async with httpx.AsyncClient() as client:
        response = await client.get(
            f"{COMPUTE}/images",
            headers={"X-Auth-Token": x_auth_token}
        )

        return response.json()


@router.get("/images/detail")
async def list_detailed_images():
    async with httpx.AsyncClient() as client:
        response = await client.get(
            f"{COMPUTE}/images/detail",
            headers={"X-Auth-Token": x_auth_token}
        )

        return response.json()


@router.get("/images/{image_id}")
async def list_detailed_image(image_id: str):
    async with httpx.AsyncClient() as client:
        response = await client.get(
            f"{COMPUTE}/images/{image_id}",
            headers={"X-Auth-Token": x_auth_token}
        )

        return response.json()


@router.get("/{project_id}/os-keypairs")
async def list_key_pairs(project_id: str):
    async with httpx.AsyncClient() as client:
        response = await client.get(
            f"{COMPUTE}/{project_id}/os-keypairs",
            headers={"X-Auth-Token": x_auth_token}
        )

        return response.json()


@router.post("/servers/local_storage")
async def create_instance_local_storage(request_body: CreateInstanceLocalStorageRequest):
    payload = request_body.model_dump()

    async with httpx.AsyncClient() as client:
        response = await client.post(
            f"{COMPUTE}/servers",
            json=payload,
            headers={"X-Auth-Token": x_auth_token}
        )

        return response.json()


@router.post("/servers/volume_storage")
async def create_instance_volume_storage(request_body: CreateInstanceVolumeStorageRequest):
    payload = request_body.model_dump()

    async with httpx.AsyncClient() as client:
        response = await client.post(
            f"{COMPUTE}/servers",
            json=payload,
            headers={"X-Auth-Token": x_auth_token}
        )

        return response.json()
