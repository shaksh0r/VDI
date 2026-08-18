import os
from dotenv import load_dotenv

load_dotenv()

EXTERNAL_NETWORK_ID = os.getenv("EXTERNAL_NETWORK_ID") 
print(EXTERNAL_NETWORK_ID)