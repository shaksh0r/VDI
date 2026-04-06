FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

COPY vpn-routes.sh /vpn-routes.sh
RUN chmod +x /vpn-routes.sh

ENV PYTHONPATH=/app

# Entrypoint runs routes first, then hands off to CMD
ENTRYPOINT ["/bin/sh", "-c", "/vpn-routes.sh && exec \"$@\"", "--"]

EXPOSE 8003

CMD ["uvicorn", "server:app", "--host", "0.0.0.0", "--port", "8003"]