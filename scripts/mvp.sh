#!/bin/bash
# MVP Setup and Run Script
# This script helps set up and run all components needed for the MVP

# Colors for output
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

echo -e "${BLUE}=== Inboxerr MVP Setup and Run ===${NC}"
echo "This script will help you set up and run all MVP components"

# Extract database connection details from settings
get_db_info() {
  echo "Extracting database connection details..."
  
  # Run a Python script to get DB connection info from settings
  python -c "
from app.core.config import settings
import re

# Parse the connection URL
url = settings.DATABASE_URL
pattern = r'postgresql(?:\+asyncpg)?://([^:]+):([^@]+)@([^:]+):(\d+)/([^?]+)'
match = re.match(pattern, url)

if match:
    print(f'DB_USER={match.group(1)}')
    print(f'DB_PASS={match.group(2)}')
    print(f'DB_HOST={match.group(3)}')
    print(f'DB_PORT={match.group(4)}')
    print(f'DB_NAME={match.group(5)}')
else:
    print('Could not parse database URL')
  "
}

# Source the DB info (if Python script outputs variables)
eval "$(get_db_info)"

# Check if PostgreSQL is running using the extracted credentials
check_postgres() {
  if [ -z "$DB_HOST" ] || [ -z "$DB_PORT" ] || [ -z "$DB_USER" ]; then
    echo -e "${RED}Could not extract database connection details from settings.${NC}"
    return 1
  fi
  
  export PGPASSWORD=$DB_PASS
  pg_status=$(psql -h $DB_HOST -p $DB_PORT -U $DB_USER -d postgres -c "SELECT 1;" 2>/dev/null)
  
  if [ $? -ne 0 ]; then
    echo -e "${RED}PostgreSQL is not running or connection failed.${NC}"
    echo "Please make sure PostgreSQL is running and connection details are correct:"
    echo "Host: $DB_HOST"
    echo "Port: $DB_PORT"
    echo "User: $DB_USER"
    echo "Database: $DB_NAME"
    return 1
  else
    echo -e "${GREEN}PostgreSQL connection successful.${NC}"
    return 0
  fi
}

# Check PostgreSQL connection
if ! check_postgres; then
  echo -e "${YELLOW}Would you like to continue anyway? (y/N)${NC}"
  read continue_anyway
  if [[ ! "$continue_anyway" =~ ^[Yy]$ ]]; then
    echo "Exiting."
    exit 1
  fi
fi

# Create directories if needed
if [ ! -d "tests" ]; then
  echo -e "${YELLOW}Creating test directories...${NC}"
  mkdir -p tests/unit
  mkdir -p tests/integration
  mkdir -p tests/unit/services
  mkdir -p tests/unit/repositories
  mkdir -p tests/unit/api
  mkdir -p tests/integration/api
  
  # Create empty __init__.py files
  touch tests/__init__.py
  touch tests/unit/__init__.py
  touch tests/integration/__init__.py
  touch tests/unit/services/__init__.py
  touch tests/unit/repositories/__init__.py
  touch tests/unit/api/__init__.py
  touch tests/integration/api/__init__.py
  
  echo -e "${GREEN}Test directory structure created!${NC}"
fi

# Function to show menu
show_menu() {
  echo ""
  echo -e "${BLUE}Available actions:${NC}"
  echo "1) Setup development database"
  echo "2) Generate database migrations"
  echo "3) Run migrations"
  echo "4) Run tests"
  echo "5) Start API server"
  echo "6) View API documentation"
  echo "q) Quit"
  echo ""
  echo -n "Enter your choice: "
}

# Main menu loop
while true; do
  show_menu
  read choice

  case $choice in
    1)
      echo -e "${YELLOW}Setting up development database...${NC}"
      python scripts/setup_test_db.py
      ;;
    2)
      echo -e "${YELLOW}Generating database migrations...${NC}"
      read -p "Enter migration description: " desc
      python scripts/generate_migration.py "$desc"
      ;;
    3)
      echo -e "${YELLOW}Running database migrations...${NC}"
      alembic upgrade head
      ;;
    4)
      echo -e "${YELLOW}Running tests...${NC}"
      python scripts/run_tests.py
      ;;
    5)
      echo -e "${YELLOW}Starting API server...${NC}"
      echo -e "${GREEN}API will be available at: http://localhost:8000${NC}"
      echo -e "${GREEN}API Docs URL: http://localhost:8000/api/docs${NC}"
      echo -e "${YELLOW}Press CTRL+C to stop the server${NC}"
      uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
      ;;
    6)
      echo -e "${YELLOW}Opening API documentation...${NC}"
      if command -v xdg-open &> /dev/null; then
        xdg-open http://localhost:8000/api/docs
      elif command -v open &> /dev/null; then
        open http://localhost:8000/api/docs
      elif command -v start &> /dev/null; then
        start http://localhost:8000/api/docs
      else
        echo -e "${RED}Cannot open browser automatically.${NC}"
        echo -e "${GREEN}Please visit: http://localhost:8000/api/docs${NC}"
      fi
      ;;
    q|Q)
      echo -e "${GREEN}Goodbye!${NC}"
      exit 0
      ;;
    *)
      echo -e "${RED}Invalid choice. Please try again.${NC}"
      ;;
  esac
done