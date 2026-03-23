from pydantic import BaseModel
from typing import List, Optional
from .common import SecurityGroup, NetworkRef



class ServerLocalStorage(BaseModel):
    name: str
    imageRef: str
    flavorRef: str
    key_name: str
    networks: List[NetworkRef]
    security_groups: List[SecurityGroup]


class CreateInstanceLocalStorageRequest(BaseModel):
    server: ServerLocalStorage



class BlockDevice(BaseModel):
    boot_index: int
    uuid: str
    source_type: str
    destination_type: str
    volume_size: int
    delete_on_termination: bool


class ServerVolumeStorage(BaseModel):
    name: str
    flavorRef: str
    key_name: str
    block_device_mapping_v2: List[BlockDevice]
    networks: List[NetworkRef]
    security_groups: List[SecurityGroup]


class CreateInstanceVolumeStorageRequest(BaseModel):
    server: ServerVolumeStorage
