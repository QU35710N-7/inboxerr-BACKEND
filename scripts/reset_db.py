"""
Reset the development database, run Alembic migrations, and seed initial data.
Works with PostgreSQL database.
"""
import sys
import os
import subprocess
from pathlib import Path



# Resolve project root dynamically
PROJECT_ROOT = Path(__file__).resolve().parent.parent

def reset_database() -> None:
    """
    Reset the development database, run Alembic migrations, and seed initial data.
    This should only be used in development environments.
    """
    # Reset PostgreSQL database (drop and recreate)
    print("🗄️ Resetting PostgreSQL database...")
    try:
        # Connect to default postgres database to drop/create our database
        subprocess.run(
            ["psql", "-U", "postgres", "-c", "DROP DATABASE IF EXISTS inboxerr;"],
            check=True
        )
        subprocess.run(
            ["psql", "-U", "postgres", "-c", "CREATE DATABASE inboxerr;"],
            check=True
        )
        print("✅ Database reset successfully")
    except subprocess.CalledProcessError as e:
        print(f"❌ Error resetting database: {e}")
        print("💡 Make sure PostgreSQL is running and you have permissions")
        sys.exit(1)

    print("🚀 Running Alembic migrations...")
    subprocess.run(["alembic", "upgrade", "head"], check=True, cwd=PROJECT_ROOT)

    print("🌱 Seeding initial data...")
    subprocess.run(
        [sys.executable, "scripts/seed_db.py"], 
        check=True, 
        cwd=PROJECT_ROOT,
        env={**os.environ, "PYTHONPATH": str(PROJECT_ROOT)}
    )

    print("✅ Database reset and seeded successfully!")

if __name__ == "__main__":
    reset_database()