#!/bin/bash
# PanelSSH SaaS — Eliminar instancia de un cliente
# Uso: ./eliminar-cliente.sh <slug>

set -e
SLUG="$1"
if [ -z "$SLUG" ]; then
    echo "Uso: $0 <slug>"
    echo "Ej:  $0 empresa-x"
    exit 1
fi

BASE_DIR="$(cd "$(dirname "$0")" && pwd)"
INSTANCE_DIR="${BASE_DIR}/instances/${SLUG}"
SERVICE_NAME="sshpanel-${SLUG}"

echo "Eliminando cliente: $SLUG"

# Detener y deshabilitar servicio
systemctl stop "$SERVICE_NAME" 2>/dev/null || true
systemctl disable "$SERVICE_NAME" 2>/dev/null || true
rm -f "/etc/systemd/system/${SERVICE_NAME}.service"
systemctl daemon-reload

# Eliminar Nginx
rm -f "/etc/nginx/sites-enabled/${SLUG}"
rm -f "/etc/nginx/sites-available/${SLUG}"
systemctl reload nginx

# Eliminar archivos
rm -rf "$INSTANCE_DIR"

echo "✓ Cliente '$SLUG' eliminado completamente."
