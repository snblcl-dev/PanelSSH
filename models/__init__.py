from flask_sqlalchemy import SQLAlchemy
from flask_login import UserMixin
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import datetime, timedelta
import random
import string
import os
import re
from datetime import datetime, timedelta
from cryptography.fernet import Fernet, InvalidToken

db = SQLAlchemy()


def get_fernet():
    from flask import current_app
    from config import Config
    try:
        key = getattr(current_app, '_fernet_key', None)
        if key is None:
            key = current_app.config.get('FERNET_KEY') or Config.FERNET_KEY
            current_app._fernet_key = key
        return Fernet(key)
    except RuntimeError:
        return Fernet(Config.FERNET_KEY)


def encrypt_password(plain):
    if not plain:
        return None
    return get_fernet().encrypt(plain.encode()).decode()


def decrypt_password(cipher):
    if not cipher:
        return None
    try:
        return get_fernet().decrypt(cipher.encode()).decode()
    except InvalidToken:
        return None


class Admin(UserMixin, db.Model):
    __tablename__ = 'admins'
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    email = db.Column(db.String(120), unique=True)
    is_superadmin = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    must_change_password = db.Column(db.Boolean, default=False)
    login_attempts = db.Column(db.Integer, default=0)
    locked_until = db.Column(db.DateTime, nullable=True)

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)

    def get_id(self):
        return f'a_{self.id}'


class Reseller(UserMixin, db.Model):
    __tablename__ = 'resellers'
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    email = db.Column(db.String(120))
    credits = db.Column(db.Integer, default=0)
    is_active = db.Column(db.Boolean, default=False)
    created_by = db.Column(db.Integer, db.ForeignKey('admins.id'))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    notes = db.Column(db.Text, default='')
    last_activity = db.Column(db.DateTime, nullable=True)
    total_credits_received = db.Column(db.Integer, default=0)
    login_attempts = db.Column(db.Integer, default=0)
    locked_until = db.Column(db.DateTime, nullable=True)

    def get_id(self):
        return f'r_{self.id}'

    # Relación
    creator = db.relationship('Admin', backref='created_resellers')
    users = db.relationship('SSHUser', backref='owner_reseller', lazy='dynamic',
                            foreign_keys='SSHUser.created_by_reseller')

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)

    def get_effective_config(self):
        """Devuelve la configuracion de creditos del reseller o la global si no tiene personalizada"""
        if self.credit_mode:
            return self
        return CreditConfig.get_config()

    def can_create_user(self, days, max_connections):
        """Verifica si el reseller tiene creditos suficientes"""
        needed = CreditConfig.get_config().calculate_cost(days, max_connections)
        return self.credits >= needed

    def deduct_credits(self, days, max_connections):
        """Descuenta creditos segun la config global"""
        cost = CreditConfig.get_config().calculate_cost(days, max_connections)
        self.credits -= cost
        return cost

    def get_max_connections(self):
        return 3


class SSHUser(db.Model):
    __tablename__ = 'ssh_users'
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False, index=True)
    password = db.Column(db.String(255), nullable=False)

    # Almacenamiento interno encriptado — NO acceder directamente,
    # usar la propiedad password_plain (getter/setter)
    _password_encrypted = db.Column('password_plain', db.String(255), nullable=False)

    @property
    def password_plain(self):
        """Desencripta y devuelve la contraseña en texto plano."""
        val = decrypt_password(self._password_encrypted)
        if val is None:
            # Contraseña legacy sin encriptar — devolver tal cual
            return self._password_encrypted
        return val

    @password_plain.setter
    def password_plain(self, value):
        """Encripta la contraseña antes de guardar en BD."""
        self._password_encrypted = encrypt_password(value)

    max_connections = db.Column(db.Integer, default=1)
    days_duration = db.Column(db.Integer, default=30)
    is_demo = db.Column(db.Boolean, default=False)
    duration_minutes = db.Column(db.Integer, nullable=True)  # Solo para usuarios demo

    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    expires_at = db.Column(db.DateTime, nullable=False)
    last_renewed_at = db.Column(db.DateTime, default=datetime.utcnow)

    is_active = db.Column(db.Boolean, default=True)
    is_blocked = db.Column(db.Boolean, default=False)
    server_id = db.Column(db.Integer, db.ForeignKey('servers.id'), nullable=True)

    server = db.relationship('Server', backref='users')

    # Relacion: quien creo el usuario
    created_by_admin = db.Column(db.Integer, db.ForeignKey('admins.id'), nullable=True)
    created_by_reseller = db.Column(db.Integer, db.ForeignKey('resellers.id'), nullable=True)

    creator_admin = db.relationship('Admin', backref='created_users')

    def is_expired(self):
        return datetime.utcnow() > self.expires_at

    def days_remaining(self):
        if self.is_expired():
            return 0
        delta = self.expires_at - datetime.utcnow()
        return max(0, delta.days)

    def renew(self, extra_days, new_max_connections=None):
        """Renueva un usuario agregando días extra"""
        if self.is_expired():
            self.expires_at = datetime.utcnow() + timedelta(days=extra_days)
        else:
            self.expires_at += timedelta(days=extra_days)

        if new_max_connections is not None:
            self.max_connections = new_max_connections

        if self.is_blocked:
            self.is_blocked = False

        self.is_active = True
        self.last_renewed_at = datetime.utcnow()

    def reset_password(self):
        """Genera una nueva contraseña automáticamente"""
        chars = string.ascii_letters + string.digits
        new_pass = ''.join(random.choice(chars) for _ in range(10))
        self.password_plain = new_pass  # El setter encripta automáticamente
        self.password = generate_password_hash(new_pass)
        return new_pass

    def to_dict(self):
        return {
            'id': self.id,
            'username': self.username,
            'max_connections': self.max_connections,
            'days_duration': self.days_duration,
            'created_at': self.created_at.strftime('%Y-%m-%d %H:%M'),
            'expires_at': self.expires_at.strftime('%Y-%m-%d'),
            'days_remaining': self.days_remaining(),
            'is_expired': self.is_expired(),
            'is_active': self.is_active,
            'is_blocked': self.is_blocked,
        }


class ActivityLog(db.Model):
    __tablename__ = 'activity_logs'
    id = db.Column(db.Integer, primary_key=True)
    action = db.Column(db.String(50), nullable=False)  # create, renew, block, unblock, delete, etc.
    description = db.Column(db.Text, nullable=False)
    performed_by = db.Column(db.String(80), nullable=False)  # admin o reseller
    performed_by_type = db.Column(db.String(20), nullable=False)  # 'admin' o 'reseller'
    target_user = db.Column(db.String(80), nullable=True)
    target_type = db.Column(db.String(20), default='ssh_user')  # ssh_user, reseller, etc.
    ip_address = db.Column(db.String(45), nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, index=True)


class Notification(db.Model):
    """Mensajes del admin para revendedores"""
    __tablename__ = 'notifications'
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(200), nullable=False)
    message = db.Column(db.Text, nullable=False)
    reseller_id = db.Column(db.Integer, db.ForeignKey('resellers.id'), nullable=True)  # NULL = para todos
    is_read = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    created_by = db.Column(db.Integer, db.ForeignKey('admins.id'), nullable=False)

    reseller = db.relationship('Reseller', backref='notifications')
    admin = db.relationship('Admin', backref='sent_notifications')


class CreditConfig(db.Model):
    """Configuracion del sistema de creditos"""
    __tablename__ = 'credit_config'
    id = db.Column(db.Integer, primary_key=True)
    mode = db.Column(db.String(20), default='per_day')
    cost_per_day = db.Column(db.Integer, default=1)
    cost_per_user = db.Column(db.Integer, default=30)
    cost_per_extra_connection = db.Column(db.Integer, default=15)

    @classmethod
    def get_config(cls):
        config = cls.query.first()
        if not config:
            config = cls(mode='per_day', cost_per_day=1, cost_per_user=30, cost_per_extra_connection=15)
            db.session.add(config)
            db.session.commit()
        return config

    def calculate_cost(self, days, max_connections):
        """Calcula el costo en creditos segun la configuracion actual"""
        if self.mode == 'per_user':
            cost = self.cost_per_user
        else:
            cost = days * self.cost_per_day
        
        if max_connections > 1:
            cost += (max_connections - 1) * self.cost_per_extra_connection
        
        return cost

    def get_max_days(self):
        """Devuelve el maximo de dias permitido segun el modo"""
        return 30 if self.mode == 'per_user' else 365


class Server(db.Model):
    """Servidor remoto con Dropbear"""
    __tablename__ = 'servers'
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    host = db.Column(db.String(255), nullable=False)
    port = db.Column(db.Integer, default=22)
    dropbear_port = db.Column(db.Integer, default=22)
    ssh_user = db.Column(db.String(100), default='root')
    location = db.Column(db.String(100), default='')
    is_active = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    auth_method = db.Column(db.String(20), default='key')  # 'key' o 'password'
    password_encrypted = db.Column(db.Text, nullable=True)
    ssh_key_path = db.Column(db.String(512), nullable=True)  # Ruta a llave SSH personalizada
    
    @property
    def password(self):
        if self.password_encrypted:
            return decrypt_password(self.password_encrypted)
        return None
    
    @password.setter
    def password(self, value):
        self.password_encrypted = encrypt_password(value)
    
    def test_connection(self):
        import paramiko
        try:
            ssh = paramiko.SSHClient()
            ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            kwargs = {'timeout': 10}
            if self.auth_method == 'password' and self.password:
                kwargs['password'] = self.password
            else:
                kwargs['key_filename'] = self.ssh_key_path or os.path.expanduser('~/.ssh/id_rsa')
            ssh.connect(self.host, port=self.port, username=self.ssh_user, **kwargs)
            ssh.close()
            return True, 'Conexion exitosa'
        except Exception as e:
            return False, str(e)


def generate_password(length=12):
    """Genera una contraseña aleatoria segura y compatible con SSH"""
    # Solo caracteres seguros: sin $ % " & que dan problemas en shells y clientes SSH
    chars = string.ascii_letters + string.digits + "-_."
    # Asegurar al menos 1 letra, 1 numero y 1 especial
    password = [
        random.choice(string.ascii_lowercase),
        random.choice(string.ascii_uppercase),
        random.choice(string.digits),
        random.choice("-_.")
    ]
    if length > 4:
        password += [random.choice(chars) for _ in range(length - 4)]
    random.shuffle(password)
    return ''.join(password)


# Patrón seguro para nombres de usuario Linux (mismo que ssh_manager.py)
_VALID_USERNAME_RE = re.compile(r'^[a-z_][a-z0-9_-]{1,31}$')


def validate_username(username):
    """
    Valida que un nombre de usuario solo contenga caracteres seguros.
    Retorna (es_valido, mensaje_error).
    """
    if not _VALID_USERNAME_RE.match(username):
        return False, (
            f"Nombre de usuario inválido: '{username}'. "
            "Solo minúsculas, números, guiones y guiones bajos (máx 32 caracteres)."
        )
    return True, ""


def init_db():
    """Crea tablas y admin por defecto"""
    db.create_all()
    
    # Migración: agregar columnas nuevas si no existen
    try:
        from sqlalchemy import inspect
        inspector = inspect(db.engine)
        columns = [c['name'] for c in inspector.get_columns('resellers')]
        if 'last_activity' not in columns:
            db.session.execute(db.text('ALTER TABLE resellers ADD COLUMN last_activity DATETIME'))
        if 'total_credits_received' not in columns:
            db.session.execute(db.text('ALTER TABLE resellers ADD COLUMN total_credits_received INTEGER DEFAULT 0'))
        db.session.commit()
    except Exception:
        db.session.rollback()
    
    # Migracion: columnas nuevas en Admin
    try:
        inspector = inspect(db.engine)
        admin_columns = [c['name'] for c in inspector.get_columns('admins')]
        if 'must_change_password' not in admin_columns:
            db.session.execute(db.text('ALTER TABLE admins ADD COLUMN must_change_password BOOLEAN DEFAULT 0'))
        if 'login_attempts' not in admin_columns:
            db.session.execute(db.text('ALTER TABLE admins ADD COLUMN login_attempts INTEGER DEFAULT 0'))
        if 'locked_until' not in admin_columns:
            db.session.execute(db.text('ALTER TABLE admins ADD COLUMN locked_until DATETIME'))
        db.session.commit()
    except Exception:
        db.session.rollback()
    
    # Migracion: columnas nuevas en Reseller
    try:
        inspector = inspect(db.engine)
        reseller_columns = [c['name'] for c in inspector.get_columns('resellers')]
        for col in ['login_attempts', 'locked_until']:
            if col not in reseller_columns:
                db.session.execute(db.text('ALTER TABLE resellers ADD COLUMN %s %s' % (
                    col, 'DATETIME' if col == 'locked_until' else 'INTEGER DEFAULT 0')))
        for col in ['credit_mode', 'cost_per_day', 'cost_per_user', 'cost_per_extra_connection', 'max_days']:
            if col not in reseller_columns:
                db.session.execute(db.text('ALTER TABLE resellers ADD COLUMN %s %s' % (
                    col, 'VARCHAR(20)' if col == 'credit_mode' else 'INTEGER')))
        db.session.commit()
    except Exception:
        db.session.rollback()
    
    # Migracion: crear CreditConfig por defecto si no existe
    try:
        if not CreditConfig.query.first():
            db.session.add(CreditConfig(mode='per_day', cost_per_day=1, cost_per_user=30, cost_per_extra_connection=15))
            db.session.commit()
    except Exception:
        db.session.rollback()
    
    # Migracion: columna server_id en SSHUser
    try:
        inspector = inspect(db.engine)
        user_columns = [c['name'] for c in inspector.get_columns('ssh_users')]
        if 'server_id' not in user_columns:
            db.session.execute(db.text('ALTER TABLE ssh_users ADD COLUMN server_id INTEGER REFERENCES servers(id)'))
        if 'is_demo' not in user_columns:
            db.session.execute(db.text('ALTER TABLE ssh_users ADD COLUMN is_demo BOOLEAN DEFAULT 0'))
        if 'duration_minutes' not in user_columns:
            db.session.execute(db.text('ALTER TABLE ssh_users ADD COLUMN duration_minutes INTEGER'))
        db.session.commit()
    except Exception:
        db.session.rollback()
    
    # Migracion: columnas nuevas en Server
    try:
        inspector = inspect(db.engine)
        server_columns = [c['name'] for c in inspector.get_columns('servers')]
        if 'auth_method' not in server_columns:
            db.session.execute(db.text("ALTER TABLE servers ADD COLUMN auth_method VARCHAR(20) DEFAULT 'key'"))
        if 'password' not in server_columns:
            db.session.execute(db.text('ALTER TABLE servers ADD COLUMN password VARCHAR(255)'))
        if 'password_encrypted' not in server_columns:
            db.session.execute(db.text('ALTER TABLE servers ADD COLUMN password_encrypted TEXT'))
        if 'ssh_key_path' not in server_columns:
            db.session.execute(db.text('ALTER TABLE servers ADD COLUMN ssh_key_path VARCHAR(512)'))
        db.session.commit()
    except Exception:
        db.session.rollback()
    
    # Migracion: encriptar contraseñas existentes en servidores
    try:
        from sqlalchemy import text as sql_text
        servers_con_plain = db.session.execute(
            sql_text("SELECT id, password FROM servers WHERE password IS NOT NULL AND password != '' AND (password_encrypted IS NULL OR password_encrypted = '')")
        ).fetchall()
        for sid, plain in servers_con_plain:
            encrypted = encrypt_password(plain)
            db.session.execute(
                sql_text("UPDATE servers SET password_encrypted = :enc WHERE id = :id"),
                {'enc': encrypted, 'id': sid}
            )
        if servers_con_plain:
            db.session.commit()
    except Exception:
        db.session.rollback()

    # Migracion: encriptar contraseñas SSH existentes
    try:
        from sqlalchemy import text as sql_text
        users_plain = db.session.execute(
            sql_text("SELECT id, password_plain FROM ssh_users WHERE password_plain IS NOT NULL AND password_plain != ''")
        ).fetchall()
        count = 0
        for uid, plain in users_plain:
            if decrypt_password(plain) is None:
                encrypted = encrypt_password(plain)
                db.session.execute(
                    sql_text("UPDATE ssh_users SET password_plain = :enc WHERE id = :id"),
                    {'enc': encrypted, 'id': uid}
                )
                count += 1
        if count > 0:
            db.session.commit()
    except Exception:
        db.session.rollback()

    # Migracion: tabla notifications
    try:
        inspector = inspect(db.engine)
        if 'notifications' not in inspector.get_table_names():
            db.create_all()
            db.session.commit()
    except Exception:
        db.session.rollback()

    # Crear admin por defecto si no existe
    if not Admin.query.filter_by(username='admin').first():
        admin = Admin(username='admin', email='admin@sshpanel.local', must_change_password=True)
        admin.set_password('admin')
        db.session.add(admin)
        db.session.commit()
