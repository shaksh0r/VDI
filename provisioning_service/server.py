from fastapi import FastAPI,Request,Depends,APIRouter
import threading
from contextlib import asynccontextmanager
import os
import time
from .api.nova import router as nova_router
from dotenv import load_dotenv
from .logic.vm import get_detailed_instances
from provisioning_service.model import generate_payload
from .api.nova import create_vm_attach_ip

load_dotenv()
x_auth_token = str(os.getenv("openstack_token"))
background_thread = None
stop_event = threading.Event()
COMPUTE = "http://topcsnova.cloudlab.buet.ac.bd/v2.1"
NETWORK = 'http://topcsneutron.cloudlab.buet.ac.bd'

vm_pool_count = 2

def background_worker(base_url:str,x_auth_token:str,vm_pool_count:int):
    while not stop_event.set():
        response = get_detailed_instances(base_url,x_auth_token)
        
        if response['status_code'] != 200:
            continue

        data = response['data']
        servers = data['servers']

        active_server_count = 0

        for server in servers:
            if server['status'] == 'ACTIVE':
                active_server_count += 1
        
        if active_server_count < vm_pool_count:
            difference = vm_pool_count - active_server_count

            for i in range(difference):
                payload = generate_payload()
                response = create_vm_attach_ip(COMPUTE,NETWORK,x_auth_token,payload)

                print(response)
        
        time.sleep(20)


    

@asynccontextmanager
async def lifespan(app:FastAPI):
    global background_thread

    stop_event.clear()
    background_thread = threading.Thread(target=background_worker,args=(COMPUTE,x_auth_token,vm_pool_count),daemon=True)
    background_thread.start()

    yield

    stop_event.set()
    if background_thread and background_thread.is_alive():
        background_thread.join(timeout=5) 
        print("Thread stopped")
    







app = FastAPI(lifespan=lifespan)


app.include_router(nova_router,prefix="/nova")

@app.get("/")
def hello():
    return "Hello"