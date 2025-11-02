#!/bin/bash

# Run Atlas Photogrammetry API locally (development)

set -e

echo "Starting Atlas Photogrammetry API..."

if [ ! -d "venv" ]; then
    echo "Creating virtual environment..."
    python3 -m venv venv
fi

echo "Activating virtual environment..."
source venv/bin/activate

echo "Installing dependencies..."
pip install -e .

if [ ! -f .env ]; then
    echo "Creating .env from .env.development..."
    cp .env.development .env
    echo "Please edit .env with your configuration"
fi

# Check if Redis is running and responding
if ! redis-cli ping > /dev/null 2>&1; then
    echo "Redis not responding. Starting Redis..."
    redis-server --daemonize yes
    
    # Wait for Redis to start
    echo "Waiting for Redis to start..."
    for i in {1..10}; do
        if redis-cli ping > /dev/null 2>&1; then
            echo "Redis started successfully"
            break
        fi
        sleep 1
    done
else
    echo "Redis is already running"
fi

# Create data directories
mkdir -p storage/workspaces storage/artifacts storage/status

export PYTHONPATH=$PWD

echo "Starting FastAPI server..."
uvicorn api.main:app --host 0.0.0.0 --port 8000 --reload &

echo "Starting Celery worker..."
celery -A worker.tasks worker --loglevel=info --concurrency=1 &

echo ""
echo "Atlas Photogrammetry API is running!"
echo "API: http://localhost:8000"
echo "Docs: http://localhost:8000/docs"
echo ""
echo "Press Ctrl+C to stop"

wait
