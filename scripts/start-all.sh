#!/usr/bin/env bash
# Start/repair the QQ bot stack: NapCat/QQ + bridge (+ AstrBot, disabled).
# Usage: bash start-all.sh [--quick]
#   --quick  quick-login fallback (used by the setsid fallback path;
#            the systemd path never passes -q to QQ, so this is a
#            deliberate choice at the call site, not a default).
#   Idempotent: safe to run any time; skips services already up.
#   /etc/machine-id is restored from a home backup only when QQ is not
#   up, because a replaced host gets a random id and the platform sees
#   a new device with an old token and drops the session to QR wait.
set -u
QQBOT="$HOME/workspace/qqbot"
MACHINE_ID_BACKUP="$QQBOT/backups/machine-id.baseline"
RUN_DIR="$QQBOT/run"
mkdir -p "$RUN_DIR"
LOG="$RUN_DIR/start-all.log"
log() { echo "[$(date -Is)] $*" | tee -a "$LOG"; }

# --- 0. Is the bridge already up? ---
if curl -fsS --max-time 2 http://127.0.0.1:6198/ >/dev/null 2>&1; then
  log "bridge already up (6198), nothing to start"
  exit 0
fi

# --- 1. Is QQ online? (needed for the machine-id restore decision) ---
QQ_UP=0
if curl -fsS --max-time 2 http://127.0.0.1:6099/ >/dev/null 2>&1; then
  QQ_UP=1
  log "QQ WebUI (6099) already up, skipping machine-id restore"
fi

# --- 2. Machine-id restore (only when QQ is NOT up) ---
if [ "$QQ_UP" = "0" ]; then
  CURRENT_ID="$(cat /etc/machine-id 2>/dev/null || echo none)"
  if [ -f "$MACHINE_ID_BACKUP" ]; then
    BASELINE_ID="$(cat "$MACHINE_ID_BACKUP")"
    if [ "$CURRENT_ID" != "$BASELINE_ID" ]; then
      log "restoring /etc/machine-id from backup (current $CURRENT_ID != baseline $BASELINE_ID)"
      if sudo -n true 2>/dev/null; then
        sudo -n cp "$MACHINE_ID_BACKUP" /etc/machine-id 2>/dev/null && log "machine-id restored" || log "machine-id restore failed (sudo cp error)"
      else
        log "machine-id restore skipped: no passwordless sudo"
      fi
    else
      log "machine-id already matches backup"
    fi
  else
    log "no machine-id backup at $MACHINE_ID_BACKUP; first login will need a QR scan"
  fi
fi

# --- 3. Install / refresh systemd units from the project tree ---
SYSTEMD_DIR="$QQBOT/systemd"
for unit in qqbot-bridge qqbot-qq qqbot-astrbot; do
  if [ -f "$SYSTEMD_DIR/$unit.service" ]; then
    if [ ! -f "/etc/systemd/system/$unit.service" ] || ! cmp -s "$SYSTEMD_DIR/$unit.service" "/etc/systemd/system/$unit.service"; then
      log "installing $unit.service"
      sudo -n cp "$SYSTEMD_DIR/$unit.service" /etc/systemd/system/ 2>/dev/null && \
        sudo -n systemctl daemon-reload 2>/dev/null || log "install $unit failed (no sudo)"
    fi
  fi
done

# --- 4. Start services (systemctl first, setsid fallback) ---
if command -v systemctl >/dev/null 2>&1; then
  for unit in qqbot-bridge qqbot-qq qqbot-astrbot; do
    if systemctl is-active --quiet "$unit" 2>/dev/null; then
      log "$unit already active"
    else
      log "starting $unit"
      systemctl --user start "$unit" 2>/dev/null || sudo -n systemctl start "$unit" 2>/dev/null || log "start $unit failed"
    fi
  done
else
  log "systemctl missing, falling back to setsid"
  QUICK_FLAG=""
  if [ "${1:-}" = "--quick" ]; then
    QUICK_FLAG="-q ${BOT_QQ:?set BOT_QQ first}"
  fi
  pgrep -f "NapCat" >/dev/null || setsid "$HOME/Napcat/opt/QQ/resources/app/app_launcher/napcat/NapCat" $QUICK_FLAG >/dev/null 2>&1 &
  pgrep -f "bridge.py" >/dev/null || setsid python3 "$QQBOT/bridge/bridge.py" >/dev/null 2>&1 &
fi
log "start-all done"
