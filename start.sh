#!/bin/bash

# ==============================================================================
# Service Start Script: Thunder AI Agent Gateway (StreamsAgentGateway)
# ==============================================================================

set -o pipefail

# Resolve project root directory
APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$APP_DIR" || { echo "[-] Failed to access application directory $APP_DIR"; exit 1; }

SERVICE_NAME="Thunder AI Agent Gateway"
SERVICE_GREP="app.main:app"
LOG_DIR="/opt/StreamsAIGateway_Logs"
PID_FILE="$LOG_DIR/gunicorn.pid"
STARTUP_LOG="$LOG_DIR/startup.log"
PORT=5006

# Ensure log directory exists and is writable
if [ ! -d "$LOG_DIR" ]; then
    mkdir -p "$LOG_DIR" 2>/dev/null || true
fi

# Fallback startup log location if log directory is not writable
if [ ! -w "$LOG_DIR" ]; then
    STARTUP_LOG="$APP_DIR/startup.log"
fi

# Function to check if service is already running
is_running() {
    if [ -f "$PID_FILE" ]; then
        local pid
        pid=$(cat "$PID_FILE" 2>/dev/null | tr -d '[:space:]')
        if [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null; then
            return 0
        fi
    fi
    if pgrep -f "$SERVICE_GREP" > /dev/null 2>&1; then
        return 0
    fi
    return 1
}

# 1. Check if service is already active
if is_running; then
    RUNNING_PIDS=$(pgrep -f "$SERVICE_GREP" 2>/dev/null | tr '\n' ' ')
    echo "[!] $SERVICE_NAME is already running."
    echo "    Active PID(s): $RUNNING_PIDS"
    if [ -f "$PID_FILE" ]; then
        echo "    Master PID: $(cat "$PID_FILE" 2>/dev/null)"
    fi
    exit 0
fi

# 2. Check prerequisites
if ! command -v uv > /dev/null 2>&1; then
    echo "[-] Error: 'uv' package manager is not installed or not in PATH."
    exit 1
fi

if [ ! -f ".env" ]; then
    echo "[!] Warning: '.env' file not found in $APP_DIR. Ensure environment variables are set."
fi

# Remove stale PID file if present
if [ -f "$PID_FILE" ]; then
    rm -f "$PID_FILE"
fi

echo "[*] Starting $SERVICE_NAME in background..."

# 3. Launch Gunicorn with Uvicorn workers via uv
nohup uv run gunicorn -c gunicorn_config.py --pid "$PID_FILE" app.main:app >> "$STARTUP_LOG" 2>&1 &

# 4. Wait and verify health
MAX_RETRIES=15
RETRY_COUNT=0
SERVICE_HEALTHY=0

echo -n "[*] Waiting for service to initialize"
while [ $RETRY_COUNT -lt $MAX_RETRIES ]; do
    sleep 1
    echo -n "."

    if is_running; then
        SERVICE_HEALTHY=1
        break
    fi

    RETRY_COUNT=$((RETRY_COUNT + 1))
done
echo ""

if [ $SERVICE_HEALTHY -eq 1 ]; then
    MASTER_PID=$(cat "$PID_FILE" 2>/dev/null || pgrep -fo "$SERVICE_GREP")
    WORKER_COUNT=$(pgrep -f "uvicorn.workers.UvicornWorker.*app.main:app" | wc -l)
    echo "[+] $SERVICE_NAME started successfully!"
    echo "    Master PID:     $MASTER_PID"
    [ "$WORKER_COUNT" -gt 0 ] && echo "    Worker Count:   $WORKER_COUNT"
    echo "    Port:           $PORT"
    echo "    Access Log:     $LOG_DIR/StreamsAgentGateway.log"
    echo "    Startup Log:    $STARTUP_LOG"
    exit 0
else
    echo "    Check logs for details: $LOG_DIR/StreamsAgentGateway.log"
    exit 1
fi
