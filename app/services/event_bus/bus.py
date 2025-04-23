"""
Event bus implementation for asynchronous messaging between components.
"""
import asyncio
import logging
from typing import Dict, List, Callable, Any, Set, Optional
from datetime import datetime

from app.services.event_bus.events import EventType

logger = logging.getLogger("inboxerr.eventbus")


class EventBus:
    """
    Event bus for asynchronous messaging between components.
    
    Supports subscription to events and publishing events.
    """
    
    def __init__(self):
        """Initialize the event bus."""
        self._subscribers: Dict[str, List[Callable]] = {}
        self._subscriber_ids: Dict[str, Set[str]] = {}
        self._lock = asyncio.Lock()
        self._initialized = False
    
    async def initialize(self) -> None:
        """Initialize the event bus."""
        if self._initialized:
            return
        
        logger.info("Initializing event bus")
        self._initialized = True
    
    async def publish(self, event_type: str, data: Dict[str, Any]) -> None:
        """
        Publish an event to subscribers.
        
        Args:
            event_type: Type of event
            data: Event data
        """
        if not self._initialized:
            await self.initialize()
        
        subscribers = []
        
        # Get subscribers with lock
        async with self._lock:
            if event_type in self._subscribers:
                subscribers = self._subscribers[event_type].copy()
        
        if not subscribers:
            logger.debug(f"No subscribers for event: {event_type}")
            return
        
        # Add timestamp if not present
        if "timestamp" not in data:
            data["timestamp"] = datetime.utcnow().isoformat()
        
        # Add event type for reference
        data["event_type"] = event_type
        
        # Execute callbacks outside of the lock
        logger.debug(f"Publishing event {event_type} to {len(subscribers)} subscribers")
        
        for callback in subscribers:
            try:
                await callback(data)
            except Exception as e:
                logger.error(f"Error in event handler for {event_type}: {e}", exc_info=True)
                # TODO: Consider adding retry logic for failed event handling
    
    async def subscribe(
        self,
        event_type: str,
        callback: Callable,
        subscriber_id: Optional[str] = None
    ) -> str:
        """
        Subscribe to an event type.
        
        Args:
            event_type: Event type to subscribe to
            callback: Function to call when event occurs
            subscriber_id: Optional subscriber ID
            
        Returns:
            str: Subscriber ID
        """
        if not self._initialized:
            await self.initialize()
        
        # Generate subscriber ID if not provided
        subscriber_id = subscriber_id or f"{callback.__module__}.{callback.__name__}"
        
        async with self._lock:
            # Initialize event type if not exists
            if event_type not in self._subscribers:
                self._subscribers[event_type] = []
                self._subscriber_ids[event_type] = set()
            
            # Add subscriber if not already subscribed
            if subscriber_id not in self._subscriber_ids[event_type]:
                self._subscribers[event_type].append(callback)
                self._subscriber_ids[event_type].add(subscriber_id)
                logger.info(f"Subscribed to {event_type}: {subscriber_id}")
            else:
                logger.debug(f"Already subscribed to {event_type}: {subscriber_id}")
        
        return subscriber_id
    
    async def unsubscribe(self, event_type: str, subscriber_id: str) -> bool:
        """
        Unsubscribe from an event type.
        
        Args:
            event_type: Event type to unsubscribe from
            subscriber_id: Subscriber ID
            
        Returns:
            bool: True if unsubscribed, False if not found
        """
        if not self._initialized:
            await self.initialize()
        
        async with self._lock:
            if event_type not in self._subscribers:
                return False
            
            if subscriber_id not in self._subscriber_ids[event_type]:
                return False
            
            # Find the index of the callback
            idx = list(self._subscriber_ids[event_type]).index(subscriber_id)
            
            # Remove the callback and ID
            self._subscribers[event_type].pop(idx)
            self._subscriber_ids[event_type].remove(subscriber_id)
            
            logger.info(f"Unsubscribed from {event_type}: {subscriber_id}")
            return True
    
    async def unsubscribe_all(self, subscriber_id: str) -> int:
        """
        Unsubscribe from all event types.
        
        Args:
            subscriber_id: Subscriber ID
            
        Returns:
            int: Number of subscriptions removed
        """
        if not self._initialized:
            await self.initialize()
        
        count = 0
        
        async with self._lock:
            for event_type in list(self._subscribers.keys()):
                if subscriber_id in self._subscriber_ids[event_type]:
                    # Find the index of the callback
                    idx = list(self._subscriber_ids[event_type]).index(subscriber_id)
                    
                    # Remove the callback and ID
                    self._subscribers[event_type].pop(idx)
                    self._subscriber_ids[event_type].remove(subscriber_id)
                    count += 1
        
        if count > 0:
            logger.info(f"Unsubscribed {subscriber_id} from {count} event types")
        
        return count
    
    def get_subscriber_count(self, event_type: Optional[str] = None) -> int:
        """
        Get the number of subscribers.
        
        Args:
            event_type: Optional event type to count subscribers for
            
        Returns:
            int: Number of subscribers
        """
        if event_type:
            return len(self._subscribers.get(event_type, []))
        else:
            return sum(len(subscribers) for subscribers in self._subscribers.values())


# Singleton instance
_event_bus = EventBus()

def get_event_bus() -> EventBus:
    """Get the singleton event bus instance."""
    return _event_bus