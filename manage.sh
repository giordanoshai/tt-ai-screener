#!/usr/bin/env bash
# tt-trading-mcp management script
# Usage: ./manage.sh [start|stop|restart|status]

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV="$SCRIPT_DIR/.venv/bin/python"
PID_FILE="$SCRIPT_DIR/.web.pid"
LOG_FILE="$SCRIPT_DIR/logs/web.log"
PORT=8765

# Use system python if venv not found
if [ ! -f "$VENV" ]; then
    VENV="python3"
fi

mkdir -p "$SCRIPT_DIR/logs"

_pid() {
    if [ -f "$PID_FILE" ]; then
        local pid
        pid=$(cat "$PID_FILE")
        if kill -0 "$pid" 2>/dev/null; then
            echo "$pid"
        else
            rm -f "$PID_FILE"
        fi
    fi
}

cmd_status() {
    local pid
    pid=$(_pid)
    if [ -n "$pid" ]; then
        echo "web server is running  (pid=$pid, port=$PORT)"
    else
        echo "web server is stopped"
    fi
}

cmd_start() {
    if [ -n "$(_pid)" ]; then
        echo "already running (pid=$(cat "$PID_FILE"))"
        return
    fi
    echo "starting web server on port $PORT ..."
    nohup "$VENV" -m uvicorn web.app:app \
        --host 0.0.0.0 \
        --port "$PORT" \
        >> "$LOG_FILE" 2>&1 &
    echo $! > "$PID_FILE"
    sleep 1
    if [ -n "$(_pid)" ]; then
        echo "started  (pid=$(cat "$PID_FILE")) — http://localhost:$PORT"
        echo "logs: $LOG_FILE"
    else
        echo "failed to start — check $LOG_FILE"
        exit 1
    fi
}

cmd_stop() {
    local pid
    pid=$(_pid)
    if [ -z "$pid" ]; then
        echo "not running"
        return
    fi
    echo "stopping (pid=$pid) ..."
    kill "$pid"
    rm -f "$PID_FILE"
    echo "stopped"
}

cmd_restart() {
    cmd_stop
    sleep 1
    cmd_start
}

cmd_logs() {
    if [ -f "$LOG_FILE" ]; then
        tail -f "$LOG_FILE"
    else
        echo "no log file yet: $LOG_FILE"
    fi
}

cd "$SCRIPT_DIR"

case "${1:-status}" in
    start)   cmd_start   ;;
    stop)    cmd_stop    ;;
    restart) cmd_restart ;;
    status)  cmd_status  ;;
    logs)    cmd_logs    ;;
    *)
        echo "Usage: $0 {start|stop|restart|status|logs}"
        exit 1
        ;;
esac
