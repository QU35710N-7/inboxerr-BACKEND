# Inboxerr Backend - Frontend Developer Setup

This guide will help frontend developers set up and interact with the Inboxerr backend API.

## Quick Start

1. Clone the repository
```bash
git clone https://github.com/your-org/inboxerr-backend.git
cd inboxerr-backend
```

2. Set up a virtual environment
```bash
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate
```

3. Install dependencies
```bash
pip install -r requirements.txt
```

4. Create a local config file
```bash
cp .env.example .env
```

5. Set up the database
```bash
# Make sure PostgreSQL is running on your system
# Create database
psql -U postgres -c "CREATE DATABASE inboxerr;"

# Run migrations
alembic upgrade head
```

6. Seed sample data for frontend development
```bash
python scripts/seed_frontend_data.py
```

7. Start the server
```bash
uvicorn app.main:app --reload
```

8. Access the API at http://localhost:8000/api/docs

## Sample Account

After running the seed script, you can use these credentials:
- Email: `test@example.com`
- Password: `Test1234!`

## Key Features Ready for Frontend Integration

- ✅ User authentication (JWT)
- ✅ Send individual and batch SMS messages
- ✅ Message templates with variable substitution
- ✅ Campaign management
- ✅ Message status tracking
- ✅ Webhook handling for status updates

## API Documentation

See the [Inboxerr API Frontend Developer Guide](FRONTEND_API_GUIDE.md) for complete documentation of all available endpoints.

## Mock SMS Gateway

For frontend development, the backend can operate without real SMS Gateway credentials. Messages will be processed normally but not actually sent:

1. In development mode, the backend will simulate sending messages
2. All webhook events can be manually triggered for testing
3. All message statuses can be updated through the API

## Using with Docker (Alternative)

If you prefer using Docker:

```bash
# Start all services
docker-compose up -d

# Seed sample data
docker-compose exec api python scripts/seed_frontend_data.py
```

## Testing Webhooks

To test webhook events for message status updates:

```bash
# Replace EVENT_TYPE with: sms:sent, sms:delivered, or sms:failed
curl -X POST http://localhost:8000/api/v1/webhooks/test/{EVENT_TYPE} \
  -H "Authorization: Bearer YOUR_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"message_id": "YOUR_MESSAGE_ID"}'
```

## Troubleshooting

- **Database connection issues**: Ensure PostgreSQL is running and credentials are correct in `.env`
- **Authentication errors**: Check that you're using the correct bearer token format
- **CORS errors**: Add your frontend URL to `BACKEND_CORS_ORIGINS` in `.env`

## Need Help?

Contact the backend team via:
- Slack: #inboxerr-backend
- Email: backend@inboxerr.com