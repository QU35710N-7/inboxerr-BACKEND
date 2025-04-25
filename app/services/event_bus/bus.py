"""
Enhanced event bus implementation for asynchronous messaging between components.
Improvements:
- Better lock handling
- Enhanced error handling and reporting
- Subscriber management
- Event batching support
- Proper subscriber cleanup
"""
import asyncio
import logging
import time
import uuid
from typing import Dict, List, Callable, Any, Set, Optional, Tuple
from datetime import datetime, timezone
from contextlib import asynccontextmanager

from app.services.event_bus.events import EventType, Event

logger = logging.getLogger("inboxerr.eventbus")


class EventBus:
    """
    Enhanced event bus for asynchronous messaging between components.
    
    Supports subscription to events, publishing events, and now includes:
    - Better lock handling for thread safety
    - Error propagation for subscribers
    - Event batching
    - Subscriber cleanup
    """
    
    def __init__(self):
        """Initialize the event bus."""
        self._subscribers: Dict[str, List[Tuple[str, Callable]]] = {}
        self._subscriber_ids: Dict[str, Set[str]] = {}
        self._lock = asyncio.Lock()
        self._initialized = False
        self._event_history: List[Dict[str, Any]] = []  # For debugging
        self._max_history = 100  # Maximum events to keep in history
        self._failed_deliveries: Dict[str, List[Dict[str, Any]]] = {}  # Failed event deliveries
    
    async def initialize(self) -> None:
        """Initialize the event bus."""
        if self._initialized:
            return
        
        logger.info("Initializing event bus")
        self._initialized = True
    
    async def shutdown(self) -> None:
        """Shutdown the event bus and clean up resources."""
        logger.info("Shutting down event bus")
        self._initialized = False
        
        # Clear subscribers
        async with self._lock:
            self._subscribers.clear()
            self._subscriber_ids.clear()
    
    @asynccontextmanager
    async def batch(self):
        """
        Context manager for batching multiple events.
        
        This allows multiple events to be published atomically.
        """
        # Create a batch container
        batch = []
        
        # Define the add_event function that will be used within the context
        async def add_event(event_type: str, data: Dict[str, Any]) -> None:
            batch.append((event_type, data))
        
        try:
            # Yield the add_event function for use within the context
            yield add_event
            
            # Process the batch after the context exits
            for event_type, data in batch:
                await self.publish(event_type, data)
                
        except Exception as e:
            logger.error(f"Error in event batch: {e}", exc_info=True)
            # Re-raise the exception after logging
            raise
    
    async def publish(self, event_type: str, data: Dict[str, Any]) -> bool:
        """
        Publish an event to subscribers.
        
        Args:
            event_type: Type of event
            data: Event data
            
        Returns:
            bool: True if event was successfully published
        """
        if not self._initialized:
            await self.initialize()
        
        subscribers = []
        subscriber_ids = []
        
        # Get subscribers with lock
        async with self._lock:
            if event_type in self._subscribers:
                subscribers = self._subscribers[event_type].copy()
                subscriber_ids = list(self._subscriber_ids[event_type])
        
        if not subscribers:
            logger.debug(f"No subscribers for event: {event_type}")
            return True
        
        # Add timestamp if not present
        if "timestamp" not in data:
            data["timestamp"] = datetime.now(timezone.utc).isoformat()
        
        # Add event type for reference
        data["event_type"] = event_type
        # Add unique event ID
        data["event_id"] = str(uuid.uuid4())
        
        # Keep history for debugging
        if len(self._event_history) >= self._max_history:
            self._event_history.pop(0)
        self._event_history.append({
            "event_type": event_type,
            "data": data,
            "subscribers": subscriber_ids,
            "timestamp": data["timestamp"]
        })
        
        # Execute callbacks outside of the lock
        logger.debug(f"Publishing event {event_type} to {len(subscribers)} subscribers")
        
        all_successful = True
        
        for subscriber_id, callback in subscribers:
            try:
                await callback(data)
            except asyncio.CancelledError:
                # Re-raise cancellation to allow proper task cleanup
                logger.warning(f"Subscriber {subscriber_id} was cancelled during event {event_type}")
                raise
            except Exception as e:
                logger.error(f"Error in subscriber {subscriber_id} for {event_type}: {e}", exc_info=True)
                
                # Record failed delivery
                if subscriber_id not in self._failed_deliveries:
                    self._failed_deliveries[subscriber_id] = []
                    
                self._failed_deliveries[subscriber_id].append({
                    "event_type": event_type,
                    "event_id": data["event_id"],
                    "error": str(e),
                    "timestamp": datetime.now(timezone.utc).isoformat()
                })
                
                # Limit failed deliveries history
                if len(self._failed_deliveries[subscriber_id]) > self._max_history:
                    self._failed_deliveries[subscriber_id].pop(0)
                
                all_successful = False
        
        return all_successful
    
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
        if subscriber_id is None:
            subscriber_id = f"{callback.__module__}.{callback.__name__}_{str(uuid.uuid4())[:8]}"
        
        async with self._lock:
            # Initialize event type if not exists
            if event_type not in self._subscribers:
                self._subscribers[event_type] = []
                self._subscriber_ids[event_type] = set()
            
            # Add subscriber if not already subscribed
            if subscriber_id not in self._subscriber_ids[event_type]:
                self._subscribers[event_type].append((subscriber_id, callback))
                self._subscriber_ids[event_type].add(subscriber_id)
                logger.info(f"Subscribed to {event_type}: {subscriber_id}")
            else:
                # Update callback for existing subscriber ID
                for i, (sid, _) in enumerate(self._subscribers[event_type]):
                    if sid == subscriber_id:
                        self._subscribers[event_type][i] = (subscriber_id, callback)
                        logger.debug(f"Updated subscriber callback for {event_type}: {subscriber_id}")
                        break
        
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
            
            # Find and remove the subscriber
            self._subscribers[event_type] = [
                (sid, callback) for sid, callback in self._subscribers[event_type]
                if sid != subscriber_id
            ]
            self._subscriber_ids[event_type].remove(subscriber_id)
            
            logger.info(f"Unsubscribed from {event_type}: {subscriber_id}")
            
            # Clean up failed deliveries for this subscriber
            if subscriber_id in self._failed_deliveries:
                del self._failed_deliveries[subscriber_id]
            
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
                # Check if subscriber exists for this event type
                if subscriber_id in self._subscriber_ids[event_type]:
                    # Remove from subscribers list
                    self._subscribers[event_type] = [
                        (sid, callback) for sid, callback in self._subscribers[event_type]
                        if sid != subscriber_id
                    ]
                    # Remove from subscriber IDs set
                    self._subscriber_ids[event_type].remove(subscriber_id)
                    count += 1
            
            # Clean up failed deliveries for this subscriber
            if subscriber_id in self._failed_deliveries:
                del self._failed_deliveries[subscriber_id]
        
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
    
    def get_event_history(self, limit: int = 10) -> List[Dict[str, Any]]:
        """
        Get recent event history for debugging.
        
        Args:
            limit: Maximum number of events to return
            
        Returns:
            List[Dict]: Recent events
        """
        return self._event_history[-limit:] if self._event_history else []
    
    def get_failed_deliveries(self, subscriber_id: Optional[str] = None) -> Dict[str, List[Dict[str, Any]]]:
        """
        Get failed event deliveries.
        
        Args:
            subscriber_id: Optional subscriber ID to filter by
            
        Returns:
            Dict: Failed deliveries by subscriber ID
        """
        if subscriber_id:
            return {subscriber_id: self._failed_deliveries.get(subscriber_id, [])}
        else:
            return self._failed_deliveries


# Singleton instance
_event_bus = EventBus()

def get_event_bus() -> EventBus:
    """Get the singleton event bus instance."""
    return _event_bus