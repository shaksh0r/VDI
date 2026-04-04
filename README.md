# Run
```sudo docker compose up -d database monitoring-service prometheus grafana```

# Confirm
```sudo docker compose ps```

# Test Endpoints
```
curl http://localhost:9091/health
curl http://localhost:9091/metrics | head
curl http://localhost:9090      # Prometheus UI HTML
curl http://localhost:3000      # Grafana login HTML
```

# Stop
```sudo docker compose down```
