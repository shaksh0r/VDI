from celery import Celery

celery = Celery(
    "worker",
    broker="amqp://guest:guest@localhost:5672//",
    backend="rpc://",
    include=["provisioning_service.message_queue.tasks"]
)