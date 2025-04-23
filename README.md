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
│   ├── /api/                  # API endpoints
│   ├── /core/                 # Core components
│   ├── /db/                   # Database related code
│   ├── /models/               # SQLAlchemy models
│   ├── /schemas/              # Pydantic schemas
│   ├── /services/             # Business logic
│   └── /utils/                # Utility functions
│
├── /alembic/                  # Database migrations
├── /tests/                    # Test suite
├── /scripts/                  # Utility scripts
├── docker-compose.yml         # Docker Compose configuration
├── Dockerfile                 # Docker build configuration
└── requirements.txt           # Python dependencies
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

## Contributing

Contributions are welcome! Please feel free to submit a Pull Request.

## License

This project is licensed under the MIT License - see the LICENSE file for details.