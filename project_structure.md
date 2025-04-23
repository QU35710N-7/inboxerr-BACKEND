# Inboxerr Backend Project Structure

```
/inboxerr-backend/
│
├── /app/                      # Application package
│   ├── __init__.py            # Package initializer
│   ├── main.py                # FastAPI application entry point
│   │
│   ├── /api/                  # API endpoints and routing
│   │   ├── __init__.py
│   │   ├── router.py          # Main API router
│   │   ├── /v1/               # API version 1
│   │   │   ├── __init__.py
│   │   │   ├── endpoints/     # API endpoints by resource
│   │   │   │   ├── __init__.py
│   │   │   │   ├── auth.py    # Authentication endpoints
│   │   │   │   ├── messages.py # SMS message endpoints
│   │   │   │   ├── webhooks.py # Webhook endpoints
│   │   │   │   └── metrics.py  # Metrics and reporting endpoints
│   │   │   └── dependencies.py # API-specific dependencies
│   │
│   ├── /core/                 # Core application components
│   │   ├── __init__.py
│   │   ├── config.py          # Application configuration
│   │   ├── security.py        # Security utilities (auth, encryption)
│   │   ├── events.py          # Event handlers for application lifecycle
│   │   └── exceptions.py      # Custom exception classes
│   │
│   ├── /db/                   # Database related code
│   │   ├── __init__.py
│   │   ├── base.py            # Base DB session setup
│   │   ├── session.py         # DB session management
│   │   └── repositories/      # Repository pattern implementations
│   │       ├── __init__.py
│   │       ├── base.py        # Base repository class
│   │       ├── messages.py    # Message repository
│   │       └── users.py       # User repository
│   │
│   ├── /models/               # Database models
│   │   ├── __init__.py
│   │   ├── base.py            # Base model class
│   │   ├── message.py         # SMS message model
│   │   ├── user.py            # User model
│   │   └── webhook.py         # Webhook model
│   │
│   ├── /schemas/              # Pydantic schemas for API
│   │   ├── __init__.py
│   │   ├── base.py            # Base schema
│   │   ├── message.py         # Message schemas
│   │   ├── user.py            # User schemas
│   │   ├── webhook.py         # Webhook schemas
│   │   └── metrics.py         # Metrics schemas
│   │
│   ├── /services/             # Business logic services
│   │   ├── __init__.py
│   │   ├── sms/               # SMS related services
│   │   │   ├── __init__.py
│   │   │   ├── sender.py      # SMS sender implementation
│   │   │   ├── validator.py   # Phone/message validation
│   │   │   └── gateway.py     # SMS gateway client
│   │   │
│   │   ├── event_bus/         # Event management
│   │   │   ├── __init__.py
│   │   │   ├── bus.py         # Event bus implementation
│   │   │   ├── events.py      # Event definitions
│   │   │   └── handlers/      # Event handlers
│   │   │       ├── __init__.py
│   │   │       ├── message_handlers.py
│   │   │       └── system_handlers.py
│   │   │
│   │   ├── webhooks/          # Webhook handling
│   │   │   ├── __init__.py
│   │   │   ├── handler.py     # Webhook processor
│   │   │   └── manager.py     # Webhook registration/management
│   │   │
│   │   └── metrics/           # Metrics collection
│   │       ├── __init__.py
│   │       └── collector.py   # Metrics collector
│   │
│   └── /utils/                # Utility functions and helpers
│       ├── __init__.py
│       ├── phone.py           # Phone number utilities
│       ├── logging.py         # Logging configuration
│       └── pagination.py      # Pagination utilities
│
├── /alembic/                  # Database migrations
│   ├── env.py                 # Alembic environment
│   ├── README                 # Alembic readme
│   ├── script.py.mako         # Migration script template
│   └── /versions/             # Migration scripts
│
├── /tests/                    # Test suite
│   ├── __init__.py
│   ├── conftest.py            # Test configuration and fixtures
│   ├── /unit/                 # Unit tests
│   │   ├── __init__.py
│   │   ├── /services/         # Tests for services
│   │   └── /api/              # Tests for API endpoints
│   └── /integration/          # Integration tests
│       ├── __init__.py
│       └── /api/              # API integration tests
│
├── /scripts/                  # Utility scripts
│   ├── seed_db.py             # Database seeding script
│   └── generate_keys.py       # Generate security keys
│
├── .env.example               # Example environment variables
├── .gitignore                 # Git ignore file
├── docker-compose.yml         # Docker Compose configuration
├── Dockerfile                 # Docker build configuration
├── pyproject.toml             # Python project metadata
├── requirements.txt           # Python dependencies
├── requirements-dev.txt       # Development dependencies
└── README.md                  # Project documentation
```