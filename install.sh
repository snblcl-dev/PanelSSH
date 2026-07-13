#!/bin/bash
set -e

echo "========================================"
echo "  SSH Panel - Instalacion para Linux"
echo "========================================"

# Detectar sistema
if [ ! -f /etc/os-release ]; then
    echo "Error: Solo soportado en Linux"
    exit 1
fi

# Verificar Python 3
if ! command -v python3 &> /dev/null; then
    echo "Instalando Python 3..."
    apt-get update -qq
    apt-get install -y -qq python3 python3-pip
fi

# Obtener version exacta de Python (ej: 3.10)
PY_VERSION=$(python3 --version 2>&1 | grep -oP '\d+\.\d+')
echo "Python detectado: $PY_VERSION"

# Instalar python3-venv de la version exacta
echo "Verificando python${PY_VERSION}-venv..."
dpkg -l "python${PY_VERSION}-venv" &>/dev/null || apt-get install -y -qq "python${PY_VERSION}-venv"

# Crear directorio del proyecto si no existe
cd "$(dirname "$0")"

# Crear entorno virtual
echo "[1/4] Creando entorno virtual..."
python3 -m venv venv

# Instalar dependencias
echo "[2/4] Instalando dependencias..."
venv/bin/pip install -r requirements.txt -q

# Crear directorio instance
mkdir -p instance

# Inicializar base de datos
echo "[3/4] Inicializando base de datos..."
venv/bin/python -c "
from app import create_app
app = create_app()
print('Base de datos creada exitosamente')
"

# Crear servicio systemd
echo "[4/4] Configurando servicio..."
SERVICE_NAME="sshpanel"
SERVICE_FILE="/etc/systemd/system/${SERVICE_NAME}.service"
PROJECT_PATH="$(pwd)"

cat > /tmp/sshpanel.service << EOF
[Unit]
Description=SSH Panel - Dropbear User Manager
After=network.target

[Service]
Type=simple
User=root
WorkingDirectory=${PROJECT_PATH}
ExecStart=${PROJECT_PATH}/venv/bin/python ${PROJECT_PATH}/wsgi.py
Restart=always
RestartSec=5
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=multi-user.target
EOF

if [ -d /etc/systemd/system ]; then
    cp /tmp/sshpanel.service ${SERVICE_FILE}
    systemctl daemon-reload
    systemctl enable ${SERVICE_NAME}
    systemctl start ${SERVICE_NAME}
    echo "  Servicio iniciado: systemctl status ${SERVICE_NAME}"
else
    echo "  AVISO: systemd no detectado. Inicia manualmente con:"
    echo "    cd ${PROJECT_PATH} && venv/bin/python wsgi.py &"
fi

echo ""
echo "========================================"
echo "  Instalacion completada!"
echo "  Panel: http://$(curl -s ifconfig.me):5000"
echo "  Usuario: admin"
echo "  Password: admin"
echo "========================================"
echo ""
echo "IMPORTANTE: Cambia la contrase??a del admin al iniciar sesion."
echo "Para ver logs: journalctl -u sshpanel -f"
