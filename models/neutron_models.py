from pydantic import BaseModel
from typing import List, Optional


class Network(BaseModel):
    name: str
    admin_state_up: bool


class CreateNetworkRequest(BaseModel):
    network: Network


class AllocationPool(BaseModel):
    start: str
    end: str


class Subnet(BaseModel):
    name: str
    network_id: str
    ip_version: int
    cidr: str
    allocation_pools: List[AllocationPool]
    dns_nameservers: List[str]
    gateway_ip: str


class CreateSubnetRequest(BaseModel):
    subnet: Subnet

class FloatingIPCreate(BaseModel):
    floating_network_id: str
    description: Optional[str] = None


class CreateFloatingIPRequest(BaseModel):
    floatingip: FloatingIPCreate



class FloatingIPAttach(BaseModel):
    port_id: str


class AttachFloatingIPRequest(BaseModel):
    floatingip: FloatingIPAttach



class ExternalGatewayInfo(BaseModel):
    network_id: str


class Router(BaseModel):
    name: str
    admin_state_up: bool
    external_gateway_info: ExternalGatewayInfo


class CreateRouterRequest(BaseModel):
    router: Router



class AttachSubnetToRouterRequest(BaseModel):
    subnet_id: str
