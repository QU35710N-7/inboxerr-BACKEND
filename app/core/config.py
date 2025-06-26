"""
Application settings and configuration management.
"""
from typing import List, Optional, Union
from pydantic import AnyHttpUrl, validator, field_validator
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """Application settings."""
    # Base
    PROJECT_NAME: str = "Inboxerr Backend"
    PROJECT_DESCRIPTION: str = "API backend for SMS management and delivery"
    VERSION: str = "0.1.0"
    API_PREFIX: str = "/api/v1"
    DEBUG: bool = False
    
    # CORS
    BACKEND_CORS_ORIGINS: List[Union[str, AnyHttpUrl]] = []

    @field_validator("BACKEND_CORS_ORIGINS", mode='before')
    def assemble_cors_origins(cls, v: Union[str, List[str]]) -> Union[List[str], str]:
        """Parse CORS origins from string or list."""
        if isinstance(v, str) and not v.startswith("["):
            return [i.strip() for i in v.split(",")]
        elif isinstance(v, (list, str)):
            return v
        raise ValueError(v)
    
    # Authentication
    SECRET_KEY: str = "CHANGEME_IN_PRODUCTION"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60 * 24  # 1 day
    API_KEY_HEADER: str = "X-API-Key"

    # Database
    DATABASE_URL: str = "postgresql+asyncpg://postgres:admin@localhost:5432/inboxerr"
    
    # SMS Gateway
    SMS_GATEWAY_URL: str = "https://endpointnumber1.work.gd/api/3rdparty/v1"
    SMS_GATEWAY_LOGIN: str = ""
    SMS_GATEWAY_PASSWORD: str = ""
    
    # Webhook
    API_BASE_URL: str = "http://localhost:8000"  # Base URL for webhooks
    WEBHOOK_SIGNATURE_KEY: Optional[str] = None
    WEBHOOK_TIMESTAMP_TOLERANCE: int = 300  # 5 minutes
    
    # SMS Processing
    BATCH_SIZE: int = 100
    DELAY_BETWEEN_SMS: float = 0.3  # seconds
    RETRY_ENABLED: bool = False
    RETRY_MAX_ATTEMPTS: int = 3
    RETRY_INTERVAL_SECONDS: int = 60


    # Virtual Campaign Sender Settings (Production Optimized)
    VIRTUAL_SENDER_MAX_CONCURRENT: int = 2  # Conservative for production
    VIRTUAL_SENDER_MICRO_BATCH_SIZE: int = 10  # Process 10 contacts at a time
    VIRTUAL_SENDER_RATE_LIMIT_DELAY: float = 0.2  # Delay between sends
    VIRTUAL_SENDER_CIRCUIT_BREAKER_THRESHOLD: int = 5  # Failures before opening circuit
    VIRTUAL_SENDER_CIRCUIT_BREAKER_TIMEOUT: int = 60  # Seconds before retry
    VIRTUAL_SENDER_MAX_RETRIES: int = 3  # Max retries per contact
    VIRTUAL_SENDER_DB_POOL_LIMIT: int = 20  # Max DB connections for virtual sender
    
    # Metrics
    METRICS_ENABLED: bool = True

    # Logging
    LOG_LEVEL: str = "INFO"
    
    class Config:
        """Pydantic config."""
        case_sensitive = True
        env_file = ".env"

    # Mock settings for testing purposes
    SMS_GATEWAY_MOCK: bool = True  # For development and testing

# Create singleton settings instance
settings = Settings()