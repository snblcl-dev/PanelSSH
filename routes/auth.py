from datetime import datetime, timedelta
from flask import Blueprint, render_template, redirect, url_for, flash, request
from flask_login import login_user, logout_user, login_required, current_user
from models import db, Admin, Reseller, ActivityLog

auth_bp = Blueprint('auth', __name__)

MAX_LOGIN_ATTEMPTS = 5
LOCKOUT_MINUTES = 15


def _check_brute_force(user):
    """Verifica si la cuenta está bloqueada por fuerza bruta. Retorna (bloqueado, mensaje)."""
    if not user or not user.locked_until:
        return False, None
    if datetime.utcnow() < user.locked_until:
        mins = int((user.locked_until - datetime.utcnow()).total_seconds() / 60) + 1
        return True, f'Cuenta bloqueada por intentos fallidos. Intenta de nuevo en {mins} minuto(s).'
    # Bloqueo expirado, reiniciar
    user.login_attempts = 0
    user.locked_until = None
    db.session.commit()
    return False, None


def _handle_failed_login(user):
    """Incrementa intentos fallidos y bloquea si se excede el límite."""
    if not user:
        return
    user.login_attempts = (user.login_attempts or 0) + 1
    if user.login_attempts >= MAX_LOGIN_ATTEMPTS:
        user.locked_until = datetime.utcnow() + timedelta(minutes=LOCKOUT_MINUTES)
        flash(f'Demasiados intentos fallidos. Cuenta bloqueada por {LOCKOUT_MINUTES} minutos.', 'danger')
    else:
        restantes = MAX_LOGIN_ATTEMPTS - user.login_attempts
        flash(f'Usuario o contraseña incorrectos. {restantes} intento(s) restante(s).', 'danger')
    db.session.commit()


def _reset_login_attempts(user):
    """Reinicia el contador de intentos tras login exitoso."""
    if user and hasattr(user, 'login_attempts'):
        user.login_attempts = 0
        user.locked_until = None


@auth_bp.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '')
        login_as = request.form.get('login_as', 'admin')

        if not username or not password:
            flash('Por favor ingresa usuario y contraseña', 'warning')
            return render_template('auth/login.html')

        user = None
        user_type = None

        if login_as == 'admin':
            user = Admin.query.filter_by(username=username).first()
            user_type = 'admin'
        else:
            user = Reseller.query.filter_by(username=username).first()
            user_type = 'reseller'

        # Protección fuerza bruta (ambos roles)
        bloq, msg = _check_brute_force(user)
        if bloq:
            flash(msg, 'danger')
            return render_template('auth/login.html')

        # Verificar si el reseller está activo
        if user_type == 'reseller' and user and not user.is_active:
            flash('Tu cuenta de revendedor no ha sido activada aún. Contacta al administrador.', 'danger')
            return render_template('auth/login.html')

        if user and user.check_password(password):
            _reset_login_attempts(user)
            db.session.commit()
            login_user(user, remember=True)

            log = ActivityLog(
                action='login',
                description=f'Inicio de sesión como {user_type}: {username}',
                performed_by=username,
                performed_by_type=user_type,
                ip_address=request.remote_addr
            )
            db.session.add(log)
            db.session.commit()

            # Forzar cambio de contraseña si es admin con must_change_password
            if user_type == 'admin' and getattr(user, 'must_change_password', False):
                flash('Por seguridad, debes cambiar tu contraseña antes de continuar.', 'warning')
                return redirect(url_for('admin.profile'))

            if user_type == 'admin':
                return redirect(url_for('admin.dashboard'))
            else:
                return redirect(url_for('reseller.reseller_dashboard'))
        else:
            _handle_failed_login(user)

    return render_template('auth/login.html')


@auth_bp.route('/register', methods=['GET', 'POST'])
def register():
    """Registro público para revendedores"""
    if request.method == 'POST':
        # Verificar límite de revendedores
        from flask import current_app
        max_resellers = current_app.config.get('MAX_RESELLERS_LIMIT', -1)
        if max_resellers != -1:
            current_count = Reseller.query.count()
            if current_count >= max_resellers:
                flash(f'No se aceptan más revendedores en este momento. Límite alcanzado.', 'danger')
                return render_template('auth/register.html')
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '')
        email = request.form.get('email', '').strip()
        
        if not username or not password:
            flash('Completa todos los campos requeridos', 'warning')
            return render_template('auth/register.html')
        
        if len(username) < 3:
            flash('El nombre de usuario debe tener al menos 3 caracteres', 'warning')
            return render_template('auth/register.html')
        
        if len(password) < 6:
            flash('La contraseña debe tener al menos 6 caracteres', 'warning')
            return render_template('auth/register.html')
        
        existing = Reseller.query.filter_by(username=username).first()
        if existing:
            flash('El nombre de usuario ya está registrado', 'danger')
            return render_template('auth/register.html')
        
        reseller = Reseller(
            username=username,
            email=email,
            credits=0,
            is_active=False,
            notes='Pendiente de aprobación'
        )
        reseller.set_password(password)
        
        db.session.add(reseller)
        
        # Log para admin
        log = ActivityLog(
            action='reseller_register',
            description=f'Nuevo revendedor registrado: {username} (pendiente de activación)',
            performed_by=username,
            performed_by_type='reseller',
            ip_address=request.remote_addr
        )
        db.session.add(log)
        db.session.commit()
        
        flash('Registro exitoso. Un administrador activará tu cuenta pronto.', 'success')
        return redirect(url_for('auth.login'))
    
    return render_template('auth/register.html')


@auth_bp.route('/logout')
@login_required
def logout():
    logout_user()
    flash('Sesión cerrada correctamente', 'info')
    return redirect(url_for('auth.login'))
