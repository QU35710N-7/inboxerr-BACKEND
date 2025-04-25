# Inboxerr API - Frontend Developer Guide

This document provides frontend developers with essential information for integrating with the Inboxerr backend API.

## Base URL

```
http://localhost:8000/api/v1
```

For production, this will be replaced with the actual deployment URL.

## Authentication

### Getting a Token

```
POST /auth/token
```

**Request Body:**
```json
{
  "username": "your-email@example.com",
  "password": "your-password"
}
```

**Response:**
```json
{
  "access_token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...",
  "token_type": "bearer",
  "expires_at": "2025-04-25T20:04:04.790Z"
}
```

### Using Authentication

Include the token in all subsequent requests:

```
Authorization: Bearer {access_token}
```

### Test User Credentials

For development, use the following credentials:

- Email: `test@example.com`
- Password: `Test1234!`

## Key Endpoints

### 1. Send a Single SMS

```
POST /messages/send
```

**Request Body:**
```json
{
  "phone_number": "+1234567890",
  "message": "Your message content",
  "scheduled_at": null,
  "custom_id": "optional-tracking-id"
}
```

### 2. Send Batch Messages

```
POST /messages/batch
```

**Request Body:**
```json
{
  "messages": [
    {
      "phone_number": "+1234567890",
      "message": "Message for recipient 1",
      "scheduled_at": null
    },
    {
      "phone_number": "+9876543210",
      "message": "Message for recipient 2",
      "scheduled_at": null
    }
  ],
  "options": {
    "delay_between_messages": 0.3,
    "fail_on_first_error": false
  }
}
```

### 3. List Messages

```
GET /messages?skip=0&limit=20
```

Optional query parameters:
- `status` - Filter by message status (pending, sent, delivered, failed)
- `phone_number` - Filter by phone number
- `from_date` - Filter by date (ISO format)
- `to_date` - Filter by date (ISO format)

### 4. Message Templates

#### Create Template

```
POST /templates
```

**Request Body:**
```json
{
  "name": "Welcome Template",
  "content": "Hello {{name}}, welcome to our service!",
  "description": "Welcome message for new users"
}
```

#### Send Using Template

```
POST /templates/send
```

**Request Body:**
```json
{
  "template_id": "template-uuid",
  "phone_number": "+1234567890",
  "variables": {
    "name": "John"
  }
}
```

### 5. Campaigns

#### Create Campaign

```
POST /campaigns
```

**Request Body:**
```json
{
  "name": "Marketing Campaign",
  "description": "Product launch campaign",
  "scheduled_start_at": "2025-05-01T09:00:00Z",
  "scheduled_end_at": "2025-05-01T18:00:00Z"
}
```

#### Start Campaign

```
POST /campaigns/{campaign_id}/start
```

## Status Codes

- `200` - Success
- `201` - Created
- `202` - Accepted (for async processing)
- `400` - Bad request
- `401` - Unauthorized
- `403` - Forbidden
- `404` - Not found
- `422` - Validation error
- `429` - Rate limit exceeded
- `500` - Server error

## Error Format

All API errors follow this format:

```json
{
  "status": "error",
  "code": "ERROR_CODE",
  "message": "Human-readable error message",
  "details": {}
}
```

## Pagination

Endpoints that return lists support pagination:

```
GET /messages?page=1&limit=20
```

Response includes pagination info:

```json
{
  "items": [...],
  "page_info": {
    "current_page": 1,
    "total_pages": 5,
    "page_size": 20,
    "total_items": 100,
    "has_previous": false,
    "has_next": true
  }
}
```

## Message Status Flow

Messages follow this status flow:

1. `pending` - Initial state when created
2. `scheduled` - For future delivery
3. `processed` - Submitted to SMS gateway
4. `sent` - Accepted by the carrier
5. `delivered` - Confirmed delivery to recipient
6. `failed` - Failed to deliver

## Webhooks

For development, you can test webhook events using:

```
GET /webhooks/test/{event_type}
```

Where `event_type` can be:
- `sms:sent`
- `sms:delivered`
- `sms:failed`

## Rate Limits

- Message sending: 60 requests per minute
- Batch operations: 10 requests per minute
- Template operations: 100 requests per minute

## Development Notes

- Phone numbers should be in E.164 format (e.g., +1234567890)
- Messages longer than 160 characters will be sent as multi-part SMS
- SMS templates support variable substitution using `{{variable_name}}` syntax