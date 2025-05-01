"""
Database session management.
"""
import logging
from typing import AsyncGenerator, Type, Any

from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker

from app.core.config import settings
from app.db.base import Base

logger = logging.getLogger("inboxerr.db")

# Create async engine with optimized pool settings
engine = create_async_engine(
    settings.DATABASE_URL,
    echo=settings.DEBUG,
    future=True,
    # Enhanced pool settings for better concurrency
    pool_size=20,  # Increased from default to handle more connections
    max_overflow=30,  # Allow more connections during peak load
    pool_timeout=30,  # Longer timeout to prevent premature failures
    pool_recycle=3600,  # Recycle connections hourly
    pool_pre_ping=True  # Verify connections before use
)

# Create async session factory
async_session_factory = sessionmaker(
    engine, 
    class_=AsyncSession, 
    expire_on_commit=False,
    # Disable autoflush to prevent unexpected flushes during operations
    autoflush=False  
)


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """
    Dependency for getting async database session.
    
    Yields:
        AsyncSession: Database session
    """
    async with async_session_factory() as session:
        logger.debug("Database session created")
        try:
            yield session
            await session.commit()
            logger.debug("Database session committed")
        except Exception as e:
            await session.rollback()
            logger.error(f"Database session rolled back due to: {str(e)}")
            raise
        finally:
            await session.close()
            logger.debug("Database session closed")


async def initialize_database() -> None:
    """
    Initialize database by creating all tables.
    
    This should be called during application startup.
    """
    logger.info("Initializing database")
    async with engine.begin() as conn:
        # Create all tables
        await conn.run_sync(Base.metadata.create_all)
    logger.info("Database initialized successfully")


async def close_database_connections() -> None:
    """
    Close all database connections.
    
    This should be called during application shutdown.
    """
    logger.info("Closing database connections")
    await engine.dispose()
    logger.info("Database connections closed")


async def get_repository(repo_type: Type):
    """
    Get repository instance with improved session management.
    
    Creates a fresh session for each repository instance to prevent
    session conflicts in concurrent operations.
    
    Args:
        repo_type: Repository class
        
    Returns:
        Repository instance
    """
    # Create a new session directly (without context manager)
    # This allows the repository to manage the session lifecycle
    session = async_session_factory()
    return repo_type(session)


# Repository management functions
async def close_repository(repository: Any) -> None:
    """
    Close a repository's session.
    
    Args:
        repository: Repository instance
    """
    if hasattr(repository, 'session'):
        await repository.session.close()