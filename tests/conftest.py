import asyncio
import os
from datetime import datetime
from collections.abc import AsyncGenerator

import pytest
import pytest_asyncio
from httpx import AsyncClient
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

from app.main import app
from app.db.repositories.users import UserRepository
from app.db.repositories.templates import TemplateRepository
from app.core.security import get_password_hash
from app.models import base as models
from app.schemas.user import User
from app.api.v1 import dependencies
from app.models.user import User as UserModel


# Use test DB from env or fallback to SQLite
TEST_DATABASE_URL = os.getenv("DATABASE_URL", "sqlite+aiosqlite:///:memory:")
async_engine = create_async_engine(TEST_DATABASE_URL, echo=False, future=True)
async_session_factory = sessionmaker(async_engine, class_=AsyncSession, expire_on_commit=False)

TEST_USER = {
    "email": "test@example.com",
    "password": "Test1234!",
    "full_name": "Test User",
    "role": "user"
}

TEST_TEMPLATES = [
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
    }
]

@pytest_asyncio.fixture(autouse=True)
def override_auth():
    def fake_user():
        return User(
            id="test-user-id",
            email="test@example.com",
            is_active=True,
            is_superuser=False,
            created_at=datetime.utcnow(),
            updated_at=datetime.utcnow()
        )
    app.dependency_overrides[dependencies.get_current_user] = lambda: fake_user()
    yield
    app.dependency_overrides.clear()

@pytest.fixture(scope="session", autouse=True)
def event_loop():
    loop = asyncio.get_event_loop_policy().new_event_loop()
    yield loop
    loop.close()

@pytest_asyncio.fixture(scope="session", autouse=True)
async def initialize_test_db():
    print("⚙️  Initializing test DB and seeding data...")
    async with async_engine.begin() as conn:
        await conn.run_sync(models.Base.metadata.drop_all)
        await conn.run_sync(models.Base.metadata.create_all)

    async with async_session_factory() as session:
        user_repo = UserRepository(session)
        existing_user = await user_repo.get_by_email(TEST_USER["email"])
        if not existing_user:
            user = UserModel(
                id="test-user-id",
                email=TEST_USER["email"],
                hashed_password=get_password_hash(TEST_USER["password"]),
                full_name=TEST_USER["full_name"],
                role=TEST_USER["role"],
                created_at=datetime.utcnow(),
                updated_at=datetime.utcnow()
            )
            session.add(user)
        else:
            user = existing_user

        template_repo = TemplateRepository(session)
        for template in TEST_TEMPLATES:
            await template_repo.create_template(
                name=template["name"],
                content=template["content"],
                description=template["description"],
                user_id=user.id
            )

        await session.commit()
    
            # --- Add existing message and task for tests that depend on them ---
        from app.db.repositories.messages import MessageRepository
        from datetime import timezone

        now = datetime.now(timezone.utc)
        message_repo = MessageRepository(session)


        await message_repo.create_message(
            phone_number="+1234567890",
            message_text="Test message seeded for unit tests",
            user_id=user.id,
            custom_id="existing-msg-id",
            metadata={}
        )


        # Create a batch using repository logic
        batch = await message_repo.create_batch(
            user_id=user.id,
            name="Test Batch",
            total=1  # One message in batch
        )
        # Override ID for the test that expects it
        batch.id = "existing-task-id"

        # Add a message linked to this batch
        await message_repo.create_message(
            phone_number="+1234567890",
            message_text="Hello from seeded batch",
            user_id=user.id,
            custom_id="msg-in-batch",
            metadata={"batch_id": batch.id}
        )

        await session.commit()

    print(f"✅ Test DB seeded at {TEST_DATABASE_URL}")

@pytest_asyncio.fixture()
async def async_client() -> AsyncGenerator[AsyncClient, None]:
    from httpx import ASGITransport
    transport = ASGITransport(app=app)
    async with AsyncClient(base_url="http://testserver", transport=transport) as client:
        yield client

@pytest_asyncio.fixture()
async def db_session() -> AsyncGenerator[AsyncSession, None]:
    async with async_session_factory() as session:
        yield session
