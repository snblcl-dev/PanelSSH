import os
import secrets
import base64
import hashlib
from pathlib import Path

# Directorio de la instancia (para multi-instancia SaaS)
# Si no se define, se usa el directorio donde esta el codigo
INSTANCE_DIR = os.environ.get('SSHPANEL_INSTANCE_DIR', str(Path(__file__).parent))


class Config:
    # Generar SECRET_KEY automaticamente si no existe
    _secret_file = Path(INSTANCE_DIR) / '.secret_key'
    if os.environ.get('SECRET_KEY'):
        SECRET_KEY = os.environ['SECRET_KEY']
    elif _secret_file.exists():
        SECRET_KEY = _secret_file.read_text().strip()
    else:
        SECRET_KEY = secrets.token_hex(64)
        _secret_file.write_text(SECRET_KEY)

    # Derivar clave Fernet del SECRET_KEY (32 bytes)
    FERNET_KEY = base64.urlsafe_b64encode(hashlib.sha256(SECRET_KEY.encode()).digest())

    # DB en el directorio de la instancia
    SQLALCHEMY_DATABASE_URI = f'sqlite:///{INSTANCE_DIR}/sshpanel.db'
    SQLALCHEMY_TRACK_MODIFICATIONS = False

    # Configuracion de Dropbear/SSH
    SSH_BACKEND = os.environ.get('SSH_BACKEND', 'local')

    SSH_SCRIPT_PATH = os.environ.get('SSH_SCRIPT_PATH', '/usr/local/bin/ssh-manager.sh')
    USER_SHELL = '/bin/false'
    SSH_GROUP = 'sshusers'
    SSH_HOME_BASE = '/home'

    # Limites
    MAX_CONNECTIONS_PER_USER = 10
    MAX_DAYS_PER_USER = 365

    # CSRF Protection
    WTF_CSRF_ENABLED = True
    WTF_CSRF_TIME_LIMIT = 3600  # 1 hora

    # Modo: 'standalone' (default) o 'saas' (sin usuarios locales)
    PANEL_MODE = os.environ.get('SSHPANEL_MODE', 'standalone')

    # Limites de plan (SaaS) — leidos de .limits en dir de instancia
    _limits_file = Path(INSTANCE_DIR) / '.limits'
    _limits = {}
    if _limits_file.exists():
        try:
            import json as _json
            _limits = _json.loads(_limits_file.read_text())
        except Exception:
            pass
    MAX_USERS_LIMIT = _limits.get('max_users', -1)
    MAX_RESELLERS_LIMIT = _limits.get('max_resellers', -1)
    MAX_SERVERS_LIMIT = _limits.get('max_servers', -1)
