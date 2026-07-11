import os
from dotenv import load_dotenv
from fastapi import APIRouter,status
import time
import threading
from models.nova_models import CreateInstanceLocalStorageRequest
from provisioning_service.logic.vm import create_instance_local_storage
from provisioning_service.logic.network import create_floating_ip,get_port_to_device
from provisioning_service.logic.vm import get_instance_detail

router = APIRouter()

COMPUTE = "http://topcsnova.cloudlab.buet.ac.bd/v2.1"
NETWORK = 'http://topcsneutron.cloudlab.buet.ac.bd'

load_dotenv()
x_auth_token = str(os.getenv("openstack_token"))

def poll_vm_creation(vm_id:str,x_auth_token:str):
    status = None
    while status not in ['ACTIVE','ERROR']:
        response = get_instance_detail(COMPUTE,x_auth_token,vm_id)
        if response['status'] != 200:
            return
        
          


def create_vm_attach_ip(COMPUTE,NETWORK,x_auth_token,request_body):
    response = create_instance_local_storage(COMPUTE, x_auth_token, request_body)
    vm_id = response['server']['id']
    time.sleep(10)
    port_response = get_port_to_device(COMPUTE,x_auth_token,vm_id)
    port_id = port_response['interfaceAttachments'][0]['port_id']
    floating_response = create_floating_ip(NETWORK,x_auth_token,port_id)

    return floating_response

@router.post("/servers/local_storage",status_code=status.HTTP_201_CREATED)
def create_instance_local_storage_route(request_body: CreateInstanceLocalStorageRequest):
    vm_thread = threading.Thread(target=(create_vm_attach_ip),args=(COMPUTE,NETWORK,x_auth_token,request_body))
    vm_thread.start()
    return {"Request":"Sent"}
    