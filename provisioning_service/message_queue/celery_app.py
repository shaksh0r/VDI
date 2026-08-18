from celery import Celery
from celery.schedules import crontab

app = Celery(
    "myproject",
    broker="pyamqp://guest:guest@localhost:5672//",
    backend="rpc://",  
    include=["duplicate_tasks"],
)

app.conf.update(
    timezone="UTC",
    enable_utc=True,
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
)

app.conf.beat_schedule = {
    "run-every-30-seconds": {
        "task": "beat_task",
        "schedule": 30.0,
    },
}