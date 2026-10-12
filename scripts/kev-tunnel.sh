#!/usr/bin/env bash
# Proxy Kev on the DGX Spark to the Mac so Docker containers can reach it (ADR 0006 section 5).
#
# Why: Docker Desktop on this Mac has no macOS "Local Network" permission, so containers cannot open connections
# to LAN hosts such as the Spark. They CAN reach the Mac's loopback through host.docker.internal. This script keeps
# an SSH tunnel  Mac 127.0.0.1:LOCAL_PORT -> Spark 127.0.0.1:REMOTE_PORT  alive as a per-user LaunchAgent
# (restarted by launchd if it drops, started again at login). Containers then use
#   TRIAGE__DECISION__BASE_URL / KEV_BASE_URL = http://host.docker.internal:LOCAL_PORT
#
# Usage: scripts/kev-tunnel.sh install | uninstall | start | stop | restart | status | check | logs | run
#   install    write the LaunchAgent and start it (survives reboots)
#   uninstall  stop it and remove the LaunchAgent
#   check      end-to-end test: Mac -> tunnel -> Kev, and a container -> host.docker.internal -> Kev
#   run        run the same tunnel in the foreground (debugging; launchd runs /usr/bin/ssh directly)
#
# Settings (environment variables, defaults in brackets):
#   SPARK_HOST [spark-337a.local]  SPARK_USER [timo]  SPARK_KEY [~/.ssh/spark_hermes_ed25519]
#   LOCAL_PORT [18003]  REMOTE_PORT [8003]  (Kev's published port on the Spark)
#   EXTRA_FORWARDS ["18001:8001"]  more local:remote pairs on the same connection (Qwen vLLM)
set -euo pipefail

SPARK_HOST="${SPARK_HOST:-spark-337a.local}"
SPARK_USER="${SPARK_USER:-timo}"
SPARK_KEY="${SPARK_KEY:-$HOME/.ssh/spark_hermes_ed25519}"
LOCAL_PORT="${LOCAL_PORT:-18003}"
REMOTE_PORT="${REMOTE_PORT:-8003}"
# Extra "local:remote" forwards on the same SSH connection. Default: Qwen (vLLM OpenAI API) for the LLM decision model.
EXTRA_FORWARDS="${EXTRA_FORWARDS:-18001:8001}"

LABEL="com.opstriage.kev-tunnel"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
LOG="$HOME/Library/Logs/opstriage-kev-tunnel.log"
DOMAIN="gui/$(id -u)"
KEV_URL="http://127.0.0.1:$LOCAL_PORT/v1/models"

say() { printf '%s\n' "$*"; }
die() { printf 'error: %s\n' "$*" >&2; exit 1; }

kev_answers() { curl -fsS -m 5 "$KEV_URL" 2>/dev/null | grep -q '"kev-latest"'; }

port_owner() { { lsof -nP -iTCP:"$LOCAL_PORT" -sTCP:LISTEN 2>/dev/null || true; } | awk 'NR==2 {print $1" (pid "$2")"}'; }

preflight() {
  [[ "$(uname -s)" == "Darwin" ]] || die "this script is for macOS"
  [[ -r "$SPARK_KEY" ]] || die "SSH key not readable: $SPARK_KEY"
  ssh -o BatchMode=yes -o ConnectTimeout=5 -i "$SPARK_KEY" "$SPARK_USER@$SPARK_HOST" true \
    || die "cannot SSH to $SPARK_USER@$SPARK_HOST with $SPARK_KEY"
}

# ExitOnForwardFailure makes a busy port fatal (launchd retries); ServerAlive* detect a dead link within ~90 s.
ssh_args() {
  SSH_ARGS=(/usr/bin/ssh -N -T
    -o BatchMode=yes
    -o ExitOnForwardFailure=yes
    -o ServerAliveInterval=30 -o ServerAliveCountMax=3
    -o ConnectTimeout=10
    -o StrictHostKeyChecking=accept-new
    -i "$SPARK_KEY"
    -L "127.0.0.1:$LOCAL_PORT:127.0.0.1:$REMOTE_PORT")
  local pair
  for pair in $EXTRA_FORWARDS; do
    SSH_ARGS+=(-L "127.0.0.1:${pair%%:*}:127.0.0.1:${pair##*:}")
  done
  SSH_ARGS+=("$SPARK_USER@$SPARK_HOST")
}

cmd_run() {  # foreground tunnel, for debugging
  ssh_args
  exec "${SSH_ARGS[@]}"
}

write_plist() {
  # launchd runs /usr/bin/ssh directly: it cannot read scripts under ~/Desktop or ~/Documents (macOS TCC).
  mkdir -p "$(dirname "$PLIST")" "$(dirname "$LOG")"
  ssh_args
  local args="" a
  for a in "${SSH_ARGS[@]}"; do args+="    <string>$a</string>"$'\n'; done
  args="${args%$'\n'}"
  cat > "$PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$LABEL</string>
  <key>ProgramArguments</key>
  <array>
$args
  </array>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>ThrottleInterval</key><integer>10</integer>
  <key>StandardOutPath</key><string>$LOG</string>
  <key>StandardErrorPath</key><string>$LOG</string>
</dict>
</plist>
EOF
  plutil -lint "$PLIST" >/dev/null
}

loaded() { launchctl print "$DOMAIN/$LABEL" >/dev/null 2>&1; }

wait_for_kev() {
  for _ in $(seq 1 20); do kev_answers && return 0; sleep 1; done
  return 1
}

cmd_install() {
  preflight
  if ! loaded; then
    owner="$(port_owner)"
    [[ -z "$owner" ]] || die "port $LOCAL_PORT is already in use by $owner (stop it, or set LOCAL_PORT)"
  fi
  write_plist
  loaded && launchctl bootout "$DOMAIN/$LABEL" 2>/dev/null || true
  launchctl bootstrap "$DOMAIN" "$PLIST"
  launchctl enable "$DOMAIN/$LABEL"
  wait_for_kev || die "tunnel started but Kev does not answer at $KEV_URL (see: $0 logs)"
  say "installed: $PLIST"
  cmd_status
}

cmd_uninstall() {
  loaded && launchctl bootout "$DOMAIN/$LABEL" || true
  rm -f "$PLIST"
  say "uninstalled (log kept at $LOG)"
}

cmd_start() {
  [[ -f "$PLIST" ]] || die "not installed; run: $0 install"
  loaded || launchctl bootstrap "$DOMAIN" "$PLIST"
  launchctl kickstart "$DOMAIN/$LABEL"
  wait_for_kev || die "Kev does not answer at $KEV_URL (see: $0 logs)"
  cmd_status
}

cmd_stop() {
  loaded && launchctl bootout "$DOMAIN/$LABEL" && say "stopped (start again with: $0 start)" || say "not running"
}

cmd_status() {
  if loaded; then
    pid="$(launchctl print "$DOMAIN/$LABEL" 2>/dev/null | awk '/^\tpid = / {print $3}')"
    say "launchd: loaded${pid:+, ssh pid $pid}"
  else
    say "launchd: not loaded"
  fi
  if kev_answers; then
    say "tunnel:  OK  127.0.0.1:$LOCAL_PORT -> $SPARK_HOST:127.0.0.1:$REMOTE_PORT (kev-latest answers)"
  else
    say "tunnel:  DOWN (nothing answers at $KEV_URL)"
    return 1
  fi
}

cmd_check() {
  cmd_status
  command -v docker >/dev/null || die "docker not found"
  say "container check: http://host.docker.internal:$LOCAL_PORT/v1/models"
  local pair
  for pair in $EXTRA_FORWARDS; do
    if curl -fsS -m 5 "http://127.0.0.1:${pair%%:*}/v1/models" >/dev/null 2>&1; then
      say "extra:   OK  127.0.0.1:${pair%%:*} -> $SPARK_HOST:${pair##*:}"
    else
      say "extra:   DOWN 127.0.0.1:${pair%%:*} -> $SPARK_HOST:${pair##*:} (service stopped on the Spark?)"
    fi
  done
  if docker run --rm alpine:3.20 sh -c \
      "wget -q -T 5 -O - http://host.docker.internal:$LOCAL_PORT/v1/models | grep -q kev-latest"; then
    say "container: OK  (set KEV_BASE_URL=http://host.docker.internal:$LOCAL_PORT in .env)"
  else
    die "container cannot reach Kev through host.docker.internal:$LOCAL_PORT"
  fi
}

case "${1:-}" in
  install) cmd_install ;;
  uninstall) cmd_uninstall ;;
  start) cmd_start ;;
  stop) cmd_stop ;;
  restart) cmd_stop; cmd_start ;;
  status) cmd_status ;;
  check) cmd_check ;;
  logs) tail -n 50 "$LOG" ;;
  run) cmd_run ;;
  *) sed -n '2,20p' "$0"; exit 64 ;;
esac
