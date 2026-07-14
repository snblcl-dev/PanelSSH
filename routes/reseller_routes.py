"""
Rutas para el panel de Revendedores/Resellers
"""
from datetime import datetime, timedelta
from flask import Blueprint, render_template, redirect, url_for, flash, request
from flask_login import login_required, current_user
from models import db, SSHUser, ActivityLog, generate_password, CreditConfig, Server, validate_username
from ssh_manager import (
    system_create_user, system_delete_user, system_block_user,
    system_unblock_user, system_change_password, system_get_online_users,
    system_disconnect_user, system_execute, system_sync_expired_users
)

reseller_bp = Blueprint('reseller', __name__)


def reseller_required(f):
    from functools import wraps
    @wraps(f)
    @login_required
    def decorated(*args, **kwargs):
        from models import Reseller
        if not isinstance(current_user, Reseller):
            flash('Acceso denegado. Esta sección es solo para revendedores.', 'danger')
            return redirect(url_for('auth.login'))
        if not current_user.is_active:
            flash('Tu cuenta no está activada. Contacta al administrador.', 'danger')
            return redirect(url_for('auth.login'))
        return f(*args, **kwargs)
    return decorated


def log_action(action, description, target_user=None, target_type='ssh_user'):
    log = ActivityLog(
        action=action,
        description=description,
        performed_by=current_user.username,
        performed_by_type='reseller',
        target_user=target_user,
        target_type=target_type,
        ip_address=request.remote_addr
    )
    db.session.add(log)
    db.session.commit()


@reseller_bp.route('/')
@reseller_required
def reseller_dashboard():
    """Dashboard del revendedor"""
    # Sincronizar expirados: bloquear en el sistema a los que ya vencieron
    system_sync_expired_users()
    now = datetime.utcnow()
    
    # Solo sus propios usuarios
    my_users = SSHUser.query.filter_by(created_by_reseller=current_user.id)
    
    total_users = my_users.count()
    active_users = my_users.filter(
        SSHUser.is_blocked == False,
        SSHUser.expires_at > now
    ).count()
    blocked_users = my_users.filter_by(is_blocked=True).count()
    expired_users = my_users.filter(SSHUser.expires_at <= now).count()
    
    return render_template('reseller/dashboard.html',
        total_users=total_users,
        active_users=active_users,
        blocked_users=blocked_users,
        expired_users=expired_users,
        credits=current_user.credits,
        credit_config=CreditConfig.get_config(),
        servers=Server.query.filter_by(is_active=True).all()
    )


@reseller_bp.route('/users')
@reseller_required
def users():
    """Usuarios creados por este reseller"""
    search = request.args.get('search', '').strip()
    status_filter = request.args.get('status', '')
    
    query = SSHUser.query.filter_by(created_by_reseller=current_user.id)
    
    if search:
        query = query.filter(SSHUser.username.contains(search))
    if status_filter == 'active':
        query = query.filter(SSHUser.is_blocked == False, SSHUser.expires_at > datetime.utcnow())
    elif status_filter == 'blocked':
        query = query.filter_by(is_blocked=True)
    elif status_filter == 'expired':
        query = query.filter(SSHUser.expires_at <= datetime.utcnow())
    
    users_list = query.order_by(SSHUser.created_at.desc()).all()
    
    return render_template('reseller/users.html', users=users_list,
                          search=search, status_filter=status_filter,
                          max_connections=current_user.get_max_connections(),
                          credit_config=CreditConfig.get_config(),
                          servers=Server.query.filter_by(is_active=True).all())


@reseller_bp.route('/users/create', methods=['POST'])
@reseller_required
def user_create():
    """Crear usuario (consume créditos)"""
    username = request.form.get('username', '').strip()
    days = request.form.get('days', 30, type=int)
    max_connections = request.form.get('max_connections', 1, type=int)
    server_id = request.form.get('server_id', type=int)
    
    if not username:
        flash('Nombre de usuario requerido', 'danger')
        return redirect(url_for('reseller.users'))

    # Validar formato de username
    valid, msg = validate_username(username)
    if not valid:
        flash(msg, 'danger')
        return redirect(url_for('reseller.users'))

    if SSHUser.query.filter_by(username=username).first():
        flash(f'El usuario "{username}" ya existe', 'danger')
        return redirect(url_for('reseller.users'))
    
    max_days = CreditConfig.get_config().get_max_days()
    if days > max_days:
        flash(f'El maximo de dias permitido es {max_days}', 'danger')
        return redirect(url_for('reseller.users'))
    days = max(1, days)
    max_connections = max(1, min(max_connections, 800))
    
    # Verificar creditos
    if not current_user.can_create_user(days, max_connections):
        config = CreditConfig.get_config()
        needed = config.calculate_cost(days, max_connections)
        flash(f'Creditos insuficientes. Necesitas {needed} creditos (tienes {current_user.credits})', 'danger')
        return redirect(url_for('reseller.users'))
    
    password = generate_password()
    expires_at = datetime.utcnow() + timedelta(days=days)
    
    from werkzeug.security import generate_password_hash
    user = SSHUser(
        username=username,
        password=generate_password_hash(password),
        password_plain=password,
        max_connections=max_connections,
        days_duration=days,
        expires_at=expires_at,
        is_active=True,
        is_blocked=False,
        created_by_reseller=current_user.id,
        server_id=server_id if server_id else None
    )
    
    # Descontar créditos
    cost = current_user.deduct_credits(days, max_connections)
    
    db.session.add(user)
    db.session.commit()
    
    # Sistema
    system_execute(user, 'create_user', username, password, expires_at, max_connections)
    
    log_action('create', f'Usuario creado: {username} ({days} días, {max_connections} conexiones, costo: {cost} créditos)',
               target_user=username)
    
    flash(f'Usuario "{username}" creado. Contraseña: {password} | Créditos restantes: {current_user.credits}', 'success')
    return redirect(url_for('reseller.users'))


@reseller_bp.route('/users/renew', methods=['POST'])
@reseller_required
def user_renew():
    """Renovar usuario"""
    user_id = request.form.get('user_id', type=int)
    extra_days = request.form.get('extra_days', 30, type=int)
    new_max_connections = request.form.get('new_max_connections', type=int)
    
    user = SSHUser.query.get_or_404(user_id)
    
    # Verificar que pertenece a este reseller
    if user.created_by_reseller != current_user.id:
        flash('No tienes permiso para modificar este usuario', 'danger')
        return redirect(url_for('reseller.users'))
    
    max_days = CreditConfig.get_config().get_max_days()
    if extra_days > max_days:
        flash(f'El maximo de dias para renovar es {max_days}', 'danger')
        return redirect(url_for('reseller.users'))
    extra_days = max(1, extra_days)
    
    if new_max_connections:
        new_max_connections = max(1, min(new_max_connections, current_user.get_max_connections()))
    
    # Calcular costo
    config = CreditConfig.get_config()
    current_max = new_max_connections if new_max_connections else user.max_connections
    cost = config.calculate_cost(extra_days, current_max)
    
    if current_user.credits < cost:
        flash(f'Créditos insuficientes. Necesitas {cost} créditos (tienes {current_user.credits})', 'danger')
        return redirect(url_for('reseller.users'))
    
    user.renew(extra_days, new_max_connections)
    current_user.credits -= cost
    db.session.commit()
    
    system_execute(user, "set_expiry", user.username, user.expires_at)
    
    log_action('renew', f'Usuario renovado: {user.username} (+{extra_days} días, costo: {cost} créditos)',
               target_user=user.username)
    
    flash(f'Usuario "{user.username}" renovado (+{extra_days} días, -{cost} créditos)', 'success')
    return redirect(url_for('reseller.users'))


@reseller_bp.route('/users/change-password', methods=['POST'])
@reseller_required
def user_change_password():
    """Cambiar contraseña de un usuario del reseller"""
    user_id = request.form.get('user_id', type=int)
    user = SSHUser.query.get_or_404(user_id)
    
    if user.created_by_reseller != current_user.id:
        flash('No tienes permiso para modificar este usuario', 'danger')
        return redirect(url_for('reseller.users'))
    
    new_password = user.reset_password()
    from werkzeug.security import generate_password_hash
    user.password = generate_password_hash(new_password)
    db.session.commit()
    
    system_execute(user, "change_password", user.username, new_password)
    
    log_action('change_password', f'Contraseña cambiada para: {user.username}',
               target_user=user.username)
    
    flash(f'Nueva contraseña para "{user.username}": {new_password}', 'success')
    return redirect(url_for('reseller.users'))


@reseller_bp.route('/users/block/<int:user_id>', methods=['POST'])
@reseller_required
def user_block(user_id):
    user = SSHUser.query.get_or_404(user_id)
    if user.created_by_reseller != current_user.id:
        flash('No tienes permiso para modificar este usuario', 'danger')
        return redirect(url_for('reseller.users'))
    
    user.is_blocked = True
    db.session.commit()
    system_execute(user, "block_user", user.username)
    log_action('block', f'Usuario bloqueado: {user.username}', target_user=user.username)
    flash(f'Usuario "{user.username}" bloqueado', 'success')
    return redirect(url_for('reseller.users'))


@reseller_bp.route('/users/unblock/<int:user_id>', methods=['POST'])
@reseller_required
def user_unblock(user_id):
    user = SSHUser.query.get_or_404(user_id)
    if user.created_by_reseller != current_user.id:
        flash('No tienes permiso para modificar este usuario', 'danger')
        return redirect(url_for('reseller.users'))
    
    user.is_blocked = False
    db.session.commit()
    system_execute(user, "unblock_user", user.username)
    log_action('unblock', f'Usuario desbloqueado: {user.username}', target_user=user.username)
    flash(f'Usuario "{user.username}" desbloqueado', 'success')
    return redirect(url_for('reseller.users'))


# ============ ONLINE (solo usuarios propios) ============

@reseller_bp.route('/online')
@reseller_required
def reseller_online():
    """Usuarios conectados creados por este reseller"""
    all_online = system_get_online_all()
    
    # Filter only this reseller's users
    my_users = SSHUser.query.filter_by(created_by_reseller=current_user.id).all()
    my_usernames = {u.username for u in my_users}
    
    reseller_online = []
    for ou in all_online:
        if ou['username'] in my_usernames:
            user_db = SSHUser.query.filter_by(username=ou['username']).first()
            ou['max_connections'] = user_db.max_connections if user_db else 'N/A'
            ou['exceeded'] = ou['connections'] > ou['max_connections'] if user_db else False
            reseller_online.append(ou)
    
    return render_template('reseller/online.html', online_users=reseller_online)


# ============ DESCONECTAR (solo usuarios propios) ============

@reseller_bp.route('/disconnect')
@reseller_required
def reseller_disconnect():
    """Usuarios del reseller que exceden conexiones máximas"""
    all_online = system_get_online_all()
    
    my_users = SSHUser.query.filter_by(created_by_reseller=current_user.id).all()
    my_usernames = {u.username for u in my_users}
    
    exceeded = []
    for ou in all_online:
        if ou['username'] in my_usernames:
            user_db = SSHUser.query.filter_by(username=ou['username']).first()
            if user_db and ou['connections'] > user_db.max_connections:
                ou['max_connections'] = user_db.max_connections
                ou['exceeded'] = True
                exceeded.append(ou)
    
    exceeded_usernames = [u['username'] for u in exceeded]
    
    return render_template('reseller/disconnect.html',
                          online_users=exceeded,
                          excedeed_count=len(exceeded),
                          exceeded_usernames=exceeded_usernames)


@reseller_bp.route('/disconnect/run', methods=['POST'])
@reseller_required
def reseller_disconnect_run():
    """Desconecta y/o bloquea usuarios del reseller"""
    username = request.form.get('username', '')
    block = request.form.get('block', 'false') == 'true'
    
    if not username:
        flash('Usuario no especificado', 'warning')
        return redirect(url_for('reseller.reseller_disconnect'))
    
    # Verificar que el usuario pertenece a este reseller
    user = SSHUser.query.filter_by(username=username, created_by_reseller=current_user.id).first()
    if not user:
        flash('No tienes permiso para modificar este usuario', 'danger')
        return redirect(url_for('reseller.reseller_disconnect'))
    
    system_disconnect_user(username)
    
    if block:
        user.is_blocked = True
        db.session.commit()
        system_block_user(username)
        log_action('disconnect_block', f'Usuario desconectado y bloqueado: {username}', target_user=username)
        flash(f'Usuario "{username}" desconectado y bloqueado', 'success')
    else:
        log_action('disconnect', f'Usuario desconectado: {username}', target_user=username)
        flash(f'Usuario "{username}" desconectado', 'success')
    
    return redirect(url_for('reseller.reseller_disconnect'))


@reseller_bp.route('/disconnect/block-all', methods=['POST'])
@reseller_required
def reseller_disconnect_block_all():
    """Desconecta y bloquea todos los usuarios del reseller que exceden su límite"""
    usernames_str = request.form.get('usernames', '')
    usernames = [u.strip() for u in usernames_str.split(',') if u.strip()]
    
    count = 0
    for uname in usernames:
        user = SSHUser.query.filter_by(username=uname, created_by_reseller=current_user.id).first()
        if user:
            system_disconnect_user(uname)
            user.is_blocked = True
            system_block_user(uname)
            count += 1
    
    db.session.commit()
    
    log_action('mass_disconnect_block', 
               f'Desconexión y bloqueo masivo: {count} usuarios',
               target_type='ssh_user')
    
    flash(f'{count} usuarios desconectados y bloqueados', 'success')
    return redirect(url_for('reseller.reseller_disconnect'))


# ============ LIMPIAR EXPIRADOS (solo usuarios propios) ============

@reseller_bp.route('/clean-expired')
@reseller_required
def reseller_clean_expired():
    """Usuarios expirados del reseller"""
    expired = SSHUser.query.filter(
        SSHUser.created_by_reseller == current_user.id,
        SSHUser.expires_at <= datetime.utcnow()
    ).all()
    return render_template('reseller/clean_expired.html', users=expired)


@reseller_bp.route('/clean-expired/run', methods=['POST'])
@reseller_required
def reseller_clean_expired_run():
    """Elimina usuarios expirados del reseller"""
    user_ids = request.form.getlist('user_ids', type=int)
    
    if not user_ids:
        flash('No se seleccionaron usuarios', 'warning')
        return redirect(url_for('reseller.reseller_clean_expired'))
    
    count = 0
    for uid in user_ids:
        user = SSHUser.query.get(uid)
        if user and user.created_by_reseller == current_user.id and user.is_expired():
            system_execute(user, "delete_user", user.username)
            db.session.delete(user)
            count += 1
    
    db.session.commit()
    
    log_action('clean_expired', f'Limpieza de expirados: {count} usuarios eliminados',
               target_type='ssh_user')
    
    flash(f'{count} usuarios expirados eliminados', 'success')
    return redirect(url_for('reseller.reseller_clean_expired'))
