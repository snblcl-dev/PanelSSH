import os
import secrets
import base64
import hashlib
from pathlib import Path


class Config:
    # Generar SECRET_KEY automaticamente si no existe
    _secret_file = Path(__file__).parent / '.secret_key'
    if os.environ.get('SECRET_KEY'):
        SECRET_KEY = os.environ['SECRET_KEY']
    elif _secret_file.exists():
        SECRET_KEY = _secret_file.read_text().strip()
    else:
        SECRET_KEY = secrets.token_hex(64)
        _secret_file.write_text(SECRET_KEY)
    
    # Derivar clave Fernet del SECRET_KEY (32 bytes)
    FERNET_KEY = base64.urlsafe_b64encode(hashlib.sha256(SECRET_KEY.encode()).digest())
    
    SQLALCHEMY_DATABASE_URI = 'sqlite:///sshpanel.db'
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
