"""
Repository for metrics operations.
"""
from datetime import datetime, timezone, date, timedelta
from typing import List, Optional, Dict, Any, Tuple

from sqlalchemy import select, update, and_, func, desc
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.dialects.postgresql import insert

from app.db.repositories.base import BaseRepository
from app.models.metrics import UserMetrics


class MetricsRepository(BaseRepository[UserMetrics, Dict[str, Any], Dict[str, Any]]):
    """Repository for metrics operations."""
    
    def __init__(self, session: AsyncSession):
        """Initialize with session and UserMetrics model."""
        super().__init__(session=session, model=UserMetrics)
    
    async def get_or_create_for_day(
        self,
        *,
        user_id: str,
        day: date
    ) -> UserMetrics:
        """
        Get or create metrics for a specific user and day.
        
        Args:
            user_id: User ID
            day: Date for metrics
            
        Returns:
            UserMetrics: Metrics for the specified day
        """
        # Check if metrics exist for this user and day
        query = select(UserMetrics).where(
            and_(
                UserMetrics.user_id == user_id,
                UserMetrics.date == day
            )
        )
        result = await self.session.execute(query)
        metrics = result.scalar_one_or_none()
        
        if metrics:
            return metrics
        
        # Create new metrics if not found
        metrics = UserMetrics(
            user_id=user_id,
            date=day,
            messages_sent=0,
            messages_delivered=0,
            messages_failed=0,
            messages_scheduled=0,
            campaigns_created=0,
            campaigns_completed=0,
            campaigns_active=0,
            templates_created=0,
            templates_used=0,
            quota_total=1000,  # Default quota
            quota_used=0
        )
        
        self.session.add(metrics)
        
        return metrics
    
    async def increment_metric(
        self,
        *,
        user_id: str,
        date: date,
        metric_name: str,
        increment: int = 1
    ) -> Optional[UserMetrics]:
        """
        Increment a specific metric for a user on a specific day.
        
        Args:
            user_id: User ID
            date: Date for metrics
            metric_name: Name of metric to increment
            increment: Amount to increment by
            
        Returns:
            UserMetrics: Updated metrics or None if error
        """
        # Get or create metrics for this day
        metrics = await self.get_or_create_for_day(user_id=user_id, day=date)
        
        # Update specific metric
        if hasattr(metrics, metric_name):
            current_value = getattr(metrics, metric_name)
            setattr(metrics, metric_name, current_value + increment)
            
            # Also update quota_used if incrementing sent messages
            if metric_name == "messages_sent":
                metrics.quota_used += increment
            
            self.session.add(metrics)
            
            return metrics
        
        return None
    
    async def get_metrics_for_day(
        self,
        *,
        user_id: str,
        day: date
    ) -> Optional[UserMetrics]:
        """
        Get metrics for a specific user and day.
        
        Args:
            user_id: User ID
            day: Date for metrics
            
        Returns:
            UserMetrics: Metrics for the specified day or None if not found
        """
        query = select(UserMetrics).where(
            and_(
                UserMetrics.user_id == user_id,
                UserMetrics.date == day
            )
        )
        result = await self.session.execute(query)
        return result.scalar_one_or_none()
    
    async def get_metrics_range(
        self,
        *,
        user_id: str,
        start_date: date,
        end_date: date
    ) -> List[UserMetrics]:
        """
        Get metrics for a specific user over a date range.
        
        Args:
            user_id: User ID
            start_date: Start date (inclusive)
            end_date: End date (inclusive)
            
        Returns:
            List[UserMetrics]: List of metrics for the date range
        """
        query = select(UserMetrics).where(
            and_(
                UserMetrics.user_id == user_id,
                UserMetrics.date >= start_date,
                UserMetrics.date <= end_date
            )
        ).order_by(UserMetrics.date)
        
        result = await self.session.execute(query)
        return result.scalars().all()
    
    async def get_monthly_metrics(
        self,
        *,
        user_id: str,
        year: int,
        month: int
    ) -> List[UserMetrics]:
        """
        Get metrics for a specific user for a full month.
        
        Args:
            user_id: User ID
            year: Year
            month: Month (1-12)
            
        Returns:
            List[UserMetrics]: List of metrics for the month
        """
        # Calculate start and end date for the month
        start_date = date(year, month, 1)
        if month == 12:
            end_date = date(year + 1, 1, 1) - timedelta(days=1)
        else:
            end_date = date(year, month + 1, 1) - timedelta(days=1)
        
        return await self.get_metrics_range(
            user_id=user_id,
            start_date=start_date,
            end_date=end_date
        )
    
    async def get_summary_metrics(
        self,
        *,
        user_id: str,
        start_date: date,
        end_date: date
    ) -> Dict[str, Any]:
        """
        Get summarized metrics for a specific user over a date range.
        
        Args:
            user_id: User ID
            start_date: Start date (inclusive)
            end_date: End date (inclusive)
            
        Returns:
            Dict[str, Any]: Summarized metrics
        """
        # Get metrics for the date range
        metrics_list = await self.get_metrics_range(
            user_id=user_id,
            start_date=start_date,
            end_date=end_date
        )
        
        # Calculate summary metrics
        total_sent = sum(m.messages_sent for m in metrics_list)
        total_delivered = sum(m.messages_delivered for m in metrics_list)
        total_failed = sum(m.messages_failed for m in metrics_list)
        
        # Calculate delivery rate
        delivery_rate = 0
        if total_sent > 0:
            delivery_rate = (total_delivered / total_sent) * 100
        
        # Get latest quota information
        quota_used = metrics_list[-1].quota_used if metrics_list else 0
        quota_total = metrics_list[-1].quota_total if metrics_list else 1000
        
        # Create summary
        return {
            "period": {
                "start_date": start_date.isoformat(),
                "end_date": end_date.isoformat()
            },
            "messages": {
                "sent": total_sent,
                "delivered": total_delivered,
                "failed": total_failed,
                "delivery_rate": round(delivery_rate, 1)
            },
            "campaigns": {
                "created": sum(m.campaigns_created for m in metrics_list),
                "completed": sum(m.campaigns_completed for m in metrics_list),
                "active": metrics_list[-1].campaigns_active if metrics_list else 0
            },
            "templates": {
                "created": sum(m.templates_created for m in metrics_list),
                "used": sum(m.templates_used for m in metrics_list)
            },
            "quota": {
                "used": quota_used,
                "total": quota_total,
                "percent": round((quota_used / quota_total) * 100, 1) if quota_total > 0 else 0
            }
        }
    
    async def update_daily_metrics(self, day: Optional[date] = None) -> int:
        """
        Update metrics for all users for a specific day based on actual data.
        This is useful for background jobs that rebuild metrics.
        
        Args:
            day: Date to update metrics for (defaults to yesterday)
            
        Returns:
            int: Number of users updated
        """
        from app.db.repositories.messages import MessageRepository
        from app.db.repositories.campaigns import CampaignRepository
        from app.db.repositories.templates import TemplateRepository
        from app.db.repositories.users import UserRepository
        
        # Default to yesterday if no day provided
        if day is None:
            day = date.today() - timedelta(days=1)
        
        # Get all active users
        async with self.session.begin_nested():
            # Get all users
            query = select("*").select_from(UserRepository.model)
            result = await self.session.execute(query)
            users = result.fetchall()
            
            updates_count = 0
            
            # For each user, calculate and store metrics
            for user_row in users:
                user_id = user_row[0]  # Assuming id is the first column
                
                # Calculate message metrics
                msg_query = select(
                    MessageRepository.model.status,
                    func.count(MessageRepository.model.id)
                ).where(
                    and_(
                        MessageRepository.model.user_id == user_id,
                        MessageRepository.model.created_at >= datetime.combine(day, datetime.min.time()),
                        MessageRepository.model.created_at < datetime.combine(day + timedelta(days=1), datetime.min.time())
                    )
                ).group_by(MessageRepository.model.status)
                
                msg_result = await self.session.execute(msg_query)
                msg_counts = dict(msg_result.fetchall())
                
                # Calculate campaign metrics
                campaign_query = select(
                    CampaignRepository.model.status,
                    func.count(CampaignRepository.model.id)
                ).where(
                    CampaignRepository.model.user_id == user_id
                ).group_by(CampaignRepository.model.status)
                
                campaign_result = await self.session.execute(campaign_query)
                campaign_counts = dict(campaign_result.fetchall())
                
                # Calculate template metrics for the day
                template_created_query = select(
                    func.count(TemplateRepository.model.id)
                ).where(
                    and_(
                        TemplateRepository.model.user_id == user_id,
                        TemplateRepository.model.created_at >= datetime.combine(day, datetime.min.time()),
                        TemplateRepository.model.created_at < datetime.combine(day + timedelta(days=1), datetime.min.time())
                    )
                )
                
                template_created_result = await self.session.execute(template_created_query)
                templates_created = template_created_result.scalar_one_or_none() or 0
                
                # Get or create metrics for this day
                metrics = await self.get_or_create_for_day(user_id=user_id, day=day)
                
                # Update metrics with actual values
                metrics.messages_sent = msg_counts.get('sent', 0)
                metrics.messages_delivered = msg_counts.get('delivered', 0)
                metrics.messages_failed = msg_counts.get('failed', 0)
                metrics.messages_scheduled = msg_counts.get('scheduled', 0)
                
                metrics.campaigns_created = sum(1 for status, count in campaign_counts.items() 
                                            if campaign_counts.get('created_at') and campaign_counts['created_at'] >= day)
                metrics.campaigns_completed = campaign_counts.get('completed', 0) + campaign_counts.get('cancelled', 0)
                metrics.campaigns_active = campaign_counts.get('active', 0)
                
                metrics.templates_created = templates_created
                
                # Calculate quota used based on messages sent
                metrics.quota_used = msg_counts.get('sent', 0) + msg_counts.get('delivered', 0) + msg_counts.get('failed', 0)
                
                # Save updated metrics
                self.session.add(metrics)
                updates_count += 1
        
        
        return updates_count