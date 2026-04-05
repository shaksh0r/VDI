# VDI Mirroring Service

A FastAPI-based web service that proxies Guacamole daemon (guacd) to provide remote desktop access through a web browser. This service retrieves connection details from a PostgreSQL database and manages WebSocket connections between clients and the Guacamole remote desktop gateway.

## Overview

The mirroring service acts as a bridge between web clients and Guacamole, enabling secure remote desktop access (RDP, VNC, SSH, etc.). It handles:
- Web interface serving static files
- WebSocket proxy connections to guacd
- Database integration for user session management
- CORS support for cross-origin requests

## Prerequisites

- Docker and Docker Compose (recommended)
- Python 3.11+ (for local development)
- Guacamole daemon (guacd) - handled automatically by Docker Compose

## Quick Start with Docker Compose

The fastest way to start the service is with Docker Compose:

```bash
docker compose up
```

This command will:
1. Pull the Guacamole daemon image
2. Build the FastAPI service
3. Create a bridge network for inter-service communication
4. Start both services with health checks

The service will be available at `http://localhost:8000`

### Environment Configuration

Create a `.env` file in the mirroring-service directory to configure the service:

```env
# Guacamole Daemon Configuration
GUACD_HOST=guacd
GUACD_PORT=4822

# Remote Desktop/VM Configuration
VM_HOST=your_vm_host
VM_PORT=3389
VM_USERNAME=your_username
VM_PASSWORD=your_password
VM_PROTOCOL=rdp
VM_DOMAIN=your_domain
VM_SECURITY=any

# Display Settings
VM_WIDTH=1280
VM_HEIGHT=720
VM_DPI=96
```

**Note:** When using Docker Compose, `GUACD_HOST` should be set to `guacd` (the service name). For local development, use `127.0.0.1`.