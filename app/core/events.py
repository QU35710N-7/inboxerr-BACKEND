"""
Event handlers for application lifecycle events.
"""
import asyncio
import logging
from typing import Dict, List, Any

from app.core.config import settings
from app.services.event_bus.bus import get_event_bus
from app.services.event_bus.events import EventType

logger = logging.getLogger("inboxerr")

# Collection of background tasks to manage
background_tasks: List[asyncio.Task] = []


async def startup_event_handler() -> None:
    """
    Handle application startup.
    
    Initialize services, database connections, and start background processes.
    """
    logger.info("Starting Inboxerr Backend application")
    
    # Initialize database (async)
    try:
        # We'll implement this in the database module
        from app.db.session import initialize_database
        await initialize_database()
        logger.info("Database initialized successfully")
    except Exception as e:
        logger.error(f"Error initializing database: {e}")
        # Don't raise error to allow startup to continue
    
    # Initialize event bus
    try:
        event_bus = get_event_bus()
        await event_bus.initialize()
        logger.info("Event bus initialized successfully")
    except Exception as e:
        logger.error(f"Error initializing event bus: {e}")
    
    # Start retry engine if enabled
    if settings.RETRY_ENABLED:
        try:
            from app.services.sms.retry_engine import get_retry_engine
            retry_engine = get_retry_engine()
            retry_task = asyncio.create_task(retry_engine.start())
            background_tasks.append(retry_task)
            logger.info("Retry engine started successfully")
        except Exception as e:
            logger.error(f"Error starting retry engine: {e}")
    
    # Start webhook listener if enabled
    try:
        from app.services.webhooks.manager import initialize_webhook_manager
        await initialize_webhook_manager()
        logger.info("Webhook manager initialized successfully")
    except Exception as e:
        logger.error(f"Error initializing webhooks: {e}")
    
    # Initialize metrics collector
    try:
        from app.services.metrics.collector import initialize_metrics
        await initialize_metrics()
        logger.info("Metrics collector initialized successfully")
    except Exception as e:
        logger.error(f"Error initializing metrics: {e}")
    
    # Log successful startup
    logger.info(f"✅ {settings.PROJECT_NAME} v{settings.VERSION} startup complete")


async def shutdown_event_handler() -> None:
    """
    Handle application shutdown.
    
    Clean up resources and close connections properly.
    """
    logger.info("Shutting down Inboxerr Backend application")
    
    # Publish shutdown event
    try:
        event_bus = get_event_bus()
        await event_bus.publish(EventType.SYSTEM_SHUTDOWN, {
            "reason": "Application shutdown",
            "graceful": True
        })
        logger.info("Published shutdown event")
    except Exception as e:
        logger.error(f"Error publishing shutdown event: {e}")
    
    # Cancel all background tasks
    for task in background_tasks:
        if not task.done():
            task.cancel()
            try:
                # Wait briefly for task to cancel
                await asyncio.wait_for(task, timeout=5.0)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                logger.warning(f"Task {task.get_name()} was cancelled")
    
    # Close database connections
    try:
        from app.db.session import close_database_connections
        await close_database_connections()
        logger.info("Database connections closed")
    except Exception as e:
        logger.error(f"Error closing database connections: {e}")
    
    # Shutdown webhook manager
    try:
        from app.services.webhooks.manager import shutdown_webhook_manager
        await shutdown_webhook_manager()
        logger.info("Webhook manager shutdown complete")
    except Exception as e:
        logger.error(f"Error shutting down webhook manager: {e}")
    
    logger.info("✅ Application shutdown complete")