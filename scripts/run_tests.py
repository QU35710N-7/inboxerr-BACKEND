#!/usr/bin/env python
"""
Run application tests with pytest.

This script:
1. Creates a test database if it doesn't exist
2. Runs pytest with specified options
3. Generates a coverage report

Usage:
    python scripts/run_tests.py [pytest_args]
    
Examples:
    python scripts/run_tests.py                             # Run all tests
    python scripts/run_tests.py tests/integration           # Run integration tests
    python scripts/run_tests.py -v tests/unit/test_users.py # Run specific test with verbose output
"""
import sys
import os
import subprocess
import re
from pathlib import Path

# Add parent directory to sys.path
sys.path.append(str(Path(__file__).parent.parent))

# Import settings after adding to path
from app.core.config import settings

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

def run_tests():
    """Run tests with pytest."""
    # Get pytest arguments from command line
    pytest_args = sys.argv[1:] if len(sys.argv) > 1 else []
    
    # Create test database name
    db_info = parse_db_url(settings.DATABASE_URL)
    if not db_info:
        print("❌ Could not parse database URL. Please check the format.")
        return False
    
    test_db_name = f"{db_info['dbname']}_test"
    
    # Set up test database environment variable
    os.environ["DATABASE_URL"] = f"postgresql+asyncpg://{db_info['user']}:{db_info['password']}@{db_info['host']}:{db_info['port']}/{test_db_name}"
    os.environ["TESTING"] = "1"
    
    # Print the test database URL
    print(f"Using test database: {test_db_name}")
    
    # Get environment variables for PGPASSWORD to avoid password prompt
    env = os.environ.copy()
    env["PGPASSWORD"] = db_info["password"]
    
    # Check if test database exists
    try:
        check_db = subprocess.run(
            ["psql", 
             "-h", db_info["host"], 
             "-p", db_info["port"], 
             "-U", db_info["user"], 
             "-d", "postgres", 
             "-c", f"SELECT 1 FROM pg_database WHERE datname = '{test_db_name}';"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env
        )
        
        if "1 row" not in check_db.stdout.decode():
            print(f"Creating test database {test_db_name}...")
            create_db = subprocess.run(
                ["psql", 
                 "-h", db_info["host"], 
                 "-p", db_info["port"], 
                 "-U", db_info["user"], 
                 "-d", "postgres", 
                 "-c", f"CREATE DATABASE {test_db_name};"],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=env
            )
            
            if create_db.returncode != 0:
                print(f"❌ Failed to create test database: {create_db.stderr.decode()}")
                return False
            
            print(f"✅ Test database {test_db_name} created")
    except Exception as e:
        print(f"⚠️ Could not check/create test database: {e}")
        print("Continuing with tests...")
    
    # Default pytest arguments if none provided
    if not pytest_args:
        # Run all tests with coverage
        pytest_args = [
            "--cov=app",
            "--cov-report=term-missing",
            "--cov-report=html",
            "-v",
            "tests/"
        ]
    
    # Run pytest
    print(f"Running tests with args: {' '.join(pytest_args)}")
    result = subprocess.run(["pytest"] + pytest_args)
    
    # Print results
    if result.returncode == 0:
        print("✅ All tests passed!")
    else:
        print(f"❌ Tests failed with exit code: {result.returncode}")
    
    return result.returncode == 0


if __name__ == "__main__":
    sys.exit(0 if run_tests() else 1)