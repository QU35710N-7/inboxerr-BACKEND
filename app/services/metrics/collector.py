# app/services/metrics/collector.py
"""
Metrics collection service.
"""
import asyncio
import logging
from typing import Dict, Any, Optional, List
from datetime import datetime, date, timezone, timedelta

from app.services.event_bus.bus import get_event_bus
from app.services.event_bus.events import EventType
from app.db.session import get_repository_context

logger = logging.getLogger("inboxerr.metrics")

async def initialize_metrics() -> None:
    """Initialize metrics collector."""
    logger.info("Initializing metrics collector")
    
    # Subscribe to events
    event_bus = get_event_bus()
    
    # Message events
    await event_bus.subscribe(
        EventType.MESSAGE_CREATED,
        _handle_message_created,
        "metrics.message_created"
    )
    
    await event_bus.subscribe(
        EventType.MESSAGE_SENT,
        _handle_message_sent,
        "metrics.message_sent"
    )
    
    await event_bus.subscribe(
        EventType.MESSAGE_DELIVERED,
        _handle_message_delivered,
        "metrics.message_delivered"
    )
    
    await event_bus.subscribe(
        EventType.MESSAGE_FAILED,
        _handle_message_failed,
        "metrics.message_failed"
    )
    
    # Campaign events
    await event_bus.subscribe(
        EventType.CAMPAIGN_CREATED,
        _handle_campaign_created,
        "metrics.campaign_created"
    )
    
    await event_bus.subscribe(
        EventType.CAMPAIGN_COMPLETED,
        _handle_campaign_completed,
        "metrics.campaign_completed"
    )
    
    # Template events
    await event_bus.subscribe(
        EventType.TEMPLATE_CREATED,
        _handle_template_created,
        "metrics.template_created"
    )
    
    await event_bus.subscribe(
        EventType.TEMPLATE_USED,
        _handle_template_used,
        "metrics.template_used"
    )
    
    logger.info("Metrics collector initialized")

# Event handlers
async def _handle_message_created(data: Dict[str, Any]) -> None:
    """Handle message created event."""
    user_id = data.get("user_id")
    if not user_id:
        return
    
    # Get today's date
    today = datetime.now(timezone.utc).date()
    
    # Update metrics
    from app.db.repositories.metrics import MetricsRepository
    
    try:
        async with get_repository_context(MetricsRepository) as metrics_repo:
            await metrics_repo.increment_metric(
                user_id=user_id,
                date=today,
                metric_name="messages_scheduled",
                increment=1
            )
    except Exception as e:
        logger.error(f"Error updating metrics for message_created: {e}")

async def _handle_message_sent(data: Dict[str, Any]) -> None:
    """Handle message sent event."""
    user_id = data.get("user_id")
    if not user_id:
        return
    
    # Get today's date
    today = datetime.now(timezone.utc).date()
    
    # Update metrics
    from app.db.repositories.metrics import MetricsRepository
    
    try:
        async with get_repository_context(MetricsRepository) as metrics_repo:
            await metrics_repo.increment_metric(
                user_id=user_id,
                date=today,
                metric_name="messages_sent",
                increment=1
            )
            
            # Decrement scheduled count if it was scheduled
            scheduled = data.get("scheduled", False)
            if scheduled:
                await metrics_repo.increment_metric(
                    user_id=user_id,
                    date=today,
                    metric_name="messages_scheduled",
                    increment=-1
                )
    except Exception as e:
        logger.error(f"Error updating metrics for message_sent: {e}")

async def _handle_message_delivered(data: Dict[str, Any]) -> None:
    """Handle message delivered event."""
    user_id = data.get("user_id")
    if not user_id:
        return
    
    # Get today's date
    today = datetime.now(timezone.utc).date()
    
    # Update metrics
    from app.db.repositories.metrics import MetricsRepository
    
    try:
        async with get_repository_context(MetricsRepository) as metrics_repo:
            await metrics_repo.increment_metric(
                user_id=user_id,
                date=today,
                metric_name="messages_delivered",
                increment=1
            )
    except Exception as e:
        logger.error(f"Error updating metrics for message_delivered: {e}")

async def _handle_message_failed(data: Dict[str, Any]) -> None:
    """Handle message failed event."""
    user_id = data.get("user_id")
    if not user_id:
        return
    
    # Get today's date
    today = datetime.now(timezone.utc).date()
    
    # Update metrics
    from app.db.repositories.metrics import MetricsRepository
    
    try:
        async with get_repository_context(MetricsRepository) as metrics_repo:
            await metrics_repo.increment_metric(
                user_id=user_id,
                date=today,
                metric_name="messages_failed",
                increment=1
            )
    except Exception as e:
        logger.error(f"Error updating metrics for message_failed: {e}")

async def _handle_campaign_created(data: Dict[str, Any]) -> None:
    """Handle campaign created event."""
    user_id = data.get("user_id")
    if not user_id:
        return
    
    # Get today's date
    today = datetime.now(timezone.utc).date()
    
    # Update metrics
    from app.db.repositories.metrics import MetricsRepository
    
    try:
        async with get_repository_context(MetricsRepository) as metrics_repo:
            await metrics_repo.increment_metric(
                user_id=user_id,
                date=today,
                metric_name="campaigns_created",
                increment=1
            )
            
            # Increment active campaigns too
            await metrics_repo.increment_metric(
                user_id=user_id,
                date=today,
                metric_name="campaigns_active",
                increment=1
            )
    except Exception as e:
        logger.error(f"Error updating metrics for campaign_created: {e}")

async def _handle_campaign_completed(data: Dict[str, Any]) -> None:
    """Handle campaign completed event."""
    user_id = data.get("user_id")
    if not user_id:
        return
    
    # Get today's date
    today = datetime.now(timezone.utc).date()
    
    # Update metrics
    from app.db.repositories.metrics import MetricsRepository
    
    try:
        async with get_repository_context(MetricsRepository) as metrics_repo:
            await metrics_repo.increment_metric(
                user_id=user_id,
                date=today,
                metric_name="campaigns_completed",
                increment=1
            )
            
            # Decrement active campaigns
            await metrics_repo.increment_metric(
                user_id=user_id,
                date=today,
                metric_name="campaigns_active",
                increment=-1
            )
    except Exception as e:
        logger.error(f"Error updating metrics for campaign_completed: {e}")

async def _handle_template_created(data: Dict[str, Any]) -> None:
    """Handle template created event."""
    user_id = data.get("user_id")
    if not user_id:
        return
    
    # Get today's date
    today = datetime.now(timezone.utc).date()
    
    # Update metrics
    from app.db.repositories.metrics import MetricsRepository
    
    try:
        async with get_repository_context(MetricsRepository) as metrics_repo:
            await metrics_repo.increment_metric(
                user_id=user_id,
                date=today,
                metric_name="templates_created",
                increment=1
            )
    except Exception as e:
        logger.error(f"Error updating metrics for template_created: {e}")

async def _handle_template_used(data: Dict[str, Any]) -> None:
    """Handle template used event."""
    user_id = data.get("user_id")
    if not user_id:
        return
    
    # Get today's date
    today = datetime.now(timezone.utc).date()
    
    # Update metrics
    from app.db.repositories.metrics import MetricsRepository
    
    try:
        async with get_repository_context(MetricsRepository) as metrics_repo:
            await metrics_repo.increment_metric(
                user_id=user_id,
                date=today,
                metric_name="templates_used",
                increment=1
            )
    except Exception as e:
        logger.error(f"Error updating metrics for template_used: {e}")

async def get_user_metrics(
    user_id: str,
    period: str = "week"
) -> Dict[str, Any]:
    """
    Get metrics for a specific user.
    
    Args:
        user_id: User ID
        period: Time period ("day", "week", "month", "year")
        
    Returns:
        Dict[str, Any]: User metrics
    """
    from app.db.repositories.metrics import MetricsRepository
    
    # Calculate date range based on period
    end_date = datetime.now(timezone.utc).date()
    
    if period == "day":
        start_date = end_date
    elif period == "week":
        start_date = end_date - timedelta(days=7)
    elif period == "month":
        start_date = end_date - timedelta(days=30)
    elif period == "year":
        start_date = end_date - timedelta(days=365)
    else:
        # Default to week
        start_date = end_date - timedelta(days=7)
    
    # Get metrics
    async with get_repository_context(MetricsRepository) as metrics_repo:
        # Get summary metrics
        summary = await metrics_repo.get_summary_metrics(
            user_id=user_id,
            start_date=start_date,
            end_date=end_date
        )
        
        # Get daily metrics for charting
        metrics_list = await metrics_repo.get_metrics_range(
            user_id=user_id,
            start_date=start_date,
            end_date=end_date
        )
        
        # Format for response
        daily_data = []
        for metric in metrics_list:
            daily_data.append({
                "date": metric.date.isoformat(),
                "sent": metric.messages_sent,
                "delivered": metric.messages_delivered,
                "failed": metric.messages_failed
            })
        
        # Combine data
        result = {
            "summary": summary,
            "daily_data": daily_data,
            "period": period
        }
        
        return result

async def get_system_metrics() -> Dict[str, Any]:
    """
    Get system-wide metrics (for admin).
    
    Returns:
        Dict[str, Any]: System metrics formatted for SystemMetricsResponse
    """
    # For system metrics, we'll query the database directly
    from app.db.repositories.messages import MessageRepository
    from app.db.repositories.users import UserRepository
    from app.db.repositories.campaigns import CampaignRepository
    
    system_metrics = {
        "messages": {},
        "users": {},
        "campaigns": {}
    }
    
    # Query message stats
    async with get_repository_context(MessageRepository) as message_repo:
        from sqlalchemy import func, select
        from app.models.message import Message
        
        # Total messages
        total_query = select(func.count(Message.id))
        result = await message_repo.session.execute(total_query)
        total_messages = result.scalar_one_or_none() or 0
        
        # Messages by status
        from app.schemas.message import MessageStatus
        status_counts = {}
        for status in [MessageStatus.SENT, MessageStatus.DELIVERED, MessageStatus.FAILED]:
            status_query = select(func.count(Message.id)).where(Message.status == status)
            result = await message_repo.session.execute(status_query)
            status_counts[status] = result.scalar_one_or_none() or 0
        
        # Last 24 hours
        yesterday = datetime.now(timezone.utc) - timedelta(days=1)
        recent_query = select(func.count(Message.id)).where(Message.created_at >= yesterday)
        result = await message_repo.session.execute(recent_query)
        recent_messages = result.scalar_one_or_none() or 0
        
        system_metrics["messages"] = {
            "total": total_messages,
            "sent": status_counts.get(MessageStatus.SENT, 0),
            "delivered": status_counts.get(MessageStatus.DELIVERED, 0),
            "failed": status_counts.get(MessageStatus.FAILED, 0),
            "last_24h": recent_messages
        }
    
    # Query user stats
    async with get_repository_context(UserRepository) as user_repo:
        from app.models.user import User
        
        # Total users
        total_query = select(func.count(User.id))
        result = await user_repo.session.execute(total_query)
        total_users = result.scalar_one_or_none() or 0
        
        # Active users
        active_query = select(func.count(User.id)).where(User.is_active == True)
        result = await user_repo.session.execute(active_query)
        active_users = result.scalar_one_or_none() or 0
        
        # New users today
        today_start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
        new_today_query = select(func.count(User.id)).where(User.created_at >= today_start)
        result = await user_repo.session.execute(new_today_query)
        new_today = result.scalar_one_or_none() or 0
        
        system_metrics["users"] = {
            "total": total_users,
            "active": active_users,
            "new_today": new_today
        }
    
    # Query campaign stats
    async with get_repository_context(CampaignRepository) as campaign_repo:
        from app.models.campaign import Campaign
        
        # Total campaigns
        total_query = select(func.count(Campaign.id))
        result = await campaign_repo.session.execute(total_query)
        total_campaigns = result.scalar_one_or_none() or 0
        
        # Active campaigns
        active_query = select(func.count(Campaign.id)).where(Campaign.status == "active")
        result = await campaign_repo.session.execute(active_query)
        active_campaigns = result.scalar_one_or_none() or 0
        
        # Campaigns completed today
        today_start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
        completed_today_query = select(func.count(Campaign.id)).where(
            Campaign.completed_at >= today_start
        )
        result = await campaign_repo.session.execute(completed_today_query)
        completed_today = result.scalar_one_or_none() or 0
        
        system_metrics["campaigns"] = {
            "total": total_campaigns,
            "active": active_campaigns,
            "completed_today": completed_today
        }
    
    return system_metrics

async def schedule_metrics_update() -> None:
    """
    Schedule regular metrics updates.
    This function is meant to be run as a background task.
    """
    from app.db.repositories.metrics import MetricsRepository
    
    while True:
        try:
            # Calculate time until the next run (midnight UTC)
            now = datetime.now(timezone.utc)
            tomorrow = (now + timedelta(days=1)).replace(
                hour=0, minute=0, second=0, microsecond=0
            )
            seconds_until_midnight = (tomorrow - now).total_seconds()
            
            # Sleep until midnight
            logger.info(f"Metrics update scheduled for {tomorrow.isoformat()}")
            await asyncio.sleep(seconds_until_midnight)
            
            # Run the update for yesterday
            yesterday = now.date() - timedelta(days=1)
            logger.info(f"Running scheduled metrics update for {yesterday.isoformat()}")
            
            async with get_repository_context(MetricsRepository) as metrics_repo:
                updated_count = await metrics_repo.update_daily_metrics(day=yesterday)
                logger.info(f"Updated metrics for {updated_count} users")
                
        except Exception as e:
            logger.error(f"Error in scheduled metrics update: {e}")
            # Sleep for a while before retrying
            await asyncio.sleep(3600)  # 1 hour