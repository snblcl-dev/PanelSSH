#!/bin/bash
# PanelSSH SaaS — Crear nueva instancia para un cliente
# Uso: ./crear-cliente.sh <slug> <subdominio> [puerto]
# Ej:  ./crear-cliente.sh empresa-x cliente1.midominio.com

set -e

SLUG="$1"
SUBDOMAIN="$2"
PORT="${3:-0}"

if [ -z "$SLUG" ] || [ -z "$SUBDOMAIN" ]; then
    echo "Uso: $0 <slug> <subdominio> [puerto]"
    echo "Ej:  $0 empresa-x cliente1.midominio.com"
    exit 1
fi

# Directorios
BASE_DIR="$(cd "$(dirname "$0")" && pwd)"
INSTANCES_DIR="${BASE_DIR}/instances"
INSTANCE_DIR="${INSTANCES_DIR}/${SLUG}"

echo "============================================"
echo "  PanelSSH SaaS — Crear Cliente"
echo "============================================"
echo "  Slug:       $SLUG"
echo "  Subdominio: $SUBDOMAIN"
echo "  Directorio: $INSTANCE_DIR"

# Crear estructura
mkdir -p "$INSTANCE_DIR"

# Copiar código base (excluyendo instances y archivos innecesarios)
echo "[1/5] Copiando código base..."
rsync -a --exclude 'instances' --exclude 'instance' --exclude '__pycache__' --exclude '*.pyc' --exclude '*.db' --exclude '.secret_key' --exclude '.git' --exclude 'venv' \
      "$BASE_DIR/" "$INSTANCE_DIR/"

# Generar secret key única
echo "[2/5] Generando secret key..."
python3 -c "import secrets; open('$INSTANCE_DIR/.secret_key','w').write(secrets.token_hex(64))"

# Asignar puerto automático si no se especificó
if [ "$PORT" -eq 0 ]; then
    # Buscar el puerto más alto usado + 1
    LAST_PORT=$(grep -rhoP 'SSHPANEL_PORT=\K\d+' /etc/systemd/system/sshpanel-*.service 2>/dev/null | sort -n | tail -1)
    PORT=$(( ${LAST_PORT:-5000} + 1 ))
fi

# Verificar que el puerto no esté en uso
if ss -tlnp | grep -q ":$PORT "; then
    echo "  ERROR: Puerto $PORT ya está en uso"
    exit 1
fi
echo "  Puerto asignado: $PORT"

# Configurar DNS automático con Cloudflare (si hay credenciales)
CF_CONFIG="$BASE_DIR/.cloudflare"
if [ -f "$CF_CONFIG" ]; then
    source "$CF_CONFIG"
    if [ -n "$CF_API_TOKEN" ] && [ -n "$CF_ZONE_ID" ]; then
        echo "[*] Creando registro DNS en Cloudflare..."
        CF_RESULT=$(curl -s -X POST "https://api.cloudflare.com/client/v4/zones/$CF_ZONE_ID/dns_records" \
            -H "Authorization: Bearer $CF_API_TOKEN" \
            -H "Content-Type: application/json" \
            --data "{\"type\":\"A\",\"name\":\"$SUBDOMAIN\",\"content\":\"$(curl -s ifconfig.me)\",\"ttl\":120,\"proxied\":false}")
        if echo "$CF_RESULT" | grep -q '"success":true'; then
            echo "  ✓ DNS creado: $SUBDOMAIN"
        else
            echo "  ⚠ Error DNS: $(echo $CF_RESULT | grep -o '\"message\":\"[^\"]*\"')"
        fi
    fi
fi

# Configurar systemd
echo "[3/5] Configurando servicio systemd..."
SERVICE_NAME="sshpanel-${SLUG}"
cat > "/etc/systemd/system/${SERVICE_NAME}.service" << SYSTEMD
[Unit]
Description=PanelSSH — $SLUG ($SUBDOMAIN)
After=network.target

[Service]
Type=simple
User=root
WorkingDirectory=$INSTANCE_DIR
Environment=SSHPANEL_INSTANCE_DIR=$INSTANCE_DIR
Environment=SSHPANEL_PORT=$PORT
Environment=SSHPANEL_MODE=saas
ExecStart=$INSTANCE_DIR/venv/bin/python $INSTANCE_DIR/wsgi.py
Restart=always
RestartSec=5
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=multi-user.target
SYSTEMD

# Crear venv si no existe
if [ ! -d "$INSTANCE_DIR/venv" ]; then
    echo "  Creando entorno virtual..."
    python3 -m venv "$INSTANCE_DIR/venv"
    "$INSTANCE_DIR/venv/bin/pip" install -r "$INSTANCE_DIR/requirements.txt" -q
fi

systemctl daemon-reload
systemctl enable "$SERVICE_NAME"
systemctl start "$SERVICE_NAME"

# Configurar Nginx
echo "[4/5] Configurando Nginx..."
cat > "/etc/nginx/sites-available/${SLUG}" << NGINX
server {
    listen 80;
    server_name $SUBDOMAIN;

    location / {
        proxy_pass http://127.0.0.1:$PORT;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
    }

    client_max_body_size 10M;
}
NGINX

ln -sf "/etc/nginx/sites-available/${SLUG}" "/etc/nginx/sites-enabled/${SLUG}"
nginx -t && systemctl reload nginx

# SSL
echo "[5/5] Instalando SSL..."
certbot --nginx -d "$SUBDOMAIN" --non-interactive --agree-tos --email admin@${SUBDOMAIN#*.} 2>/dev/null || \
    echo "  AVISO: SSL no se pudo instalar automáticamente. Ejecuta manualmente:"
    echo "    certbot --nginx -d $SUBDOMAIN"

echo ""
echo "============================================"
echo "  ✓ Cliente creado exitosamente!"
echo ""
echo "  URL:     https://$SUBDOMAIN"
echo "  Puerto:  $PORT"
echo "  Admin:   admin / admin"
echo "  Service: systemctl status $SERVICE_NAME"
echo "  Logs:    journalctl -u $SERVICE_NAME -f"
echo "============================================"
