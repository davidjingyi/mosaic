#!/usr/bin/env bash
set -euo pipefail

# ============================================================
#  Mosaic — One-command deployment script
#  Usage: bash deploy.sh [dev|prod|stop|status|restart]
#
#  Environment variables:
#    APP_PORT    — app listen port (default: 8000)
#    EMBED_PORT  — embedding service port (default: 8003)
#    VENV_DIR    — virtualenv path (default: venv)
# ============================================================

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

VENV_DIR="${VENV_DIR:-venv}"
CONFIG_FILE="config.yaml"
EXAMPLE_CONFIG="config.example.yaml"
EMBED_PID_FILE=".embed.pid"
APP_PID_FILE=".app.pid"
LOG_DIR="logs"
APP_PORT="${APP_PORT:-8000}"
EMBED_PORT="${EMBED_PORT:-8003}"

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; NC='\033[0m'

log()  { echo -e "${GREEN}[mosaic]${NC} $1"; }
warn() { echo -e "${YELLOW}[mosaic]${NC} $1"; }
err()  { echo -e "${RED}[mosaic]${NC} $1"; }

# ── check prerequisites ──────────────────────────────────────
check_prereqs() {
    command -v python3 >/dev/null 2>&1 || { err "python3 not found"; exit 1; }
    log "python3: $(python3 --version)"
}

# ── create venv ──────────────────────────────────────────────
setup_venv() {
    if [ ! -d "$VENV_DIR" ]; then
        log "Creating virtual environment..."
        python3 -m venv "$VENV_DIR"
    else
        log "Virtual environment exists: $VENV_DIR"
    fi
    source "$VENV_DIR/bin/activate"
    log "Installing dependencies..."
    pip install -q --upgrade pip
    pip install -q -r requirements.txt
    log "Dependencies installed"
}

# ── config ───────────────────────────────────────────────────
setup_config() {
    if [ ! -f "$CONFIG_FILE" ]; then
        if [ -f "$EXAMPLE_CONFIG" ]; then
            cp "$EXAMPLE_CONFIG" "$CONFIG_FILE"
            warn "Created $CONFIG_FILE from $EXAMPLE_CONFIG"
            warn ">>> EDIT config.yaml and set llm.api_key before starting <<<"
        else
            err "$EXAMPLE_CONFIG not found"
            exit 1
        fi
    fi
}

# ── dirs ─────────────────────────────────────────────────────
setup_dirs() {
    mkdir -p "$LOG_DIR" data knowledge-base
}

# ── start embedding service ──────────────────────────────────
start_embed() {
    if [ -f "$EMBED_PID_FILE" ] && kill -0 "$(cat "$EMBED_PID_FILE")" 2>/dev/null; then
        log "Embedding service already running (PID $(cat "$EMBED_PID_FILE"))"
        return
    fi

    log "Starting embedding service (port $EMBED_PORT)..."
    EMBED_PORT="$EMBED_PORT" nohup "${VENV_DIR}/bin/python" start_embedding.py > "$LOG_DIR/embed.log" 2>&1 &
    echo $! > "$EMBED_PID_FILE"
    log "Embedding service started (PID $(cat "$EMBED_PID_FILE"))"

    # Wait for readiness
    for i in $(seq 1 30); do
        if curl -sf "http://127.0.0.1:${EMBED_PORT}/health" >/dev/null 2>&1; then
            log "Embedding service ready"
            return
        fi
        sleep 1
    done
    err "Embedding service failed to start (check $LOG_DIR/embed.log)"
    exit 1
}

# ── start app ────────────────────────────────────────────────
start_app() {
    local mode="${1:-dev}"

    if [ -f "$APP_PID_FILE" ] && kill -0 "$(cat "$APP_PID_FILE")" 2>/dev/null; then
        log "App already running (PID $(cat "$APP_PID_FILE"))"
        return
    fi

    if [ "$mode" = "prod" ]; then
        log "Starting in PRODUCTION mode (gunicorn, port $APP_PORT)..."
        nohup "${VENV_DIR}/bin/gunicorn" -c gunicorn_conf.py app.main:app --bind "0.0.0.0:${APP_PORT}" \
            > "$LOG_DIR/app.log" 2>&1 &
    else
        log "Starting in DEV mode (uvicorn, port $APP_PORT)..."
        nohup "${VENV_DIR}/bin/uvicorn" app.main:app --host 0.0.0.0 --port "$APP_PORT" --reload \
            > "$LOG_DIR/app.log" 2>&1 &
    fi

    echo $! > "$APP_PID_FILE"
    log "App started (PID $(cat "$APP_PID_FILE"))"

    # Wait for readiness
    for i in $(seq 1 20); do
        if curl -sf "http://127.0.0.1:${APP_PORT}/health" >/dev/null 2>&1; then
            log "App ready → http://localhost:${APP_PORT}"
            return
        fi
        sleep 1
    done
    err "App failed to start (check $LOG_DIR/app.log)"
    exit 1
}

# ── stop all ─────────────────────────────────────────────────
stop_all() {
    log "Stopping services..."

    for pid_file in "$APP_PID_FILE" "$EMBED_PID_FILE"; do
        if [ -f "$pid_file" ]; then
            local pid
            pid=$(cat "$pid_file")
            if kill -0 "$pid" 2>/dev/null; then
                kill "$pid" 2>/dev/null || true
                log "Stopped PID $pid ($pid_file)"
            fi
            rm -f "$pid_file"
        fi
    done

    # Also kill any remaining uvicorn/gunicorn in this dir
    pkill -f "uvicorn app.main:app" 2>/dev/null || true
    pkill -f "gunicorn.*app.main:app" 2>/dev/null || true
    pkill -f "embedding_service.py" 2>/dev/null || true

    log "All stopped"
}

# ── status ───────────────────────────────────────────────────
show_status() {
    echo ""
    echo "  Mosaic Services"
    echo "  ───────────────"

    if [ -f "$EMBED_PID_FILE" ] && kill -0 "$(cat "$EMBED_PID_FILE")" 2>/dev/null; then
        echo -e "  Embedding   ${GREEN}● running${NC}  (PID $(cat "$EMBED_PID_FILE"))"
    else
        echo -e "  Embedding   ${RED}○ stopped${NC}"
    fi

    if [ -f "$APP_PID_FILE" ] && kill -0 "$(cat "$APP_PID_FILE")" 2>/dev/null; then
        echo -e "  App         ${GREEN}● running${NC}  (PID $(cat "$APP_PID_FILE"))"
    else
        echo -e "  App         ${RED}○ stopped${NC}"
    fi

    echo ""
}

# ── main ─────────────────────────────────────────────────────
case "${1:-dev}" in
    dev|prod)
        check_prereqs
        setup_venv
        setup_config
        setup_dirs
        start_embed
        start_app "$1"
        show_status
        log "Deploy complete. Run 'bash deploy.sh stop' to shut down."
        ;;
    stop)
        stop_all
        show_status
        ;;
    status)
        show_status
        ;;
    restart)
        stop_all
        sleep 2
        check_prereqs
        setup_venv
        setup_config
        setup_dirs
        start_embed
        start_app "dev"
        show_status
        ;;
    *)
        echo ""
        echo "  Mosaic — One-command deployment"
        echo ""
        echo "  Usage:"
        echo "    bash deploy.sh           # Dev mode (uvicorn + auto-reload)"
        echo "    bash deploy.sh prod      # Production mode (gunicorn)"
        echo "    bash deploy.sh stop      # Stop all services"
        echo "    bash deploy.sh restart   # Stop + restart"
        echo "    bash deploy.sh status    # Show service status"
        echo ""
        ;;
esac
