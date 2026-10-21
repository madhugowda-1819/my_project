#!/usr/bin/env bash
set -euo pipefail

APP_DIR="${1:?Usage: $0 /absolute/path/to/my_project /absolute/path/to/venv [service-user]}"
VENV_DIR="${2:?Usage: $0 /absolute/path/to/my_project /absolute/path/to/venv [service-user]}"
SERVICE_USER="${3:-sportmate}"

if [[ "${APP_DIR}" != /* || "${VENV_DIR}" != /* ]]; then
  echo "APP_DIR and VENV_DIR must be absolute paths." >&2
  exit 1
fi
if [[ ! -f "${APP_DIR}/manage.py" ]]; then
  echo "manage.py was not found under ${APP_DIR}." >&2
  exit 1
fi
if [[ ! -x "${VENV_DIR}/bin/python" ]]; then
  echo "Virtualenv Python was not found at ${VENV_DIR}/bin/python." >&2
  exit 1
fi

sudo install -d -m 0755 /etc/systemd/system
sudo install -d -m 0755 /var/log/sportmate

sudo tee /etc/systemd/system/sportmate-ground-cache-cleanup.service >/dev/null <<EOF
[Unit]
Description=SportMate expired live-ground cache cleanup
After=network-online.target

[Service]
Type=oneshot
WorkingDirectory=${APP_DIR}
ExecStart=${VENV_DIR}/bin/python manage.py cleanup_expired_sports_grounds
User=${SERVICE_USER}
Group=${SERVICE_USER}
StandardOutput=journal
StandardError=journal
EOF

sudo install -m 0644 "$(dirname "$0")/sportmate-ground-cache-cleanup.timer" \
  /etc/systemd/system/sportmate-ground-cache-cleanup.timer
sudo systemctl daemon-reload
sudo systemctl enable --now sportmate-ground-cache-cleanup.timer
sudo systemctl start sportmate-ground-cache-cleanup.service
sudo systemctl status --no-pager sportmate-ground-cache-cleanup.timer
