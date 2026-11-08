#!/usr/bin/env bash
set -euo pipefail

if [ $# -ne 1 ]; then
  echo "usage: ./deploy/move-to-server.sh user@server-ip"
  exit 1
fi

HOST="$1"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
REMOTE_DIR="YAPPER-AGENT"
SSH=(ssh -o ServerAliveInterval=30 "$HOST")
export COPYFILE_DISABLE=1

[ -f "$ROOT/.env" ] || { echo "no .env found in $ROOT"; exit 1; }

local_pids() {
  for pid in $(pgrep -f "app.main:app|src/index.ts" || true); do
    if lsof -a -p "$pid" -d cwd -Fn 2>/dev/null | grep -q "^n$ROOT"; then
      echo "$pid"
    fi
  done
}

upload() {
  tar -C "$1" --no-xattrs -czf - "${@:2}" | "${SSH[@]}" "tar -C $REMOTE_DIR --warning=no-unknown-keyword -xzf -"
}

echo "1/6 checking the server"
"${SSH[@]}" true
if "${SSH[@]}" "test -f $REMOTE_DIR/data/yapper.db" && [ "${FORCE:-0}" != "1" ]; then
  echo "the server already has a Yapper database. Run with FORCE=1 to overwrite it with this Mac's copy."
  exit 1
fi

echo "2/6 installing docker and the code on the server"
"${SSH[@]}" "bash -s" < "$ROOT/deploy/setup-server.sh"

echo "3/6 downloading Yapper on the server"
upload "$ROOT" .env
"${SSH[@]}" "chmod 600 $REMOTE_DIR/.env && cd $REMOTE_DIR && (sudo docker compose pull -q || sudo docker compose build -q)"

pids="$(local_pids)"
if [ -n "$pids" ]; then
  read -r -p "4/6 Yapper is running on this Mac. Stop it so the server can take over? [y/N] " answer
  [[ "$answer" =~ ^[Yy]$ ]] || { echo "cancelled. Yapper is still running on this Mac."; exit 1; }
  kill $pids
  sleep 3
else
  echo "4/6 Yapper is not running on this Mac"
fi

echo "5/6 copying memory and the WhatsApp login"
tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT
mkdir -p "$tmp/data" "$tmp/gateway"
[ -f "$ROOT/data/yapper.db" ] && sqlite3 "$ROOT/data/yapper.db" ".backup '$tmp/data/yapper.db'"
[ -d "$ROOT/gateway/auth" ] && cp -R "$ROOT/gateway/auth" "$tmp/gateway/auth"
"${SSH[@]}" "rm -f $REMOTE_DIR/data/yapper.db-wal $REMOTE_DIR/data/yapper.db-shm"
upload "$tmp" data gateway

echo "6/6 starting Yapper"
"${SSH[@]}" "cd $REMOTE_DIR && sudo docker compose up -d"
for _ in $(seq 1 30); do
  if health="$("${SSH[@]}" "curl -sf http://127.0.0.1:3001/health" 2>/dev/null)"; then
    echo "$health"
    echo
    echo "done. Yapper now runs on $HOST, even when this Mac is off."
    echo "watch it: ssh $HOST 'cd $REMOTE_DIR && sudo docker compose logs -f'"
    exit 0
  fi
  sleep 5
done
echo "started, but the gateway is not answering yet. check: ssh $HOST 'cd $REMOTE_DIR && sudo docker compose logs --tail 50'"
