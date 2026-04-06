FROM python:3.11-slim

WORKDIR /app

# Install dependencies first (better layer caching)
COPY provisioning_service/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

# Copy the entire project so imports like `provisioning_service.server` resolve
COPY provisioning_service ./provisioning_service
COPY models ./models

# Make the parent /app the working directory so top-level package imports work
ENV PYTHONPATH=/app

COPY vpn-routes.sh /vpn-routes.sh
RUN chmod +x /vpn-routes.sh

ENV PYTHONPATH=/app

# Entrypoint runs routes first, then hands off to CMD
ENTRYPOINT ["/bin/sh", "-c", "/vpn-routes.sh && exec \"$@\"", "--"]

# Default command (overridden in docker-compose per service)
CMD ["uvicorn", "provisioning_service.server:app", "--host", "0.0.0.0", "--port", "8002"]