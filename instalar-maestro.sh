#!/bin/bash
# PanelSSH SaaS — Instalar el Panel Maestro como servicio
set -e

BASE_DIR="$(cd "$(dirname "$0")" && pwd)"
MASTER_DIR="$BASE_DIR/master"
SERVICE_NAME="sshpanel-maestro"

echo "Instalando Panel Maestro..."

# Crear systemd service
cat > "/etc/systemd/system/${SERVICE_NAME}.service" << SYSTEMD
[Unit]
Description=PanelSSH — Panel Maestro (SaaS)
After=network.target

[Service]
Type=simple
User=root
WorkingDirectory=$MASTER_DIR
Environment=MASTER_PORT=5100
ExecStart=$BASE_DIR/venv/bin/python $MASTER_DIR/wsgi.py
Restart=always
RestartSec=5
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=multi-user.target
SYSTEMD

systemctl daemon-reload
systemctl enable "$SERVICE_NAME"
systemctl start "$SERVICE_NAME"

echo "✓ Panel Maestro instalado"
echo "  URL:     http://$(curl -s ifconfig.me):5100"
echo "  Service: systemctl status $SERVICE_NAME"
echo "  Logs:    journalctl -u $SERVICE_NAME -f"
echo "  Pass:    admin123 (cambiala en /etc/systemd/system/${SERVICE_NAME}.service)"
