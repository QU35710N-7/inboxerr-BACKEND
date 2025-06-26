# app/scripts/create_admin.py
import asyncio
import sys
from pathlib import Path

# Add parent directory to path
sys.path.append(str(Path(__file__).parent.parent))

from app.db.session import get_repository_context, initialize_database
from app.db.repositories.users import UserRepository
from app.core.security import get_password_hash

async def create_admin_user():
    """Create an admin user if none exists."""
    # Initialize database
    await initialize_database()
    
    # Use session properly with context manager
    async with get_repository_context(UserRepository) as user_repo:

        # Check if admin exists
        admin = await user_repo.get_by_email("admin@inboxerr.com")
        
        if admin:
            print("✅ Admin user already exists (id:", admin.id, ")")
            return
        
        # Create admin user
        password = "Admin123!"
        hashed_password = get_password_hash(password)
        
        admin = await user_repo.create(
            email="admin@inboxerr.com",
            hashed_password=hashed_password,
            full_name="Admin User",
            is_active=True,
            role="admin"
        )
        
    print(f"Admin user created with ID: {admin.id}")
    print(f"Email: admin@inboxerr.com")
    print(f"Password: {password}")

if __name__ == "__main__":
    asyncio.run(create_admin_user())