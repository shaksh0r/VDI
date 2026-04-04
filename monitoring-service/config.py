"""Configuration for the VDI Monitoring Microservice."""
import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent / ".env")

# OpenStack (use clouds.yaml or env)
OS_AUTH_URL = os.getenv("OS_AUTH_URL", "")
OS_PROJECT_NAME = os.getenv("OS_PROJECT_NAME", "admin")
OS_USERNAME = os.getenv("OS_USERNAME", "")
OS_PASSWORD = os.getenv("OS_PASSWORD", "")
OS_USER_DOMAIN_NAME = os.getenv("OS_USER_DOMAIN_NAME", "Default")
OS_PROJECT_DOMAIN_NAME = os.getenv("OS_PROJECT_DOMAIN_NAME", "Default")

# Monitoring DB (same PostgreSQL as VDI)
MONITORING_DB_HOST = os.getenv("MONITORING_DB_HOST", "localhost")
MONITORING_DB_PORT = int(os.getenv("MONITORING_DB_PORT", "5432"))
MONITORING_DB_NAME = os.getenv("MONITORING_DB_NAME", "vdi")
MONITORING_DB_USER = os.getenv("MONITORING_DB_USER", "vdi")
MONITORING_DB_PASSWORD = os.getenv("MONITORING_DB_PASSWORD", "")

def get_db_connection_string() -> str:
    return (
        f"postgresql://{MONITORING_DB_USER}:{MONITORING_DB_PASSWORD}"
        f"@{MONITORING_DB_HOST}:{MONITORING_DB_PORT}/{MONITORING_DB_NAME}"
    )

def openstack_configured() -> bool:
    return bool(OS_AUTH_URL and OS_USERNAME and OS_PASSWORD)
