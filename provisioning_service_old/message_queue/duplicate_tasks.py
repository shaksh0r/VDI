from celery_app import app
import time

@app.task(name="beat_task")
def func():
    return "Hello World"
