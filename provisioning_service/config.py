import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent / ".env")

#OpenStack
OPENSTACK_AUTH_URL          = os.getenv("OPENSTACK_AUTH_URL", "")
OPENSTACK_USERNAME          = os.getenv("OPENSTACK_USERNAME", "")
OPENSTACK_PASSWORD          = os.getenv("OPENSTACK_PASSWORD", "")
OPENSTACK_PROJECT_NAME      = os.getenv("OPENSTACK_PROJECT_NAME", "")
OPENSTACK_USER_DOMAIN_NAME  = os.getenv("OPENSTACK_USER_DOMAIN_NAME", "Default")
OPENSTACK_PROJECT_DOMAIN_NAME = os.getenv("OPENSTACK_PROJECT_DOMAIN_NAME", "Default")
OPENSTACK_COMPUTE_URL = os.getenv("OPENSTACK_COMPUTE_URL", "http://topcsnova.cloudlab.buet.ac.bd/v2.1")
OPENSTACK_NETWORK_URL = os.getenv("OPENSTACK_NETWORK_URL", "http://topcsneutron.cloudlab.buet.ac.bd/v2.0")
OPENSTACK_IMAGE_URL   = os.getenv("OPENSTACK_IMAGE_URL",   "http://topcsglance.cloudlab.buet.ac.bd/v2")
OPENSTACK_VOLUME_URL  = os.getenv("OPENSTACK_VOLUME_URL",  "http://topcscinder.cloudlab.buet.ac.bd/v3")

EXTERNAL_NETWORK_ID   = os.getenv("EXTERNAL_NETWORK_ID", "")

#Default VM configuration
DEFAULT_KEY_NAME       = os.getenv("DEFAULT_KEY_NAME",       "default-key")
DEFAULT_SEC_GROUP_NAME = os.getenv("DEFAULT_SEC_GROUP_NAME", "default")
EXTRA_SEC_GROUP_1      = os.getenv("EXTRA_SEC_GROUP_1",      "")
EXTRA_SEC_GROUP_2      = os.getenv("EXTRA_SEC_GROUP_2",      "")

#Database
DB_USER     = os.getenv("DB_USER",     "myuser")
DB_PASSWORD = os.getenv("DB_PASSWORD", "mypassword")
DB_NAME     = os.getenv("DB_NAME",     "mydatabase")
DB_HOST     = os.getenv("DB_HOST",     "database")
DB_PORT     = int(os.getenv("DB_PORT", "5432"))
DB_POOL_MIN = int(os.getenv("DB_POOL_MIN", "5"))
DB_POOL_MAX = int(os.getenv("DB_POOL_MAX", "20"))

#Auth service
AUTH_SERVICE_URL = os.getenv("AUTH_SERVICE_URL", "http://auth-service:8003")

#Intervals & timeouts
RECONCILIATION_INTERVAL_SECONDS = int(os.getenv("RECONCILIATION_INTERVAL_SECONDS", "30"))
JOB_POLL_INTERVAL_SECONDS       = int(os.getenv("JOB_POLL_INTERVAL_SECONDS",       "5"))
VM_CREATION_POLL_INTERVAL       = int(os.getenv("VM_CREATION_POLL_INTERVAL",       "10"))
VM_CREATION_TIMEOUT_SECONDS     = int(os.getenv("VM_CREATION_TIMEOUT_SECONDS",     "300"))
OPENSTACK_REQUEST_TIMEOUT       = int(os.getenv("OPENSTACK_REQUEST_TIMEOUT",       "30"))

#Pool defaults
DEFAULT_MAX_SESSION_MINUTES = int(os.getenv("DEFAULT_MAX_SESSION_MINUTES", "240"))
