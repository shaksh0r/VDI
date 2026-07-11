from fastapi import FastAPI
from tasks import process_data
from pydantic import BaseModel

class data(BaseModel):
    x:int

app = FastAPI()

@app.post("/send")
def send_task(x:int):
    task = process_data.delay(x)
    return {"task_id":task.id}

