FROM python:3.12-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq-dev gcc \
    && rm -rf /var/lib/apt/lists/*

COPY provisioning_service/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY provisioning_service/ /app/provisioning_service/
COPY models ./models

EXPOSE 8001

CMD ["uvicorn", "provisioning_service.main:app", "--host", "0.0.0.0", "--port", "8001"]
