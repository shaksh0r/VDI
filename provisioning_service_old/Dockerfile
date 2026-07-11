FROM python:3.11-slim

WORKDIR /app


COPY provisioning_service/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

COPY provisioning_service ./provisioning_service
COPY models ./models


ENV PYTHONPATH=/app


ENV PYTHONPATH=/app

CMD ["uvicorn", "provisioning_service.server:app", "--host", "0.0.0.0", "--port", "8002"]