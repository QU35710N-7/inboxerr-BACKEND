#!/usr/bin/env python
"""
Generate Alembic migrations.

This script parses database credentials from settings and runs alembic to generate migrations.

Usage:
    python scripts/generate_migration.py "Add message templates"
"""
import sys
import subprocess
import re
from pathlib import Path

# Add parent directory to path to allow importing from app
sys.path.append(str(Path(__file__).parent.parent))

# Import after adding to path
from app.core.config import settings

def generate_migration(message):
    """Generate an Alembic migration with the given message."""
    try:
        # Set the Alembic database URL from settings
        # Replace asyncpg with standard psycopg2 for Alembic
        db_url = settings.DATABASE_URL.replace("postgresql+asyncpg", "postgresql")
        
        # Set environment variable for Alembic
        import os
        os.environ["ALEMBIC_DB_URL"] = db_url
        
        # Run alembic command
        print(f"Generating migration: {message}")
        result = subprocess.run(
            ["alembic", "revision", "--autogenerate", "-m", message], 
            check=True,
            capture_output=True,
            text=True
        )
        
        # Parse output to find migration file path
        output = result.stdout
        file_pattern = r"Generating .*\\(.*?\.py)"
        match = re.search(file_pattern, output)
        
        if match:
            migration_file = match.group(1)
            print(f"✅ Successfully generated migration: {migration_file}")
        else:
            print(f"✅ Successfully generated migration")
            
        print("⚠️ Please review the generated migration file to ensure it's correct.")
        print("💡 To apply the migration, run: alembic upgrade head")
        
    except subprocess.CalledProcessError as e:
        print(f"❌ Error generating migration: {e}")
        print("Error output:")
        print(e.stderr)
        print("💡 Make sure alembic is installed and your database is running.")
        sys.exit(1)

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("❌ Error: Please provide a migration message.")
        print("💡 Example: python scripts/generate_migration.py 'Add user table'")
        sys.exit(1)
    
    message = sys.argv[1]
    generate_migration(message)