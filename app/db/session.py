"""
Database session management.
"""
# app/db/session.py
import logging
from contextlib import asynccontextmanager
from typing import AsyncGenerator, Any, Type, TypeVar

from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker
from fastapi import Depends

from app.core.config import settings
from app.db.base import Base

logger = logging.getLogger("inboxerr.db")

# Create async engine with optimized pool settings
engine = create_async_engine(
    settings.DATABASE_URL,
    echo=settings.DEBUG,
    # Enhanced pool settings for better concurrency
    pool_size=20,
    max_overflow=30,
    pool_timeout=30,
    pool_recycle=3600,
    pool_pre_ping=True
)

# Create async session factory
async_session_factory = sessionmaker(
    engine, class_=AsyncSession, expire_on_commit=False, autoflush=False
)

# Context manager for database sessions
@asynccontextmanager
async def get_session() -> AsyncGenerator[AsyncSession, None]:
    """
    Context manager for database sessions.
    
    Automatically handles commit/rollback and ensures session is closed.
    
    Usage:
        async with get_session() as session:
            # Use session here
    """
    session = async_session_factory()
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

# Dependency function for FastAPI endpoints
async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """
    Get a database session for FastAPI endpoints via dependency injection.
    """
    async with get_session() as session:
        yield session

# Create a type variable for repository types
T = TypeVar('T')

# Factory function for repositories
def get_repository_factory(repo_type: Type[T]):
    """
    Create a repository factory for use with FastAPI dependency injection.
    
    Usage:
        @router.get("/")
        async def endpoint(repo = Depends(get_repository_factory(UserRepository))):
            # Use repo here
    """
    async def _get_repo(session: AsyncSession = Depends(get_db)) -> T:
        return repo_type(session)
    return _get_repo

# Context manager for repositories
@asynccontextmanager
async def get_repository_context(repo_type: Type[T]) -> AsyncGenerator[T, None]:
    """
    Get a repository with managed session lifecycle.
    
    Usage:
        async with get_repository_context(UserRepository) as repo:
            # Use repo here
    """
    async with get_session() as session:
        yield repo_type(session)

# Legacy function for backward compatibility
async def get_repository(repo_type: Type[T]) -> T:
    """
    Legacy repository factory (for backward compatibility).
    Warning: Session must be manually closed when using this function.
    
    This will be deprecated - use get_repository_context instead.
    """
    logger.warning(
        "Using deprecated get_repository function - session won't be automatically closed. "
        "Consider using get_repository_context instead."
    )
    session = async_session_factory()
    return repo_type(session)

async def initialize_database() -> None:
    """
    Initialize the database connection pool and run any startup tasks.
    
    This should be called during application startup.
    """
    logger.info("Initializing database connection pool")
    
    # Test database connection
    async with get_session() as session:
        try:
            # Use text() for raw SQL queries
            from sqlalchemy import text
            query = text("SELECT 1")
            await session.execute(query)
            logger.info("Database connection successful")
        except Exception as e:
            logger.error(f"Database connection failed: {e}")
            raise
            
    logger.info("Database initialization complete")


async def close_database_connections() -> None:
    """
    Close all database connections in the pool.
    
    This should be called during application shutdown.
    """
    logger.info("Closing database connections")
    
    # Dispose the engine to close all connections in the pool
    await engine.dispose()
    
    logger.info("Database connections closed")