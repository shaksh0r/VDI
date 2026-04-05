import asyncio

from dotenv import load_dotenv
from fastapi import APIRouter

from models.nova_models import CreateInstanceLocalStorageRequest, CreateInstanceVolumeStorageRequest
from provisioning_service.config_env import nova_compute_url, openstack_token
from ..logic.vm import (
    get_instances,
    get_detailed_instances,
    get_instance,
    get_flavors,
    get_flavor,
    get_images,
    get_detailed_images,
    get_image,
    get_key_pairs,
    create_instance_local_storage,
    create_instance_volume_storage,
)

load_dotenv()

router = APIRouter()

COMPUTE = nova_compute_url()
x_auth_token = str(openstack_token())


@router.get("/servers")
async def list_instances():
    return await asyncio.to_thread(get_instances,COMPUTE, x_auth_token)


@router.get("/servers/detail")
async def list_detailed_instances():
    return await get_detailed_instances(COMPUTE, x_auth_token)


@router.get("/servers/{instance_id}")
async def detailed_instance(instance_id: str):
    return await get_instance(COMPUTE, x_auth_token, instance_id)


@router.get("/flavors")
async def list_flavors():
    return await get_flavors(COMPUTE, x_auth_token)


@router.get("/flavors/{flavor_id}")
async def list_detailed_flavor(flavor_id: str):
    return await get_flavor(COMPUTE, x_auth_token, flavor_id)


@router.get("/images")
async def list_images():
    return await get_images(COMPUTE, x_auth_token)


@router.get("/images/detail")
async def list_detailed_images():
    return await get_detailed_images(COMPUTE, x_auth_token)


@router.get("/images/{image_id}")
async def list_detailed_image(image_id: str):
    return await get_image(COMPUTE, x_auth_token, image_id)


@router.get("/{project_id}/os-keypairs")
async def list_key_pairs(project_id: str):
    return await get_key_pairs(COMPUTE, x_auth_token, project_id)


@router.post("/servers/local_storage")
async def create_instance_local_storage_route(request_body: CreateInstanceLocalStorageRequest):
    return await create_instance_local_storage(COMPUTE, x_auth_token, request_body.model_dump())


@router.post("/servers/volume_storage")
async def create_instance_volume_storage_route(request_body: CreateInstanceVolumeStorageRequest):
    return await create_instance_volume_storage(COMPUTE, x_auth_token, request_body.model_dump())