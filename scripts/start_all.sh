#!/bin/bash
# 本地全栈一键启动（2026-09-28）：后端 8010 + 前端静态服务 5174（vite preview）
# 用法: scripts/start_all.sh [start|stop|status|build]   （缺省 start）
# GPU 语音服务（diarize_svc）在 GPU 机器上，由 scripts/gpu/run_supervised.sh 看护，不归本脚本。
set -u
cd "$(dirname "$0")/.."
ROOT=$PWD
BACKEND_PORT=8010
FRONTEND_PORT=5174
BACKEND_LOG=/tmp/meeting_backend.log
FRONTEND_LOG=/tmp/meeting_frontend.log

# 只匹配真正的服务进程，避开 bash -c / sh -c wrapper（否则 stop 杀不掉真身）
backend_pid() { pgrep -af "uvicorn app.main:app.*$BACKEND_PORT" | awk '$2 ~ /python/ {print $1; exit}'; }
frontend_pid() { pgrep -af "vite preview.*$FRONTEND_PORT" | awk '$2 ~ /node/ {print $1; exit}'; }

cmd_start() {
    if [ -z "$(backend_pid)" ]; then
        echo "启动后端 :$BACKEND_PORT ..."
        (cd server && setsid --fork nohup "$ROOT/.venv/bin/python" -m uvicorn app.main:app \
            --host 0.0.0.0 --port $BACKEND_PORT > "$BACKEND_LOG" 2>&1 < /dev/null &)
    else
        echo "后端已在跑 (pid $(backend_pid))"
    fi
    if [ -z "$(frontend_pid)" ]; then
        if [ ! -d web/dist ]; then
            echo "web/dist 不存在，先构建: scripts/start_all.sh build"
            exit 1
        fi
        echo "启动前端静态服务 :$FRONTEND_PORT (vite preview)..."
        (cd web && setsid --fork nohup npm run preview -- --port $FRONTEND_PORT --host > "$FRONTEND_LOG" 2>&1 < /dev/null &)
    else
        echo "前端已在跑 (pid $(frontend_pid))"
    fi
    sleep 6
    cmd_status
}

cmd_stop() {
    p=$(backend_pid); [ -n "$p" ] && kill "$p" && echo "后端已停 (pid $p)"
    p=$(frontend_pid); [ -n "$p" ] && kill "$p" && echo "前端已停 (pid $p)"
}

cmd_status() {
    b=$(backend_pid); f=$(frontend_pid)
    if [ -n "$b" ]; then echo "后端  :$BACKEND_PORT  运行中 (pid $b)  日志 $BACKEND_LOG"; else echo "后端  :$BACKEND_PORT  未运行"; fi
    if [ -n "$f" ]; then echo "前端  :$FRONTEND_PORT  运行中 (pid $f)  日志 $FRONTEND_LOG"; else echo "前端  :$FRONTEND_PORT  未运行"; fi
    curl -s -m 3 "http://127.0.0.1:$BACKEND_PORT/api/speakers" >/dev/null \
        && echo "健康检查: 后端 OK" || echo "健康检查: 后端无响应"
}

cmd_build() {
    echo "构建前端 ..."
    (cd web && npm run build) || exit 1
    echo "构建完成（vite preview 会 Serving dist/）"
}

case "${1:-start}" in
    start)   cmd_start ;;
    stop)    cmd_stop ;;
    status)  cmd_status ;;
    build)   cmd_build ;;
    restart) cmd_stop; sleep 2; cmd_start ;;
    *) echo "用法: $0 [start|stop|status|build|restart]"; exit 1 ;;
esac
