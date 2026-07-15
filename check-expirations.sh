#!/bin/bash
# PanelSSH SaaS — Revisa y apaga instancias vencidas
# Ejecutar via cron cada hora: 0 * * * * /root/PanelSSH/check-expirations.sh

BASE_DIR="$(cd "$(dirname "$0")" && pwd)"
MASTER_DB="$BASE_DIR/master/instances.json"

if [ ! -f "$MASTER_DB" ]; then
    exit 0
fi

TODAY=$(date +%Y-%m-%d)

python3 -c "
import json, subprocess, sys
from datetime import datetime

try:
    data = json.load(open('$MASTER_DB'))
except:
    sys.exit(0)

today = '$TODAY'
stopped = 0
for inst in data:
    expires = inst.get('expires_at')
    if not expires:
        continue
    # Si la fecha de vencimiento ya pasó
    if expires < today:
        slug = inst['slug']
        # Verificar si el servicio está corriendo
        r = subprocess.run(['systemctl', 'is-active', f'sshpanel-{slug}'], capture_output=True, text=True)
        if r.stdout.strip() == 'active':
            subprocess.run(['systemctl', 'stop', f'sshpanel-{slug}'])
            stopped += 1
            print(f'[EXPIRED] {slug} — servicio detenido (venció {expires})')

if stopped > 0:
    print(f'[DONE] {stopped} instancia(s) detenida(s)')
"
