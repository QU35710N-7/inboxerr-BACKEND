"""
Import all models here to ensure they are registered with SQLAlchemy.
"""
# Import Base
from app.models.base import Base

# Import all models
from app.models.user import User, APIKey
from app.models.campaign import Campaign
from app.models.message import Message, MessageEvent, MessageBatch, MessageTemplate
from app.models.webhook import Webhook, WebhookDelivery, WebhookEvent

# Metrics Models
from app.models.metrics import UserMetrics

# This allows alembic to auto-discover all models when creating migrations