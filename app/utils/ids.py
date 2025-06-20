# app/utils/ids.py

from enum import Enum
from uuid import uuid4

class IDPrefix(str, Enum):
    MESSAGE = "msg"
    EVENT = "event"
    BATCH = "batch"
    USER = "user"
    CAMPAIGN = "campaign"
    TEMPLATE = "template"
    WEBHOOK = "webhook"
    IMPORT = "import"
    TASK = "task"

def generate_prefixed_id(prefix: IDPrefix) -> str:
    """
    Generate a UUID string with a prefix.
    
    Args:
        prefix (IDPrefix): The entity prefix (e.g., MESSAGE, EVENT).
        
    Returns:
        str: A prefixed UUID string like 'msg-xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx'
    """
    return f"{prefix.value}-{uuid4()}"
