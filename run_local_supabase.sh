#!/bin/bash
# Run the app locally but connected to Supabase
#
# Usage: ./run_local_supabase.sh

cd "$(dirname "$0")"

# Load from .env file
if [ -f .env ]; then
    export $(grep -v '^#' .env | xargs)
    echo "Loaded DATABASE_URL from .env"
else
    echo "ERROR: .env file not found"
    exit 1
fi

if [ -z "$DATABASE_URL" ]; then
    echo "ERROR: DATABASE_URL not set in .env"
    exit 1
fi

echo "Starting Flask app connected to Supabase..."
echo ""
echo "Access at: http://localhost:5000"
echo "Debug endpoints:"
echo "  http://localhost:5000/debug/db"
echo "  http://localhost:5000/debug/info"
echo ""

# Run Flask in debug mode
export FLASK_DEBUG=1
export SECRET_KEY="dev-secret-key"
export ADMIN_PASSWORD="admin"

./venv/bin/python app.py
