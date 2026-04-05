from fastapi import FastAPI,Request,Depends,APIRouter
from contextlib import asynccontextmanager
from .database_connection import create_database_pool
from pydantic import BaseModel
import httpx

from .api.glance_cinder import router as glance_router
from .api.keystone import router as keystone_router
from .api.neutron import router as neutron_router
from .api.nova import router as nova_router
from .api.user import router as user_router

from .database_connection import get_db
from .services.pooling.pool_manager import Pool_Manager


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.db_pool = await create_database_pool()

    yield

    await app.state.db_pool.close()


app = FastAPI(lifespan=lifespan)

vm_pool = Pool_Manager(app,min_vm=2)

app.include_router(glance_router,prefix="/glance")
app.include_router(keystone_router,prefix="/keystone")
app.include_router(neutron_router,prefix="/neutron")
app.include_router(nova_router,prefix="/nova")
app.include_router(user_router,prefix="/user")


class User(BaseModel):
    name: str


@app.get("/")
def hello():
    return "hello"




@app.post("/user")
async def create_user(user: User,db = Depends(get_db)):
    name = user.name
    await db.execute("INSERT INTO temp (name) VALUES ($1)",name)

    return "done"   

