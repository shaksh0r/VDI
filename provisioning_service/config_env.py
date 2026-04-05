"""Shared DB and OpenStack settings from environment (Docker-friendly)."""
import os


def db_settings() -> dict:
    return {
        "user": os.getenv("DB_USER") or os.getenv("POSTGRES_USER", "vdi"),
        "password": os.getenv("DB_PASSWORD") or os.getenv("POSTGRES_PASSWORD", "vdi"),
        "database": os.getenv("DB_NAME") or os.getenv("POSTGRES_DB", "vdi"),
        "host": os.getenv("DB_HOST", "localhost"),
        "port": int(os.getenv("DB_PORT", "5432")),
        "min_size": int(os.getenv("DB_POOL_MIN", "2")),
        "max_size": int(os.getenv("DB_POOL_MAX", "20")),
    }


def nova_compute_url() -> str:
    return os.getenv(
        "NOVA_COMPUTE_URL",
        "http://topcsnova.cloudlab.buet.ac.bd/v2.1",
    ).rstrip("/")


def openstack_token() -> str:
    return os.getenv("openstack_token", "") or os.getenv("OPENSTACK_TOKEN", "")


def openstack_key_name() -> str:
    return os.getenv("OPENSTACK_KEY_NAME", "default-key")
