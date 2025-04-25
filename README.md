# Inboxerr Backend

API backend for SMS management and delivery.

## Features

- ✅ Send individual and batch SMS messages
- ✅ Track message delivery status
- ✅ Import contacts from CSV
- ✅ Scheduled message delivery
- ✅ Webhook integration for real-time updates
- ✅ User authentication and API key management
- ✅ Message templates
- ✅ Comprehensive retry handling
- ✅ Event-driven architecture

## Technology Stack

- **Framework**: FastAPI
- **Database**: PostgreSQL with SQLAlchemy (async)
- **Authentication**: JWT and API keys
- **Containerization**: Docker & Docker Compose
- **API Documentation**: OpenAPI/Swagger
- **Testing**: pytest
- **SMS Gateway Integration**: Android SMS Gateway

## Getting Started

### Prerequisites

- Docker and Docker Compose
- Python 3.10+
- Android SMS Gateway credentials

### Installation

1. Clone the repository:
   ```bash
   git clone https://github.com/yourusername/inboxerr-backend.git
   cd inboxerr-backend
   ```

2. Copy the example environment file:
   ```bash
   cp .env.example .env
   ```
   
3. Update the `.env` file with your configuration:
   ```
   SMS_GATEWAY_URL=https://endpointnumber1.work.gd/api/3rdparty/v1
   SMS_GATEWAY_LOGIN=your_login
   SMS_GATEWAY_PASSWORD=your_password
   SECRET_KEY=your_secret_key
   ```

4. Start the application with Docker Compose:
   ```bash
   docker-compose up -d
   ```

5. Run database migrations:
   ```bash
   docker-compose exec api alembic upgrade head
   ```

6. Access the API at `http://localhost:8000/api/docs`

### Development Setup

For local development without Docker:

1. Create a virtual environment:
   ```bash
   python -m venv venv
   source venv/bin/activate  # On Windows: venv\Scripts\activate
   ```

2. Install dependencies:
   ```bash
   pip install -r requirements.txt
   pip install -r requirements-dev.txt
   ```

3. Set up environment variables:
   ```bash
   export DATABASE_URL=postgresql+asyncpg://postgres:postgres@localhost:5432/inboxerr
   export SMS_GATEWAY_URL=https://endpointnumber1.work.gd/api/3rdparty/v1
   export SMS_GATEWAY_LOGIN=your_login
   export SMS_GATEWAY_PASSWORD=your_password
   ```

4. Run the application:
   ```bash
   uvicorn app.main:app --reload
   ```

## API Endpoints

### Authentication

- `POST /api/v1/auth/token` - Get access token
- `POST /api/v1/auth/register` - Register new user
- `GET /api/v1/auth/me` - Get current user info
- `POST /api/v1/auth/keys` - Create API key
- `GET /api/v1/auth/keys` - List API keys

### Messages

- `POST /api/v1/messages/send` - Send a single message
- `POST /api/v1/messages/batch` - Send batch of messages
- `POST /api/v1/messages/import` - Import contacts and send messages
- `GET /api/v1/messages/{message_id}` - Get message details
- `GET /api/v1/messages` - List messages
- `PUT /api/v1/messages/{message_id}/status` - Update message status
- `DELETE /api/v1/messages/{message_id}` - Delete message

### Webhooks

- `GET /api/v1/webhooks` - List webhooks
- `POST /api/v1/webhooks` - Register webhook
- `DELETE /api/v1/webhooks/{webhook_id}` - Delete webhook
- `GET /api/v1/webhooks/logs` - Get webhook delivery logs

## Project Structure

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

## Usage Examples

### Sending a Single SMS

```python
import requests
import json

url = "http://localhost:8000/api/v1/messages/send"
headers = {
    "Authorization": "Bearer YOUR_ACCESS_TOKEN",
    "Content-Type": "application/json"
}
data = {
    "phone_number": "+1234567890",
    "message": "Hello from Inboxerr!"
}

response = requests.post(url, headers=headers, data=json.dumps(data))
print(response.json())
```

### Importing Contacts from CSV

```python
import requests

url = "http://localhost:8000/api/v1/messages/import"
headers = {
    "Authorization": "Bearer YOUR_ACCESS_TOKEN"
}
files = {
    "file": open("contacts.csv", "rb")
}
data = {
    "message_template": "Hello {{name}}, this is a test message!"
}

response = requests.post(url, headers=headers, files=files, data=data)
print(response.json())
```

# Inboxerr API Updates

## Message Template System

The Inboxerr API now includes a robust Message Template System, allowing you to:

- Create reusable templates with variable placeholders
- Apply variables to templates and preview the results
- Send messages using templates with personalized data
- Manage templates (create, update, delete, list)

### Getting Started with Templates

1. **Create a new template**:
   ```bash
   curl -X POST "http://localhost:8000/api/v1/templates" \
     -H "Authorization: Bearer YOUR_TOKEN" \
     -H "Content-Type: application/json" \
     -d '{
       "name": "Welcome Template",
       "content": "Hello {{name}}, welcome to our service!",
       "description": "Welcome message for new users"
     }'
   ```

2. **Send a message using a template**:
   ```bash
   curl -X POST "http://localhost:8000/api/v1/templates/send" \
     -H "Authorization: Bearer YOUR_TOKEN" \
     -H "Content-Type: application/json" \
     -d '{
       "template_id": "YOUR_TEMPLATE_ID",
       "phone_number": "+1234567890",
       "variables": {
         "name": "John"
       }
     }'
   ```

See the full [Message Template System User Guide](path/to/message-template-system-user-guide.md) for more details.

## Database Management

We've added tools to simplify database migration and setup:

### Generating Migrations

To generate a new migration after changing your models:

```bash
python scripts/generate_migration.py "Description of your changes"
```

### Setting Up a Test Database

To set up a test database with sample data:

```bash
python scripts/setup_test_db.py
```

This will:
1. Create a test database if it doesn't exist
2. Run all migrations
3. Create a test user and sample templates

Test user credentials:
- Email: test@example.com
- Password: Test1234!

## Contributing

Contributions are welcome! Please feel free to submit a Pull Request.

## License

This project is licensed under the MIT License - see the LICENSE file for details.