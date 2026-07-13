from flask import Blueprint, render_template, redirect, url_for, flash, request
from flask_login import login_user, logout_user, login_required, current_user
from models import db, Admin, Reseller, ActivityLog

auth_bp = Blueprint('auth', __name__)


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
            
            # Fuerza bruta: verificar si la cuenta está bloqueada
            if user and user.locked_until:
                from datetime import datetime
                if datetime.utcnow() < user.locked_until:
                    mins = int((user.locked_until - datetime.utcnow()).total_seconds() / 60)
                    flash(f'Cuenta bloqueada por intentos fallidos. Intenta de nuevo en {mins} minuto(s).', 'danger')
                    return render_template('auth/login.html')
                else:
                    # Bloqueo expirado, reiniciar
                    user.login_attempts = 0
                    user.locked_until = None
                    db.session.commit()
        else:
            user = Reseller.query.filter_by(username=username).first()
            user_type = 'reseller'
            if user and not user.is_active:
                flash('Tu cuenta de revendedor no ha sido activada aún. Contacta al administrador.', 'danger')
                return render_template('auth/login.html')
        
        if user and user.check_password(password):
            # Login exitoso: reiniciar intentos
            if user_type == 'admin':
                user.login_attempts = 0
                user.locked_until = None
                db.session.commit()
            
            login_user(user)
            
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
            # Login fallido: incrementar intentos para admin
            if user_type == 'admin' and user:
                from datetime import datetime, timedelta
                user.login_attempts = (user.login_attempts or 0) + 1
                if user.login_attempts >= 5:
                    user.locked_until = datetime.utcnow() + timedelta(minutes=15)
                    flash('Demasiados intentos fallidos. Cuenta bloqueada por 15 minutos.', 'danger')
                else:
                    restantes = 5 - user.login_attempts
                    flash(f'Usuario o contraseña incorrectos. {restantes} intento(s) restante(s).', 'danger')
                db.session.commit()
            else:
                flash('Usuario o contraseña incorrectos', 'danger')
    
    return render_template('auth/login.html')


@auth_bp.route('/register', methods=['GET', 'POST'])
def register():
    """Registro público para revendedores"""
    if request.method == 'POST':
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
