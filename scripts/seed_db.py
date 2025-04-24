"""
Placeholder script for seeding the database in development.
Currently not in use — extend as needed.
Seed the database with essential data (e.g., admin user).
Currently skips message seeding — placeholder for future use.

"""

import subprocess
import os
import sys

def run_admin_script():
    """Ensure an admin user exists by running create_admin.py."""
    try:
        subprocess.run(
            [sys.executable, "app/scripts/create_admin.py"],
            check=True,
            cwd=os.getcwd(),  # Ensures correct working directory
            env={**os.environ, "PYTHONPATH": os.getcwd()}
        )
        print("👮 Admin user created.")
    except subprocess.CalledProcessError:
        print("⚠️ Failed to create admin user. Check create_admin.py.")


def seed():
    print("🌱 [SKIPPED] No seed data logic implemented yet.")

if __name__ == "__main__":
    seed()
    run_admin_script()
