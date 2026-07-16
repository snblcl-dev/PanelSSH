#!/bin/bash
# PanelSSH SaaS — Instalar el Panel Maestro como servicio
# Uso: ./instalar-maestro.sh [dominio]
# Ej:  ./instalar-maestro.sh maestro.tudominio.com
set -e

BASE_DIR="$(cd "$(dirname "$0")" && pwd)"
MASTER_DIR="$BASE_DIR/master"
SERVICE_NAME="sshpanel-maestro"
DOMAIN="${1:-}"

echo "============================================"
echo "  PanelSSH — Instalar Panel Maestro"
echo "============================================"

# Crear systemd service
echo "[1/3] Configurando servicio..."
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

# Nginx + SSL si se proporciona dominio
if [ -n "$DOMAIN" ]; then
    echo "[2/3] Configurando Nginx + SSL para $DOMAIN..."

    # Verificar que Nginx y Certbot estén instalados
    if ! command -v nginx &>/dev/null; then
        apt-get update -qq && apt-get install -y -qq nginx
    fi
    if ! command -v certbot &>/dev/null; then
        apt-get install -y -qq certbot python3-certbot-nginx
    fi

    cat > "/etc/nginx/sites-available/maestro" << NGINX
server {
    listen 80;
    server_name $DOMAIN;

    location / {
        proxy_pass http://127.0.0.1:5100;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
    }

    client_max_body_size 10M;
}
NGINX

    ln -sf /etc/nginx/sites-available/maestro /etc/nginx/sites-enabled/maestro
    rm -f /etc/nginx/sites-enabled/default
    nginx -t && systemctl reload nginx

    # SSL
    echo "[3/3] Instalando SSL..."
    certbot --nginx -d "$DOMAIN" --non-interactive --agree-tos --email "admin@${DOMAIN#*.}" 2>/dev/null && \
        echo "  ✓ SSL configurado" || \
        echo "  ⚠ SSL manual: certbot --nginx -d $DOMAIN"

    echo ""
    echo "  URL: https://$DOMAIN"
else
    echo "[2/3] Sin dominio — acceso directo por IP"
    echo "[3/3] Sin SSL"
    echo ""
    echo "  URL: http://$(curl -s ifconfig.me):5100"
    echo "  Para SSL: ./instalar-maestro.sh maestro.tudominio.com"
fi

# Cron para revisar instancias vencidas cada hora
CRON_EXPIRY="0 * * * * $BASE_DIR/check-expirations.sh"
(crontab -l 2>/dev/null | grep -v "check-expirations"; echo "$CRON_EXPIRY") | crontab -

echo "  Service: systemctl status $SERVICE_NAME"
echo "  Pass:    admin123 (cámbiala desde Perfil en el panel)"
echo "============================================"
