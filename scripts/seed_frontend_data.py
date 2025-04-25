#!/usr/bin/env python
"""
Seed database with sample data for frontend development.

This script creates sample users, templates, messages and campaigns
to make frontend development easier.

Usage:
    python scripts/seed_frontend_data.py
"""
import asyncio
import sys
import os
from pathlib import Path
from datetime import datetime, timedelta, timezone
import random
import uuid

# Add parent directory to path
sys.path.append(str(Path(__file__).parent.parent))

# Import app modules
from app.db.session import initialize_database, async_session_factory
from app.db.repositories.users import UserRepository
from app.db.repositories.templates import TemplateRepository
from app.db.repositories.messages import MessageRepository
from app.db.repositories.campaigns import CampaignRepository
from app.core.security import get_password_hash
from app.schemas.message import MessageStatus

# Sample phone numbers
PHONE_NUMBERS = [
    "+12025550108", "+12025550112", "+12025550118", "+12025550121",
    "+12025550125", "+12025550132", "+12025550139", "+12025550144",
    "+12025550152", "+12025550158", "+12025550165", "+12025550171"
]

# Sample message templates
TEMPLATES = [
    {
        "name": "Welcome Message",
        "content": "Hi {{name}}, welcome to our service! We're glad you've joined us.",
        "description": "Template for welcoming new users"
    },
    {
        "name": "OTP Verification",
        "content": "Your verification code is {{code}}. It will expire in {{minutes}} minutes.",
        "description": "Template for sending OTP codes"
    },
    {
        "name": "Appointment Reminder",
        "content": "Hi {{name}}, this is a reminder for your appointment on {{date}} at {{time}}. Reply YES to confirm or call {{phone}} to reschedule.",
        "description": "Template for appointment reminders"
    },
    {
        "name": "Order Confirmation",
        "content": "Your order #{{order_id}} has been confirmed! Estimated delivery: {{delivery_date}}. Track your package at {{tracking_url}}",
        "description": "Order confirmation message"
    },
    {
        "name": "Payment Reminder",
        "content": "Reminder: Your payment of ${{amount}} is due on {{due_date}}. Please ensure your account has sufficient funds.",
        "description": "Payment reminder notification"
    }
]

# Sample messages
MESSAGES = [
    "Your verification code is 123456",
    "Your appointment is confirmed for tomorrow at 10:00 AM",
    "Your order #12345 has been shipped and will arrive on Friday",
    "Thank you for your payment of $99.99",
    "Your subscription will renew on May 15, 2025",
    "Your account password has been reset successfully",
    "Your flight PO491 has been delayed by 30 minutes",
    "Your table reservation at Milano Restaurant is confirmed",
    "Your prescription is ready for pickup at Central Pharmacy",
    "Reminder: You have a meeting scheduled in 1 hour"
]

# Sample campaign names
CAMPAIGN_NAMES = [
    "Spring Sale Promotion",
    "Customer Feedback Survey",
    "Product Launch Announcement",
    "Abandoned Cart Reminder",
    "Loyalty Program Update"
]

async def create_test_user():
    """Create a test user if it doesn't exist."""
    async with async_session_factory() as session:
        # Create user repository
        user_repo = UserRepository(session)
        
        # Check if user exists
        existing_user = await user_repo.get_by_email("test@example.com")
        if existing_user:
            print(f"✅ Test user test@example.com already exists")
            return existing_user
        
        # Create user
        hashed_password = get_password_hash("Test1234!")
        user = await user_repo.create(
            email="test@example.com",
            hashed_password=hashed_password,
            full_name="Test User",
            role="user"
        )
        
        print(f"✅ Created test user: {user.email}")
        return user

async def create_test_templates(user_id):
    """Create test message templates."""
    async with async_session_factory() as session:
        # Create template repository
        template_repo = TemplateRepository(session)
        
        created_templates = []
        # Create templates
        for template_data in TEMPLATES:
            template = await template_repo.create_template(
                name=template_data["name"],
                content=template_data["content"],
                description=template_data["description"],
                user_id=user_id
            )
            created_templates.append(template)
            print(f"✅ Created template: {template_data['name']}")
        
        return created_templates

async def create_test_messages(user_id, template_id=None):
    """Create test messages with different statuses."""
    async with async_session_factory() as session:
        # Create message repository
        message_repo = MessageRepository(session)
        
        # Generate different message statuses
        statuses = [
            MessageStatus.PENDING,
            MessageStatus.SENT,
            MessageStatus.DELIVERED,
            MessageStatus.FAILED,
            MessageStatus.SCHEDULED
        ]
        
        created_messages = []
        # Create messages with different statuses
        for i, message_text in enumerate(MESSAGES):
            phone = random.choice(PHONE_NUMBERS)
            status = statuses[i % len(statuses)]
            
            # For scheduled messages, set a future time
            scheduled_at = None
            if status == MessageStatus.SCHEDULED:
                scheduled_at = datetime.now(timezone.utc) + timedelta(days=1)
            
            # Create message
            message = await message_repo.create_message(
                phone_number=phone,
                message_text=message_text,
                user_id=user_id,
                custom_id=f"sample-{uuid.uuid4().hex[:8]}",
                scheduled_at=scheduled_at,
                metadata={"sample": True, "template_id": template_id}
            )
            
            # If not scheduled, update to the appropriate status
            if status != MessageStatus.SCHEDULED and status != MessageStatus.PENDING:
                # Update message status
                await message_repo.update_message_status(
                    message_id=message.id,
                    status=status,
                    event_type="seeded_data",
                    reason="Sample data" if status == MessageStatus.FAILED else None,
                    gateway_message_id=f"gw-{uuid.uuid4()}" if status != MessageStatus.PENDING else None
                )
            
            created_messages.append(message)
            print(f"✅ Created message with status {status}: {message_text[:30]}...")
        
        return created_messages

async def create_test_campaigns(user_id):
    """Create test campaigns."""
    async with async_session_factory() as session:
        # Create campaign repository
        campaign_repo = CampaignRepository(session)
        message_repo = MessageRepository(session)
        
        created_campaigns = []
        # Create campaigns with different statuses
        statuses = ["draft", "active", "paused", "completed", "cancelled"]
        
        for i, name in enumerate(CAMPAIGN_NAMES):
            status = statuses[i % len(statuses)]
            
            # Create campaign
            campaign = await campaign_repo.create_campaign(
                name=name,
                description=f"Sample campaign: {name}",
                user_id=user_id,
                scheduled_start_at=datetime.now(timezone.utc) + timedelta(days=1),
                scheduled_end_at=datetime.now(timezone.utc) + timedelta(days=2),
                settings={"sample": True}
            )
            
            # Add 3-5 messages to each campaign
            msg_count = random.randint(3, 5)
            for j in range(msg_count):
                phone = random.choice(PHONE_NUMBERS)
                message_text = f"Campaign {name}: {random.choice(MESSAGES)}"
                
                await message_repo.create_message(
                    phone_number=phone,
                    message_text=message_text,
                    user_id=user_id,
                    campaign_id=campaign.id,
                    metadata={"campaign": name}
                )
            
            # Update campaign stats
            campaign.total_messages = msg_count
            
            # Update campaign status
            if status != "draft":
                await campaign_repo.update_campaign_status(
                    campaign_id=campaign.id,
                    status=status,
                    started_at=datetime.now(timezone.utc) - timedelta(days=1) if status != "draft" else None,
                    completed_at=datetime.now(timezone.utc) if status in ["completed", "cancelled"] else None
                )
                
                # Update stats for non-draft campaigns
                if status in ["active", "paused", "completed"]:
                    sent = msg_count if status in ["completed"] else random.randint(1, msg_count)
                    delivered = random.randint(0, sent) if status in ["completed"] else 0
                    failed = random.randint(0, msg_count - sent) if status in ["completed"] else 0
                    
                    await campaign_repo.update_campaign_stats(
                        campaign_id=campaign.id,
                        increment_sent=sent,
                        increment_delivered=delivered,
                        increment_failed=failed
                    )
            
            created_campaigns.append(campaign)
            print(f"✅ Created campaign with status {status}: {name}")
        
        return created_campaigns

async def seed_database():
    """Seed database with sample data."""
    print("🌱 Seeding database with frontend development data...")
    
    # Initialize database
    await initialize_database()
    
    # Create test user
    user = await create_test_user()
    
    # Create templates
    templates = await create_test_templates(user.id)
    
    # Create messages
    if templates:
        await create_test_messages(user.id, templates[0].id)
    else:
        await create_test_messages(user.id)
    
    # Create campaigns
    await create_test_campaigns(user.id)
    
    print("\n✅ Database seeded successfully with frontend development data!")
    print(f"📱 Test user: test@example.com")
    print(f"🔑 Password: Test1234!")

if __name__ == "__main__":
    asyncio.run(seed_database())