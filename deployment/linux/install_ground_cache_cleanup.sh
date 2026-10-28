#!/usr/bin/env bash
set -euo pipefail

APP_DIR="${1:?application directory is required}"
VENV_DIR="${2:?virtualenv directory is required}"
RUN_USER="${3:?service user is required}"

if [[ ! -x "${VENV_DIR}/bin/python" ]]; then
  echo "Virtualenv Python not found: ${VENV_DIR}/bin/python" >&2
  exit 1
fi

install -d -m 0755 /etc/systemd/system

cat > /etc/systemd/system/sportmate-ground-cache-cleanup.service <<EOF
[Unit]
Description=Clean expired SportMate live-ground cache records
After=network.target

[Service]
Type=oneshot
User=${RUN_USER}
WorkingDirectory=${APP_DIR}
ExecStart=${VENV_DIR}/bin/python ${APP_DIR}/manage.py cleanup_expired_sports_grounds
EOF

cat > /etc/systemd/system/sportmate-ground-cache-cleanup.timer <<EOF
[Unit]
Description=Run SportMate ground-cache cleanup hourly

[Timer]
OnBootSec=10min
OnUnitActiveSec=1h
Persistent=true

[Install]
WantedBy=timers.target
EOF

systemctl daemon-reload
systemctl enable --now sportmate-ground-cache-cleanup.timer
systemctl start sportmate-ground-cache-cleanup.service
systemctl is-enabled --quiet sportmate-ground-cache-cleanup.timer
systemctl is-active --quiet sportmate-ground-cache-cleanup.timer
