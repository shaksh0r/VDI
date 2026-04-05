FROM python:3.11-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc libpq-dev \
    && rm -rf /var/lib/apt/lists/*

COPY notifications /app/notifications
COPY models /app/models
COPY provisioning_service /app/provisioning_service

RUN pip install --no-cache-dir -r /app/provisioning_service/requirements.txt

ENV PYTHONPATH=/app

EXPOSE 8010

CMD ["uvicorn", "provisioning_service.server:app", "--host", "0.0.0.0", "--port", "8010"]
