#!/usr/bin/env python
"""
Setup a test database for development.

This script:
1. Checks if the test database exists
2. Creates it if it doesn't
3. Runs all migrations
4. Seeds it with test data

Usage:
    python scripts/setup_test_db.py
"""
import sys
import os
import asyncio
import subprocess
from pathlib import Path
import re

# Add parent directory to path to allow importing app
sys.path.append(str(Path(__file__).parent.parent))

# Import app modules
from app.core.config import settings
from app.db.session import initialize_database
from app.db.repositories.users import UserRepository
from app.db.repositories.templates import TemplateRepository
from app.core.security import get_password_hash

# Test data to seed
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

# Extract database connection info from the URL
def parse_db_url(url):
    """Parse database URL to extract connection information."""
    # PostgreSQL URL format: postgresql+asyncpg://user:password@host:port/dbname
    pattern = r"postgresql(?:\+asyncpg)?://([^:]+):([^@]+)@([^:]+):(\d+)/([^?]+)"
    match = re.match(pattern, url)
    
    if match:
        return {
            "user": match.group(1),
            "password": match.group(2),
            "host": match.group(3),
            "port": match.group(4),
            "dbname": match.group(5)
        }
    return None

async def create_test_user():
    """Create a test user if it doesn't exist."""
    from app.db.session import async_session_factory
    
    async with async_session_factory() as session:
        # Create user repository
        user_repo = UserRepository(session)
        
        # Check if user exists
        existing_user = await user_repo.get_by_email(TEST_USER["email"])
        if existing_user:
            print(f"✅ Test user {TEST_USER['email']} already exists")
            return existing_user
        
        # Create user
        hashed_password = get_password_hash(TEST_USER["password"])
        user = await user_repo.create(
            email=TEST_USER["email"],
            hashed_password=hashed_password,
            full_name=TEST_USER["full_name"],
            role=TEST_USER["role"]
        )
        
        print(f"✅ Created test user: {user.email}")
        return user

async def create_test_templates(user_id):
    """Create test message templates."""
    from app.db.session import async_session_factory
    
    async with async_session_factory() as session:
        # Create template repository
        template_repo = TemplateRepository(session)
        
        # Create templates
        for template_data in TEST_TEMPLATES:
            await template_repo.create_template(
                name=template_data["name"],
                content=template_data["content"],
                description=template_data["description"],
                user_id=user_id
            )
            print(f"✅ Created template: {template_data['name']}")

async def setup_database():
    """Setup the test database."""
    try:
        # Get database configuration from settings
        db_info = parse_db_url(settings.DATABASE_URL)
        if not db_info:
            print("❌ Could not parse database URL. Please check the format.")
            return False
        
        # Extract database name and create connection to postgres database
        db_name = db_info["dbname"]
        postgres_url = f"postgresql://{db_info['user']}:{db_info['password']}@{db_info['host']}:{db_info['port']}/postgres"
        
        # Get environment variables for PGPASSWORD to avoid password prompt
        env = os.environ.copy()
        env["PGPASSWORD"] = db_info["password"]
        
        # Test connection using psql
        connection_test = subprocess.run(
            ["psql", 
             "-h", db_info["host"], 
             "-p", db_info["port"], 
             "-U", db_info["user"], 
             "-d", "postgres", 
             "-c", "SELECT 1;"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env
        )
        
        if connection_test.returncode != 0:
            print("❌ Could not connect to PostgreSQL server.")
            print(connection_test.stderr.decode())
            print("Please make sure PostgreSQL is running and the credentials are correct.")
            return False
        
        # Check if database exists
        check_db = subprocess.run(
            ["psql", 
             "-h", db_info["host"], 
             "-p", db_info["port"], 
             "-U", db_info["user"], 
             "-d", "postgres", 
             "-c", f"SELECT 1 FROM pg_database WHERE datname = '{db_name}';"],
            stdout=subprocess.PIPE,
            env=env
        )
        
        if "1 row" not in check_db.stdout.decode():
            print(f"Creating database {db_name}...")
            create_db = subprocess.run(
                ["psql", 
                 "-h", db_info["host"], 
                 "-p", db_info["port"], 
                 "-U", db_info["user"], 
                 "-d", "postgres", 
                 "-c", f"CREATE DATABASE {db_name};"],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=env
            )
            
            if create_db.returncode != 0:
                print(f"❌ Failed to create database {db_name}")
                print(create_db.stderr.decode())
                return False
            
            print(f"✅ Database {db_name} created successfully")
        else:
            print(f"✅ Database {db_name} already exists")
        
        # Run migrations
        print("Running Alembic migrations...")
        alembic = subprocess.run(
            ["alembic", "upgrade", "head"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE
        )
        
        if alembic.returncode != 0:
            print("❌ Failed to run migrations")
            print(alembic.stderr.decode())
            return False
        
        print("✅ Migrations applied successfully")
        
        # Initialize database
        await initialize_database()
        
        # Create test user
        user = await create_test_user()
        
        # Create test templates
        await create_test_templates(user.id)
        
        print("\n🎉 Test database setup complete!")
        print(f"📝 Test user: {TEST_USER['email']}")
        print(f"🔑 Password: {TEST_USER['password']}")
        
        return True
        
    except Exception as e:
        print(f"❌ Error setting up test database: {e}")
        return False

if __name__ == "__main__":
    asyncio.run(setup_database())