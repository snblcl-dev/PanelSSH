# PanelSSH — Documentación Completa

## ¿Qué es PanelSSH?

Panel web para administrar usuarios SSH/Dropbear en servidores Linux. Permite crear, renovar, bloquear, desbloquear, cambiar contraseñas y eliminar usuarios del sistema. Soporta servidores locales y remotos vía SSH (Paramiko). Incluye sistema de créditos para revendedores, usuarios demo con duración en minutos, y monitoreo de conexiones activas.

**Versión actual**: v1.0 con soporte SaaS multi-instancia.

---

## Stack Tecnológico

| Capa | Tecnología |
|------|-----------|
| **Backend** | Python 3, Flask, SQLAlchemy, Flask-Login |
| **Frontend** | Jinja2, Tailwind CSS CDN, Font Awesome 6 |
| **Base de datos** | SQLite (una por instancia) |
| **Servidor WSGI** | Waitress |
| **SSH remoto** | Paramiko |
| **Encriptación** | Fernet (cryptography) |
| **Despliegue** | systemd, Nginx, Certbot |
| **APIs externas** | Cloudflare (DNS automático) |

---

## Arquitectura

```
PanelSSH/
├── app.py                     # Factory: create_app(), blueprints, context processor
├── config.py                  # Config: DB path, secret key, límites, modo (PANEL_MODE)
├── wsgi.py                    # Entry point Waitress (usa SSHPANEL_PORT)
├── ssh_manager.py             # Capa sistema: comandos Linux locales/remotos
├── install.sh                 # Instalación standalone
├── instalar-maestro.sh        # Instalación panel maestro SaaS
├── crear-cliente.sh           # Crear nueva instancia SaaS
├── eliminar-cliente.sh        # Eliminar instancia SaaS
├── sync_expired.py            # Cron: bloquear expirados cada 5 min
├── check-expirations.sh       # Cron: apagar instancias vencidas
├── models/
│   └── __init__.py            # Modelos: Admin, Reseller, SSHUser, Server, ActivityLog, CreditConfig, Notification
├── routes/
│   ├── auth.py                # Login, registro, logout
│   ├── admin.py               # Panel admin (CRUD usuarios, resellers, servidores, logs, notificaciones)
│   ├── reseller_routes.py     # Panel reseller (limitado a sus usuarios)
│   └── api.py                 # API REST (stats, online, logs) con rate limiting
├── templates/                 # Templates Jinja2 (19 archivos)
│   ├── base.html              # Layout base: sidebar, CSS, JS
│   ├── auth/                  # login.html, register.html
│   ├── admin/                 # 13 templates (dashboard, users, servers, etc.)
│   └── reseller/              # 6 templates (dashboard, users, etc.)
├── master/                    # Panel Maestro (SaaS)
│   ├── app.py                 # Flask app del maestro (puerto 5100)
│   ├── wsgi.py                # Entry point
│   ├── config.json            # Hash contraseña, secret key
│   ├── instances.json         # Registro de instancias
│   ├── activity.log           # Log de acciones del maestro
│   └── templates/             # dashboard, login, profile
```

---

## Funcionalidades Principales

### Admin
- Dashboard con estadísticas (total usuarios, activos, bloqueados, expirados, online)
- CRUD completo de usuarios SSH (crear, renovar, bloquear, desbloquear, eliminar, cambiar contraseña)
- Usuarios demo: duración en minutos (10-180 min), máx 800 conexiones, badge especial
- Gestión de revendedores (activar, créditos, asignar, eliminar)
- Gestión de servidores remotos (agregar, probar conexión, activar/desactivar)
- Monitoreo: usuarios online, vista general, desconexión por exceso de conexiones
- Sistema de créditos configurable (por día / por usuario) con vista previa de costos
- Registro de actividad completo con paginación y filtros
- Notificaciones a revendedores

### Reseller
- Dashboard propio con sus estadísticas
- Crear/renovar usuarios (consume créditos, con vista previa de costo)
- Bloquear/desbloquear sus usuarios
- Monitoreo y desconexión de sus usuarios
- Limpiar expirados propios
- Registro de actividad propio
- Buzón de notificaciones del admin

### SaaS Multi-instancia
- Panel maestro: gestiona todas las instancias desde una UI
- Crear instancias con un clic (script `crear-cliente.sh`)
- Eliminar instancias (`eliminar-cliente.sh`)
- DNS automático vía API de Cloudflare
- Stats globales (CPU, RAM, disco, usuarios totales)
- Límites por instancia (max usuarios, revendedores, servidores)
- Modo mantenimiento por instancia
- Backup de DB por instancia
- Actualización masiva (git pull en todas las instancias)
- Fecha de vencimiento por instancia (se apaga sola)
- Login con página propia, rate limiting, bloqueo por fuerza bruta

---

## Cómo Funciona (Backend)

### Creación de usuario SSH

1. **Formulario** → valida username (regex `[a-z_][a-z0-9_-]{1,31}`), días, conexiones
2. **DB** → guarda en SQLite (contraseña encriptada con Fernet)
3. **Sistema** → `ssh_manager.py` decide local o remoto según `server_id`:
   - **Local**: ejecuta `useradd`, `chpasswd`, `chage -E`
   - **Remoto**: conecta vía Paramiko y ejecuta los mismos comandos
4. **Log** → registra en ActivityLog
5. **Flash** → muestra contraseña generada al admin

### Bloqueo de expirados

1. **Cron** ejecuta `sync_expired.py` cada 5 minutos
2. Busca usuarios con `expires_at <= now AND is_blocked=False`
3. Ejecuta `system_block_user()` → `passwd -l`, `usermod -e 1`, `pkill -u`

### Flujo de autenticación

1. Login → POST a `/auth/login`
2. Rate limiting: 10 intentos/min, bloqueo 15 min tras 5 fallos
3. Sesión: Flask-Login guarda user_id con prefijo (`a_1` = admin, `r_1` = reseller)
4. `before_request` en admin: fuerza cambio de contraseña si `must_change_password=True`

---

## Instalación Standalone (VPS individual)

```bash
# 1. Clonar
git clone https://github.com/snblcl-dev/PanelSSH.git
cd PanelSSH

# 2. Instalar
chmod +x install.sh
./install.sh

# 3. Acceder
# http://IP:5000
# Usuario: admin / Contraseña: admin (cambiar al entrar)

# 4. Enlazar dominio + SSL
cat > /etc/nginx/sites-available/panel << 'NGINX'
server {
    listen 80;
    server_name tudominio.com;
    location / {
        proxy_pass http://127.0.0.1:5000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
    client_max_body_size 10M;
}
NGINX

ln -sf /etc/nginx/sites-available/panel /etc/nginx/sites-enabled/panel
nginx -t && systemctl reload nginx
certbot --nginx -d tudominio.com

# 5. Cerrar puerto 5000 (ya no necesario)
ufw deny 5000
```

## Instalación SaaS (Multi-instancia)

```bash
# 1. Clonar e instalar base
git clone https://github.com/snblcl-dev/PanelSSH.git
cd PanelSSH
./install.sh

# 2. Activar Panel Maestro
chmod +x instalar-maestro.sh
./instalar-maestro.sh maestro.tudominio.com
# Pass: admin123 (cambiar desde Perfil)

# 3. Configurar Cloudflare (DNS automático)
cp .cloudflare.example .cloudflare
nano .cloudflare  # poner CF_API_TOKEN y CF_ZONE_ID

# 4. Crear primera instancia cliente
./crear-cliente.sh empresa1 cliente1.tudominio.com
```

---

## Comandos Útiles

### Gestión de servicios
```bash
# Panel principal standalone
systemctl status sshpanel
systemctl restart sshpanel
journalctl -u sshpanel -f

# Panel maestro SaaS
systemctl status sshpanel-maestro
systemctl restart sshpanel-maestro

# Instancia cliente
systemctl status sshpanel-empresa1
systemctl restart sshpanel-empresa1

# Reiniciar todas las instancias
systemctl restart 'sshpanel-*'
```

### Git
```bash
# Actualizar panel principal
cd /root/PanelSSH && git pull origin main && systemctl restart sshpanel

# Actualizar TODAS las instancias
cd /root/PanelSSH && git pull origin main
for d in instances/*/; do git -C "$d" pull; done
# O desde el panel maestro: botón "Actualizar Todo"

# Actualizar solo el maestro
systemctl restart sshpanel-maestro
```

### Base de datos
```bash
# Backup manual de una instancia
cp instances/empresa1/sshpanel.db /root/backups/empresa1-$(date +%Y%m%d).db

# Ver tamaño de DBs
du -sh instances/*/sshpanel.db

# Restaurar backup
systemctl stop sshpanel-empresa1
cp /root/backups/empresa1-20260715.db instances/empresa1/sshpanel.db
systemctl start sshpanel-empresa1
```

### Nginx
```bash
# Verificar configuración
nginx -t

# Recargar
systemctl reload nginx

# Ver logs
tail -f /var/log/nginx/access.log
tail -f /var/log/nginx/error.log

# Si un dominio eliminado muestra el panel maestro:
# Es porque Nginx cae al default_server. Ejecutar el bloque de abajo.
```

### Solución: dominio huérfano muestra panel maestro
```bash
cat > /etc/nginx/sites-available/default << 'NGINX'
server {
    listen 80 default_server;
    listen [::]:80 default_server;
    listen 443 ssl default_server;
    listen [::]:443 ssl default_server;
    server_name _;
    ssl_certificate /etc/ssl/certs/ssl-cert-snakeoil.pem;
    ssl_certificate_key /etc/ssl/private/ssl-cert-snakeoil.key;
    return 404;
}
NGINX
ln -sf /etc/nginx/sites-available/default /etc/nginx/sites-enabled/default
nginx -t && systemctl reload nginx
```

### Debugging
```bash
# Ver error específico de una instancia
journalctl -u sshpanel-empresa1 --no-pager -n 50

# Ver si el puerto está en uso
ss -tlnp | grep 5001

# Ver todas las instancias corriendo
systemctl list-units 'sshpanel-*' --no-pager

# Ver crons activos
crontab -l | grep -E "sync_expired|check-expirations"

# Ver logs del sync de expirados
tail -f /var/log/sshpanel-sync.log
```

---

## Variables de Entorno

| Variable | Default | Descripción |
|----------|---------|-------------|
| `SSHPANEL_INSTANCE_DIR` | dir del código | Directorio de la instancia (DB, secret key) |
| `SSHPANEL_PORT` | 5000 | Puerto donde corre la instancia |
| `SSHPANEL_MODE` | standalone | `standalone` o `saas` (sin usuarios locales) |
| `SSHPANEL_BACKEND` | local | `local` o `script` (para SSH manager) |
| `SECRET_KEY` | auto | Clave secreta de Flask (se auto-genera) |

---

## Flujo de Datos

```
┌─────────────────────────────────────────────────────┐
│                   Panel Maestro                      │
│  master/app.py (puerto 5100)                         │
│  ┌──────────────┐  ┌──────────────┐                 │
│  │ instances.json│  │ activity.log │                 │
│  └──────────────┘  └──────────────┘                 │
│  Crear/eliminar/gestionar instancias                 │
│  Limites, vencimientos, mantenimiento                │
│  Stats VPS (CPU/RAM/DISCO)                          │
└────────┬────────────────────────────────────────────┘
         │ ejecuta scripts
         ▼
┌─────────────────────────────────────────────────────┐
│              Instancias Cliente                      │
│  instances/empresa1/  (puerto 5001)                  │
│  instances/empresa2/  (puerto 5002)                  │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐          │
│  │ sshpanel.db│  │.secret_key│  │.limits  │          │
│  └──────────┘  └──────────┘  └──────────┘          │
│  Cada una es un PanelSSH independiente               │
│  con su propia DB, usuarios, configuración           │
└─────────────────────────────────────────────────────┘
```

---

## Ramas del Proyecto

| Rama | Propósito |
|------|-----------|
| `main` | Versión estable con todo (standalone + SaaS) |
| `feat/saas-multi-instancia` | Desarrollo del SaaS (ya mergeado a main) |
| `v1.0-standalone` | Tag congelado de la versión standalone (histórico) |

---

## Seguridad

- **Contraseñas SSH**: encriptadas con Fernet en la DB (modelo SSHUser)
- **Contraseñas admin/reseller**: hasheadas con Werkzeug
- **API**: rate limiting (30 req/min por IP)
- **Login**: protección fuerza bruta (5 intentos → bloqueo 15 min)
- **Comandos shell**: validación regex + `shlex.quote()` contra inyección
- **CSRF**: protección Flask-WTF en formularios POST
- **Panel maestro**: sesión Flask + contraseña hasheada + rate limiting
- **Cloudflare API token**: restringido por IP en el dashboard de Cloudflare
