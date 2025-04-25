# Message Template System - User Guide

## Overview

The message template system allows you to create reusable templates for your SMS messages. This is particularly useful when you need to send similar messages to multiple recipients with personalized content.

## Key Features

- Create and manage reusable message templates
- Support for variables using the `{{variable_name}}` syntax
- Preview how templates will look with specific variable values
- Send messages using templates with just a phone number and variable values
- Send batch messages using the same template with different variables for each recipient

## Creating Templates

### Via API

```http
POST /api/v1/templates
Content-Type: application/json
Authorization: Bearer YOUR_TOKEN

{
  "name": "OTP Notification",
  "content": "Your verification code is {{code}}. It will expire in {{minutes}} minutes.",
  "description": "Template for sending OTP codes",
  "is_active": true
}
```

The system will automatically detect variables in the format `{{variable_name}}` from your template content.

### Variable Format

Variables should be enclosed in double curly braces like `{{variable_name}}`. Variable names can contain letters, numbers, and underscores.

Examples:
- `{{code}}`
- `{{user_name}}`
- `{{order_123}}`

## Using Templates

### Previewing a Template

Before sending, you can preview how your template will look with specific variables:

```http
POST /api/v1/templates/apply?template_id=TEMPLATE_ID
Content-Type: application/json
Authorization: Bearer YOUR_TOKEN

{
  "variables": {
    "code": "123456",
    "minutes": "15"
  }
}
```

Response:
```json
{
  "result": "Your verification code is 123456. It will expire in 15 minutes.",
  "missing_variables": []
}
```

### Sending a Message with a Template

```http
POST /api/v1/templates/send
Content-Type: application/json
Authorization: Bearer YOUR_TOKEN

{
  "template_id": "TEMPLATE_ID",
  "phone_number": "+1234567890",
  "variables": {
    "code": "123456",
    "minutes": "15"
  },
  "scheduled_at": null,
  "custom_id": "otp-1234"
}
```

This will apply the variables to your template and send the resulting message.

### Batch Sending with Templates

For sending to multiple recipients with different variables:

```http
POST /api/v1/messages/batch
Content-Type: application/json
Authorization: Bearer YOUR_TOKEN

{
  "messages": [
    {
      "phone_number": "+1234567890",
      "message": "Your custom message using {{variable}} syntax",
      "custom_id": "batch-1"
    },
    {
      "phone_number": "+0987654321",
      "message": "Another message with {{different}} variable",
      "custom_id": "batch-2"
    }
  ],
  "options": {
    "delay_between_messages": 0.3,
    "fail_on_first_error": false
  }
}
```

## Managing Templates

### Listing Templates

```http
GET /api/v1/templates?active_only=true
Authorization: Bearer YOUR_TOKEN
```

### Getting a Specific Template

```http
GET /api/v1/templates/{template_id}
Authorization: Bearer YOUR_TOKEN
```

### Updating a Template

```http
PUT /api/v1/templates/{template_id}
Content-Type: application/json
Authorization: Bearer YOUR_TOKEN

{
  "name": "Updated OTP Template",
  "content": "Your code is {{code}}. Valid for {{minutes}} minutes.",
  "is_active": true
}
```

### Deleting a Template

```http
DELETE /api/v1/templates/{template_id}
Authorization: Bearer YOUR_TOKEN
```

## Best Practices

1. **Descriptive Variable Names**: Use clear, descriptive variable names that indicate what data should be inserted.

2. **Test Before Sending**: Always use the `/templates/apply` endpoint to test how your template will look with real data before sending messages.

3. **Handle Missing Variables**: Check the `missing_variables` field in responses to ensure all required variables are provided.

4. **Version Your Templates**: If you need to make significant changes to a template that's in use, consider creating a new version instead of updating the existing one.

5. **Keep Templates Simple**: Avoid complex formatting that might not render well on all mobile devices.

6. **Include Message Signature**: Consider including your company name or service identifier at the end of templates to help recipients identify the sender.

## Examples

### Appointment Reminder

```
Template Content:
"Hi {{name}}, this is a reminder for your appointment on {{date}} at {{time}}. Reply YES to confirm or call {{phone}} to reschedule."

Applied with:
{
  "name": "John",
  "date": "May 5, 2025",
  "time": "2:30 PM",
  "phone": "555-123-4567"
}

Result:
"Hi John, this is a reminder for your appointment on May 5, 2025 at 2:30 PM. Reply YES to confirm or call 555-123-4567 to reschedule."
```

### Order Confirmation

```
Template Content:
"Your order #{{order_id}} has been confirmed! Estimated delivery: {{delivery_date}}. Track your package at {{tracking_url}}. Thanks for shopping with {{company_name}}!"

Applied with:
{
  "order_id": "1234567",
  "delivery_date": "Apr 28-30, 2025",
  "tracking_url": "https://track.example.com/1234567",
  "company_name": "Example Shop"
}

Result:
"Your order #1234567 has been confirmed! Estimated delivery: Apr 28-30, 2025. Track your package at https://track.example.com/1234567. Thanks for shopping with Example Shop!"
```