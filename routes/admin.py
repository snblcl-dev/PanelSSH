"""
Rutas principales del panel de administración.
Cubre: Dashboard, ABM Usuarios, Online, Logs, Resellers, Clientes
"""
import json
from datetime import datetime, timedelta
from flask import Blueprint, render_template, redirect, url_for, flash, request, jsonify, current_app
from flask_login import login_required, current_user
from models import db, Admin, Reseller, SSHUser, ActivityLog, generate_password, CreditConfig, Server, validate_username, Notification
from ssh_manager import (
    system_create_user, system_delete_user, system_block_user,
    system_unblock_user, system_change_password, system_get_online_users,
    system_disconnect_user, system_set_expiry, system_execute,
    system_get_online_all, system_sync_expired_users
)

admin_bp = Blueprint('admin', __name__)

# Rutas permitidas sin cambiar contraseña
_CHANGE_PASSWORD_EXEMPT = {'admin.profile', 'admin.change_password', 'auth.logout'}


@admin_bp.before_request
def enforce_password_change():
    """
    Obliga al admin a cambiar su contraseña antes de usar el panel.
    """
    from flask_login import current_user
    if not current_user.is_authenticated:
        return None
    if not isinstance(current_user, Admin):
        return None
    if not getattr(current_user, 'must_change_password', False):
        return None
    if request.endpoint in _CHANGE_PASSWORD_EXEMPT:
        return None
    flash('Debes cambiar tu contraseña antes de continuar.', 'warning')
    return redirect(url_for('admin.profile'))


# Decorador para verificar que es admin
def admin_required(f):
    from functools import wraps
    @wraps(f)
    @login_required
    def decorated(*args, **kwargs):
        if not isinstance(current_user, Admin):
            flash('Acceso denegado. Se requieren permisos de administrador.', 'danger')
            return redirect(url_for('auth.login'))
        return f(*args, **kwargs)
    return decorated


def log_action(action, description, target_user=None, target_type='ssh_user'):
    """Registra una actividad en el log"""
    log = ActivityLog(
        action=action,
        description=description,
        performed_by=current_user.username,
        performed_by_type='admin',
        target_user=target_user,
        target_type=target_type,
        ip_address=request.remote_addr
    )
    db.session.add(log)
    db.session.commit()


def saas_block():
    """En modo SaaS, redirige si se intenta crear usuarios locales"""
    from flask import current_app
    if current_app.config.get('PANEL_MODE') == 'saas':
        flash('En modo SaaS, los usuarios se gestionan desde cada instancia.', 'warning')
        return redirect(url_for('admin.dashboard'))
    return None


@admin_bp.route('/')
@admin_required
def dashboard():
    """Dashboard principal con estadísticas"""
    now = datetime.utcnow()
    
    total_users = SSHUser.query.count()
    active_users = SSHUser.query.filter(
        SSHUser.is_blocked == False,
        SSHUser.expires_at > now
    ).count()
    blocked_users = SSHUser.query.filter_by(is_blocked=True).count()
    expired_users = SSHUser.query.filter(SSHUser.expires_at <= now).count()
    
    # Próximos a vencer (3 días)
    expiring_soon = SSHUser.query.filter(
        SSHUser.is_blocked == False,
        SSHUser.expires_at > now,
        SSHUser.expires_at <= now + timedelta(days=3)
    ).count()
    
    # Total resellers
    total_resellers = Reseller.query.count()
    active_resellers = Reseller.query.filter_by(is_active=True).count()
    
    # Usuarios online
    online_data = system_get_online_users()
    online_users_count = len(online_data.get('users', [])) if online_data['success'] else 0
    
    # Últimas actividades
    recent_logs = ActivityLog.query.order_by(
        ActivityLog.created_at.desc()
    ).limit(10).all()
    
    # Creados hoy
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    created_today = SSHUser.query.filter(SSHUser.created_at >= today_start).count()
    
    return render_template('admin/dashboard.html',
        total_users=total_users,
        active_users=active_users,
        blocked_users=blocked_users,
        expired_users=expired_users,
        expiring_soon=expiring_soon,
        total_resellers=total_resellers,
        active_resellers=active_resellers,
        online_users_count=online_users_count,
        created_today=created_today,
        recent_logs=recent_logs,
        resellers=Reseller.query.filter_by(is_active=True).all(),
        servers=Server.query.filter_by(is_active=True).all()
    )


# ============ USUARIOS SSH ============

@admin_bp.route('/users')
@admin_required
def users():
    """Lista todos los usuarios SSH"""
    search = request.args.get('search', '').strip()
    status_filter = request.args.get('status', '')
    
    query = SSHUser.query
    
    if search:
        query = query.filter(SSHUser.username.contains(search))
    
    if status_filter == 'active':
        query = query.filter(
            SSHUser.is_blocked == False,
            SSHUser.expires_at > datetime.utcnow()
        )
    elif status_filter == 'blocked':
        query = query.filter_by(is_blocked=True)
    elif status_filter == 'expired':
        query = query.filter(SSHUser.expires_at <= datetime.utcnow())
    
    users_list = query.order_by(SSHUser.created_at.desc()).all()
    resellers = Reseller.query.filter_by(is_active=True).all()
    
    return render_template('admin/users.html', users=users_list, resellers=resellers,
                          search=search, status_filter=status_filter,
                          servers=Server.query.filter_by(is_active=True).all())


@admin_bp.route('/users/create', methods=['POST'])
@admin_required
def user_create():
    """Crea un nuevo usuario SSH"""
    server_id = request.form.get('server_id', type=int)
    if current_app.config.get('PANEL_MODE') == 'saas' and not server_id:
        flash('En modo SaaS solo puedes crear usuarios en servidores remotos. Agrega un servidor primero.', 'warning')
        return redirect(url_for('admin.users'))

    # Verificar límite de usuarios
    max_users = current_app.config.get('MAX_USERS_LIMIT', -1)
    if max_users != -1:
        current_count = SSHUser.query.count()
        if current_count >= max_users:
            flash(f'Has alcanzado el límite de {max_users} usuarios de tu plan. Contacta al administrador para ampliarlo.', 'danger')
            return redirect(url_for('admin.users'))

    username = request.form.get('username', '').strip()
    days = request.form.get('days', 30, type=int)
    max_connections = request.form.get('max_connections', 1, type=int)
    assign_reseller = request.form.get('assign_reseller', type=int)
    server_id = request.form.get('server_id', type=int)
    
    if not username:
        flash('El nombre de usuario es requerido', 'danger')
        return redirect(url_for('admin.users'))

    # Validar formato de username
    valid, msg = validate_username(username)
    if not valid:
        flash(msg, 'danger')
        return redirect(url_for('admin.users'))

    # Validaciones
    if SSHUser.query.filter_by(username=username).first():
        flash(f'El usuario "{username}" ya existe', 'danger')
        return redirect(url_for('admin.users'))
    
    max_days = CreditConfig.get_config().get_max_days()
    if days > max_days:
        flash(f'El maximo de dias permitido es {max_days}', 'danger')
        return redirect(url_for('admin.users'))
    days = max(1, days)
    if max_connections > 10:
        flash('El máximo de conexiones permitido es 10', 'danger')
        return redirect(url_for('admin.users'))
    max_connections = max(1, max_connections)

    password = generate_password()
    expires_at = datetime.utcnow() + timedelta(days=days)

    user = SSHUser(
        username=username,
        password_plain=password,
        max_connections=max_connections,
        days_duration=days,
        expires_at=expires_at,
        is_active=True,
        is_blocked=False,
        created_by_admin=current_user.id,
        created_by_reseller=assign_reseller if assign_reseller else None,
        server_id=server_id if server_id else None
    )
    user.password = user.password_plain
    from werkzeug.security import generate_password_hash
    user.password = generate_password_hash(password)
    
    db.session.add(user)
    db.session.commit()
    
    # Intentar crear en el sistema (local o remoto segun server_id)
    from ssh_manager import system_execute
    sys_result = system_execute(user, 'create_user', username, password, expires_at, max_connections)
    
    log_action('create', f'Usuario creado: {username} ({days} días, {max_connections} conexiones)',
               target_user=username)

    flash(f'Usuario "{username}" creado exitosamente. Contraseña: {password}', 'success')
    return redirect(url_for('admin.users'))


@admin_bp.route('/users/create-demo', methods=['POST'])
@admin_required
def user_create_demo():
    """Crea un usuario demo con duración en minutos (solo admin)"""
    server_id = request.form.get('server_id', type=int)
    if current_app.config.get('PANEL_MODE') == 'saas' and not server_id:
        flash('En modo SaaS solo puedes crear usuarios demo en servidores remotos.', 'warning')
        return redirect(url_for('admin.users'))

    # Verificar límite de usuarios (demos también cuentan)
    max_users = current_app.config.get('MAX_USERS_LIMIT', -1)
    if max_users != -1:
        current_count = SSHUser.query.count()
        if current_count >= max_users:
            flash(f'Límite de {max_users} usuarios alcanzado.', 'danger')
            return redirect(url_for('admin.users'))

    username = request.form.get('username', '').strip()
    minutes = request.form.get('minutes', 30, type=int)
    max_connections = request.form.get('max_connections', 1, type=int)
    assign_reseller = request.form.get('assign_reseller', type=int)
    server_id = request.form.get('server_id', type=int)

    if not username:
        flash('El nombre de usuario es requerido', 'danger')
        return redirect(url_for('admin.users'))

    valid, msg = validate_username(username)
    if not valid:
        flash(msg, 'danger')
        return redirect(url_for('admin.users'))

    if SSHUser.query.filter_by(username=username).first():
        flash(f'El usuario "{username}" ya existe', 'danger')
        return redirect(url_for('admin.users'))

    # Validar minutos (1-180)
    minutes = max(1, min(minutes, 180))
    if max_connections > 800:
        flash('El máximo de conexiones para demo es 800', 'danger')
        return redirect(url_for('admin.users'))
    max_connections = max(1, max_connections)

    password = generate_password()
    expires_at = datetime.utcnow() + timedelta(minutes=minutes)

    user = SSHUser(
        username=username,
        password_plain=password,
        max_connections=max_connections,
        days_duration=0,
        expires_at=expires_at,
        is_active=True,
        is_blocked=False,
        is_demo=True,
        duration_minutes=minutes,
        created_by_admin=current_user.id,
        created_by_reseller=assign_reseller if assign_reseller else None,
        server_id=server_id if server_id else None
    )
    from werkzeug.security import generate_password_hash
    user.password = generate_password_hash(password)

    db.session.add(user)
    db.session.commit()

    from ssh_manager import system_execute
    system_execute(user, 'create_user', username, password, expires_at, max_connections)

    log_action('create_demo', f'Usuario demo creado: {username} ({minutes} minutos, {max_connections} conexiones)',
               target_user=username)

    flash(f'Usuario demo "{username}" creado. Contraseña: {password} | Expira en {minutes} minutos', 'success')
    return redirect(url_for('admin.users'))




# ============ RESELLERS ============

@admin_bp.route('/resellers')
@admin_required
def resellers():
    resellers_list = Reseller.query.order_by(Reseller.created_at.desc()).all()
    resellers_data = []
    for r in resellers_list:
        now = datetime.utcnow()
        users = SSHUser.query.filter_by(created_by_reseller=r.id)
        total = users.count()
        active = users.filter(SSHUser.is_blocked == False, SSHUser.expires_at > now).count()
        blocked = users.filter_by(is_blocked=True).count()
        expired = users.filter(SSHUser.expires_at <= now).count()
        last_log = ActivityLog.query.filter(
            ActivityLog.performed_by == r.username,
            ActivityLog.performed_by_type == 'reseller'
        ).order_by(ActivityLog.created_at.desc()).first()
        resellers_data.append({
            'reseller': r, 'total_users': total, 'active_users': active,
            'blocked_users': blocked, 'expired_users': expired,
            'last_activity': last_log.created_at if last_log else None,
            'last_action': last_log.description[:60] if last_log else '---',
        })
    return render_template('admin/resellers.html', resellers=resellers_data)


@admin_bp.route('/resellers/activate/<int:reseller_id>', methods=['POST'])
@admin_required
def reseller_activate(reseller_id):
    reseller = Reseller.query.get_or_404(reseller_id)
    reseller.is_active = not reseller.is_active
    db.session.commit()
    status = 'activado' if reseller.is_active else 'desactivado'
    log_action('reseller_toggle', 'Revendedor %s: %s' % (status, reseller.username),
               target_user=reseller.username, target_type='reseller')
    flash('Revendedor %s %s' % (reseller.username, status), 'success')
    return redirect(url_for('admin.resellers'))


@admin_bp.route('/resellers/credits/<int:reseller_id>', methods=['POST'])
@admin_required
def reseller_add_credits(reseller_id):
    reseller = Reseller.query.get_or_404(reseller_id)
    credits = request.form.get('credits', 0, type=int)
    if credits <= 0:
        flash('La cantidad debe ser positiva', 'warning')
        return redirect(url_for('admin.resellers'))
    reseller.credits += credits
    reseller.total_credits_received = (reseller.total_credits_received or 0) + credits
    db.session.commit()
    log_action('reseller_credits', 'Creditos a %s: +%d' % (reseller.username, credits),
               target_user=reseller.username, target_type='reseller')
    flash('%d creditos a %s' % (credits, reseller.username), 'success')
    return redirect(url_for('admin.resellers'))


@admin_bp.route('/resellers/deduct-credits/<int:reseller_id>', methods=['POST'])
@admin_required
def reseller_deduct_credits(reseller_id):
    reseller = Reseller.query.get_or_404(reseller_id)
    credits = request.form.get('credits', 0, type=int)
    if credits <= 0:
        flash('La cantidad debe ser positiva', 'warning')
        return redirect(url_for('admin.resellers'))
    if credits > reseller.credits:
        flash('Maximo a restar: %d (tiene %d)' % (reseller.credits, reseller.credits), 'danger')
        return redirect(url_for('admin.resellers'))
    reseller.credits -= credits
    db.session.commit()
    log_action('reseller_deduct_credits', 'Creditos restados a %s: -%d' % (reseller.username, credits),
               target_user=reseller.username, target_type='reseller')
    flash('%d creditos restados a %s' % (credits, reseller.username), 'success')
    return redirect(url_for('admin.resellers'))


@admin_bp.route('/resellers/delete/<int:reseller_id>', methods=['POST'])
@admin_required
def reseller_delete(reseller_id):
    reseller = Reseller.query.get_or_404(reseller_id)
    user_count = SSHUser.query.filter_by(created_by_reseller=reseller.id).count()
    if user_count > 0:
        flash('Tiene %d usuarios. Reasigna o eliminalos primero.' % user_count, 'danger')
        return redirect(url_for('admin.resellers'))
    username = reseller.username
    db.session.delete(reseller)
    db.session.commit()
    log_action('reseller_delete', 'Eliminado: %s' % username,
               target_user=username, target_type='reseller')
    flash('Revendedor %s eliminado' % username, 'success')
    return redirect(url_for('admin.resellers'))

@admin_bp.route('/users/renew', methods=['POST'])
@admin_required
def user_renew():
    """Renueva un usuario existente"""
    user_id = request.form.get('user_id', type=int)
    extra_days = request.form.get('extra_days', 30, type=int)
    new_max_connections = request.form.get('new_max_connections', type=int)
    
    user = SSHUser.query.get_or_404(user_id)
    max_days = CreditConfig.get_config().get_max_days()
    if extra_days > max_days:
        flash(f'El maximo de dias para renovar es {max_days}', 'danger')
        return redirect(url_for('admin.users'))
    extra_days = max(1, extra_days)

    if new_max_connections and new_max_connections > 10:
        flash('El maximo de conexiones permitido es 10', 'danger')
        return redirect(url_for('admin.users'))

    old_expiry = user.expires_at.strftime('%Y-%m-%d')
    user.renew(extra_days, new_max_connections)
    db.session.commit()
    
    # Actualizar en el sistema
    system_execute(user, 'set_expiry', user.username, user.expires_at)
    
    log_action('renew', f'Usuario renovado: {user.username} (+{extra_days} días, vence: {user.expires_at.strftime("%Y-%m-%d")})',
               target_user=user.username)
    
    flash(f'Usuario "{user.username}" renovado (+{extra_days} días)', 'success')
    return redirect(url_for('admin.users'))


@admin_bp.route('/users/block/<int:user_id>', methods=['POST'])
@admin_required
def user_block(user_id):
    user = SSHUser.query.get_or_404(user_id)
    if not user.is_blocked:
        user.is_blocked = True
        db.session.commit()
        system_execute(user, 'block_user', user.username)
        log_action('block', f'Usuario bloqueado: {user.username}', target_user=user.username)
        flash(f'Usuario "{user.username}" bloqueado', 'success')
    return redirect(url_for('admin.users'))


@admin_bp.route('/users/unblock/<int:user_id>', methods=['POST'])
@admin_required
def user_unblock(user_id):
    user = SSHUser.query.get_or_404(user_id)
    if user.is_blocked:
        user.is_blocked = False
        db.session.commit()
        system_execute(user, 'unblock_user', user.username)
        system_execute(user, 'set_expiry', user.username, user.expires_at)
        log_action('unblock', f'Usuario desbloqueado: {user.username}', target_user=user.username)
        flash(f'Usuario "{user.username}" desbloqueado', 'success')
    return redirect(url_for('admin.users'))


@admin_bp.route('/users/delete/<int:user_id>', methods=['POST'])
@admin_required
def user_delete(user_id):
    user = SSHUser.query.get_or_404(user_id)
    username = user.username
    
    system_execute(user, 'delete_user', username)
    db.session.delete(user)
    db.session.commit()
    
    log_action('delete', f'Usuario eliminado: {username}', target_user=username)
    flash(f'Usuario "{username}" eliminado permanentemente', 'success')
    return redirect(url_for('admin.users'))


@admin_bp.route('/users/change-password/<int:user_id>', methods=['POST'])
@admin_required
def user_change_password(user_id):
    user = SSHUser.query.get_or_404(user_id)
    custom_password = request.form.get('new_password', '').strip()

    if custom_password:
        if len(custom_password) < 4:
            flash('La contraseña debe tener al menos 4 caracteres', 'danger')
            return redirect(url_for('admin.users'))
        new_password = custom_password
        user.password_plain = new_password  # Actualizar en BD
    else:
        new_password = user.reset_password()

    from werkzeug.security import generate_password_hash
    user.password = generate_password_hash(new_password)
    db.session.commit()
    system_execute(user, 'change_password', user.username, new_password)

    log_action('change_password', f'Contrasena cambiada para: {user.username}',
               target_user=user.username)

    flash(f'Nueva contraseña para "{user.username}": {new_password}', 'success')
    return redirect(url_for('admin.users'))


# ============ ONLINE / DESCONECTAR ============

@admin_bp.route('/online')
@admin_required
def online():
    """Usuarios conectados activamente"""
    online_users = system_get_online_all()
    
    # Crear dict rápido: username -> info
    online_dict = {}
    for ou in online_users:
        online_dict[ou['username']] = ou
    
    # Marcar quienes exceden sus conexiones máximas
    for ou in online_users:
        user_db = SSHUser.query.filter_by(username=ou['username']).first()
        if user_db:
            ou['max_connections'] = user_db.max_connections
            ou['exceeded'] = ou['connections'] > user_db.max_connections
        else:
            ou['max_connections'] = 'N/A'
            ou['exceeded'] = False
    
    excedeed_count = sum(1 for u in online_users if u.get('exceeded'))
    
    # Todos los usuarios para la vista general
    all_users = SSHUser.query.order_by(SSHUser.username).all()
    
    return render_template('admin/online.html', online_users=online_users,
                          excedeed_count=excedeed_count, all_users=all_users,
                          online_dict=online_dict)


@admin_bp.route('/disconnect')
@admin_required
def disconnect():
    """Vista dedicada para desconexión de usuarios que exceden límites"""
    online_users = system_get_online_all()
    
    exceeded_usernames = []
    for ou in online_users:
        user_db = SSHUser.query.filter_by(username=ou['username']).first()
        if user_db:
            ou['max_connections'] = user_db.max_connections
            ou['exceeded'] = ou['connections'] > user_db.max_connections
            if ou['exceeded']:
                exceeded_usernames.append(ou['username'])
        else:
            ou['max_connections'] = 'N/A'
            ou['exceeded'] = False
    
    excedeed_count = len(exceeded_usernames)
    
    return render_template('admin/disconnect.html', 
                          online_users=online_users,
                          excedeed_count=excedeed_count,
                          exceeded_usernames=exceeded_usernames)


@admin_bp.route('/online/disconnect', methods=['POST'])
@admin_required
def online_disconnect():
    username = request.form.get('username')
    if username:
        user = SSHUser.query.filter_by(username=username).first()
        if user:
            system_execute(user, 'disconnect_user', username)
        else:
            system_disconnect_user(username)
        log_action('disconnect', f'Usuario desconectado: {username}', target_user=username)
        flash(f'Usuario "{username}" desconectado', 'success')
    return redirect(url_for('admin.online'))


@admin_bp.route('/online/disconnect-block', methods=['POST'])
@admin_required
def online_disconnect_block():
    usernames_str = request.form.get('usernames', '')
    usernames = [u.strip() for u in usernames_str.split(',') if u.strip()]
    
    count = 0
    for username in usernames:
        system_disconnect_user(username)
        user = SSHUser.query.filter_by(username=username).first()
        if user:
            user.is_blocked = True
            system_execute(user, 'block_user', username)
            count += 1
    
    db.session.commit()
    
    log_action('mass_disconnect_block', 
               f'Desconexión y bloqueo masivo: {count} usuarios ({", ".join(usernames[:5])}...)',
               target_type='ssh_user')
    
    flash(f'{count} usuarios desconectados y bloqueados', 'success')
    return redirect(url_for('admin.online'))


# ============ CLIENTES ============

@admin_bp.route('/clients')
@admin_required
def clients():
    """Vista detallada de todos los clientes"""
    search = request.args.get('search', '').strip()
    reseller_filter = request.args.get('reseller', type=int)
    
    query = SSHUser.query
    
    if search:
        query = query.filter(SSHUser.username.contains(search))
    if reseller_filter:
        query = query.filter_by(created_by_reseller=reseller_filter)
    
    users_list = query.order_by(SSHUser.created_at.desc()).all()
    resellers = Reseller.query.all()
    
    return render_template('admin/clients.html', users=users_list, resellers=resellers)


# ============ LIMPIAR EXPIRADOS ============

@admin_bp.route('/clean-expired')
@admin_required
def clean_expired():
    """Vista para limpiar usuarios expirados"""
    expired = SSHUser.query.filter(SSHUser.expires_at <= datetime.utcnow()).all()
    return render_template('admin/clean_expired.html', users=expired)


@admin_bp.route('/clean-expired/run', methods=['POST'])
@admin_required
def clean_expired_run():
    """Ejecuta la limpieza de usuarios expirados"""
    user_ids = request.form.getlist('user_ids', type=int)
    
    if not user_ids:
        flash('No se seleccionaron usuarios', 'warning')
        return redirect(url_for('admin.clean_expired'))
    
    count = 0
    for uid in user_ids:
        user = SSHUser.query.get(uid)
        if user and user.is_expired():
            system_delete_user(user.username)
            db.session.delete(user)
            count += 1
    
    db.session.commit()
    
    log_action('clean_expired', f'Limpieza de expirados: {count} usuarios eliminados',
               target_type='ssh_user')
    
    flash(f'{count} usuarios expirados eliminados', 'success')
    return redirect(url_for('admin.clean_expired'))


# ============ PERFIL / CAMBIAR CONTRASEÑA ============

@admin_bp.route('/profile')
@admin_required
def profile():
    """Perfil del administrador"""
    return render_template('admin/profile.html')


@admin_bp.route('/profile/change-password', methods=['POST'])
@admin_required
def change_password():
    """Cambia la contraseña del administrador"""
    current_password = request.form.get('current_password', '')
    new_password = request.form.get('new_password', '')
    confirm_password = request.form.get('confirm_password', '')
    
    if not current_user.check_password(current_password):
        flash('La contraseña actual es incorrecta', 'danger')
        return redirect(url_for('admin.profile'))
    
    if len(new_password) < 6:
        flash('La nueva contraseña debe tener al menos 6 caracteres', 'warning')
        return redirect(url_for('admin.profile'))
    
    if new_password != confirm_password:
        flash('Las contraseñas no coinciden', 'danger')
        return redirect(url_for('admin.profile'))
    
    current_user.set_password(new_password)
    current_user.must_change_password = False
    db.session.commit()
    
    log_action('change_admin_password', 'Contraseña de administrador cambiada')
    
    flash('Contraseña cambiada exitosamente', 'success')
    return redirect(url_for('admin.dashboard'))


# ============ LOGS / REGISTRO ============

@admin_bp.route('/logs')
@admin_required
def logs():
    """Registro completo de actividades"""
    page = request.args.get('page', 1, type=int)
    action_filter = request.args.get('action', '')
    search = request.args.get('search', '').strip()
    
    query = ActivityLog.query
    
    if action_filter:
        query = query.filter_by(action=action_filter)
    if search:
        query = query.filter(
            db.or_(
                ActivityLog.description.contains(search),
                ActivityLog.performed_by.contains(search),
                ActivityLog.target_user.contains(search)
            )
        )
    
    logs_page = query.order_by(ActivityLog.created_at.desc()).paginate(
        page=page, per_page=25, error_out=False
    )
    
    # Obtener acciones únicas para el filtro
    actions = [a[0] for a in db.session.query(ActivityLog.action).distinct().all()]
    
    return render_template('admin/logs.html', logs=logs_page, actions=sorted(actions),
                          action_filter=action_filter, search=search)


@admin_bp.route('/logs/delete-old', methods=['POST'])
@admin_required
def logs_delete_old():
    """Elimina registros antiguos del log"""
    days = request.form.get('days', 30, type=int)
    if days < 1:
        flash('Los dias deben ser al menos 1', 'warning')
        return redirect(url_for('admin.logs'))
    cutoff = datetime.utcnow() - timedelta(days=days)
    old_logs = ActivityLog.query.filter(ActivityLog.created_at < cutoff).all()
    count = len(old_logs)
    for log in old_logs:
        db.session.delete(log)
    db.session.commit()
    log_action('clean_logs', 'Registros eliminados: %d (mas antiguos de %d dias)' % (count, days),
               target_type='system')
    flash('%d registro(s) antiguos eliminados' % count, 'success')
    return redirect(url_for('admin.logs'))


@admin_bp.route('/logs/delete-all', methods=['POST'])
@admin_required
def logs_delete_all():
    """Elimina TODOS los registros del log"""
    count = ActivityLog.query.count()
    ActivityLog.query.delete()
    db.session.commit()
    flash('Todos los registros eliminados (%d en total)' % count, 'success')
    return redirect(url_for('admin.logs'))


# ============ CONFIGURACION DE CREDITOS ============

@admin_bp.route('/credit-settings', methods=['GET', 'POST'])
@admin_required
def credit_settings():
    """Configuracion del sistema de creditos para revendedores"""
    from models import CreditConfig
    config = CreditConfig.get_config()
    
    if request.method == 'POST':
        mode = request.form.get('mode', 'per_day')
        cost_per_day = request.form.get('cost_per_day', 1, type=int)
        cost_per_user = request.form.get('cost_per_user', 30, type=int)
        cost_per_extra = request.form.get('cost_per_extra_connection', 15, type=int)
        
        config.mode = mode
        config.cost_per_day = max(1, cost_per_day)
        config.cost_per_user = max(1, cost_per_user)
        config.cost_per_extra_connection = max(1, cost_per_extra)
        db.session.commit()
        
        log_action('credit_config', 'Configuracion de creditos actualizada: modo=%s, dia=%d, usuario=%d, conexion_extra=%d' %
                   (mode, config.cost_per_day, config.cost_per_user, config.cost_per_extra_connection),
                   target_type='system')
        
        flash('Configuracion de creditos actualizada', 'success')
        return redirect(url_for('admin.credit_settings'))
    
    return render_template('admin/credit_settings.html', config=config)


@admin_bp.route('/resellers/edit/<int:reseller_id>', methods=['GET', 'POST'])
@admin_required
def reseller_edit(reseller_id):
    reseller = Reseller.query.get_or_404(reseller_id)
    if request.method == 'POST':
        new_username = request.form.get('username', '').strip()
        new_email = request.form.get('email', '').strip()
        new_password = request.form.get('password', '').strip()
        new_notes = request.form.get('notes', '').strip()
        if new_username and new_username != reseller.username:
            existing = Reseller.query.filter_by(username=new_username).first()
            if existing:
                flash('El nombre de usuario ya esta en uso', 'danger')
                return redirect(url_for('admin.reseller_edit', reseller_id=reseller.id))
            reseller.username = new_username
        reseller.email = new_email if new_email else reseller.email
        reseller.notes = new_notes
        if new_password:
            if len(new_password) < 6:
                flash('La contrasena debe tener al menos 6 caracteres', 'warning')
                return redirect(url_for('admin.reseller_edit', reseller_id=reseller.id))
            reseller.set_password(new_password)
        db.session.commit()
        log_action('reseller_edit', 'Revendedor editado: ' + reseller.username,
                   target_user=reseller.username, target_type='reseller')
        flash('Revendedor actualizado', 'success')
        return redirect(url_for('admin.resellers'))
    return render_template('admin/reseller_edit.html', reseller=reseller)


@admin_bp.route('/resellers/detail/<int:reseller_id>')
@admin_required
def reseller_detail(reseller_id):
    reseller = Reseller.query.get_or_404(reseller_id)
    now = datetime.utcnow()
    users = SSHUser.query.filter_by(created_by_reseller=reseller.id).order_by(SSHUser.created_at.desc()).all()
    logs = ActivityLog.query.filter(
        ActivityLog.performed_by == reseller.username,
        ActivityLog.performed_by_type == 'reseller'
    ).order_by(ActivityLog.created_at.desc()).limit(20).all()
    credit_logs = ActivityLog.query.filter(
        ActivityLog.action == 'reseller_credits',
        ActivityLog.target_user == reseller.username
    ).order_by(ActivityLog.created_at.desc()).all()
    active = sum(1 for u in users if not u.is_blocked and not u.is_expired())
    blocked = sum(1 for u in users if u.is_blocked)
    expired = sum(1 for u in users if u.is_expired())
    return render_template('admin/reseller_detail.html',
                          reseller=reseller, users=users, logs=logs,
                          credit_logs=credit_logs,
                          active=active, blocked=blocked, expired=expired)


@admin_bp.route('/resellers/reassign/<int:reseller_id>', methods=['POST'])
@admin_required
def reseller_reassign(reseller_id):
    reseller = Reseller.query.get_or_404(reseller_id)
    target_reseller_id = request.form.get('target_reseller', type=int)
    users = SSHUser.query.filter_by(created_by_reseller=reseller.id).all()
    count = len(users)
    if target_reseller_id:
        target = Reseller.query.get(target_reseller_id)
        if not target:
            flash('Revendedor destino no encontrado', 'danger')
            return redirect(url_for('admin.resellers'))
        for user in users:
            user.created_by_reseller = target.id
        db.session.commit()
        log_action('reseller_reassign',
                   'Usuarios reasignados: %d de %s a %s' % (count, reseller.username, target.username),
                   target_user=reseller.username, target_type='reseller')
        flash('%d usuarios reasignados a %s' % (count, target.username), 'success')
    else:
        for user in users:
            user.created_by_reseller = None
            user.created_by_admin = current_user.id
        db.session.commit()
        log_action('reseller_reassign',
                   'Usuarios reasignados: %d de %s al admin' % (count, reseller.username),
                   target_user=reseller.username, target_type='reseller')
        flash('%d usuarios reasignados al administrador' % count, 'success')
    return redirect(url_for('admin.resellers'))


# ============ MULTISERVIDOR ============

@admin_bp.route('/servers')
@admin_required
def servers():
    from models import Server
    servers_list = Server.query.order_by(Server.created_at.desc()).all()
    return render_template('admin/servers.html', servers=servers_list)


@admin_bp.route('/servers/create', methods=['POST'])
@admin_required
def server_create():
    from models import Server
    # Verificar límite de servidores
    max_servers = current_app.config.get('MAX_SERVERS_LIMIT', -1)
    if max_servers != -1:
        current_count = Server.query.count()
        if current_count >= max_servers:
            flash(f'Límite de {max_servers} servidores alcanzado en tu plan.', 'danger')
            return redirect(url_for('admin.servers'))

    name = request.form.get('name', '').strip()
    host = request.form.get('host', '').strip()
    if not name or not host:
        flash('Nombre y host son requeridos', 'danger')
        return redirect(url_for('admin.servers'))
    ssh_key_path = request.form.get('ssh_key_path', '').strip()
    server = Server(
        name=name, host=host,
        port=request.form.get('port', 22, type=int),
        dropbear_port=request.form.get('dropbear_port', 22, type=int),
        ssh_user=request.form.get('ssh_user', 'root').strip(),
        location=request.form.get('location', '').strip(),
        auth_method=request.form.get('auth_method', 'key'),
        password=request.form.get('password', '').strip() or None,
        ssh_key_path=ssh_key_path or None
    )
    db.session.add(server)
    db.session.commit()
    log_action('server_create', 'Servidor agregado: %s (%s)' % (name, host))
    flash('Servidor agregado exitosamente', 'success')
    return redirect(url_for('admin.servers'))


@admin_bp.route('/servers/test/<int:server_id>')
@admin_required
def server_test(server_id):
    from models import Server
    server = Server.query.get_or_404(server_id)
    ok, msg = server.test_connection()
    flash('Conexion exitosa a %s' % server.name if ok else 'Error: %s' % msg,
          'success' if ok else 'danger')
    return redirect(url_for('admin.servers'))


@admin_bp.route('/servers/toggle/<int:server_id>', methods=['POST'])
@admin_required
def server_toggle(server_id):
    from models import Server
    server = Server.query.get_or_404(server_id)
    server.is_active = not server.is_active
    db.session.commit()
    log_action('server_toggle', 'Servidor %s: %s' % (server.name, 'activado' if server.is_active else 'desactivado'))
    flash('Servidor %s %s' % (server.name, 'activado' if server.is_active else 'desactivado'), 'success')
    return redirect(url_for('admin.servers'))


@admin_bp.route('/servers/delete/<int:server_id>', methods=['POST'])
@admin_required
def server_delete(server_id):
    from models import Server
    server = Server.query.get_or_404(server_id)
    name = server.name
    db.session.delete(server)
    db.session.commit()
    log_action('server_delete', 'Servidor eliminado: %s' % name)
    flash('Servidor %s eliminado' % name, 'success')
    return redirect(url_for('admin.servers'))


# ============ NOTIFICACIONES A REVENDEDORES ============

@admin_bp.route('/notifications')
@admin_required
def notifications():
    """Lista de notificaciones enviadas"""
    notifs = Notification.query.order_by(Notification.created_at.desc()).limit(50).all()
    resellers = Reseller.query.filter_by(is_active=True).all()
    return render_template('admin/notifications.html', notifications=notifs, resellers=resellers)


@admin_bp.route('/notifications/send', methods=['POST'])
@admin_required
def notification_send():
    """Enviar notificacion a revendedores"""
    title = request.form.get('title', '').strip()
    message = request.form.get('message', '').strip()
    reseller_id = request.form.get('reseller_id', type=int)  # None = todos

    if not title or not message:
        flash('Título y mensaje son requeridos', 'danger')
        return redirect(url_for('admin.notifications'))

    notif = Notification(
        title=title,
        message=message,
        reseller_id=reseller_id if reseller_id else None,
        created_by=current_user.id
    )
    db.session.add(notif)
    db.session.commit()

    target = 'todos los revendedores' if not reseller_id else f'revendedor #{reseller_id}'
    log_action('notification', f'Notificacion enviada a {target}: {title}')
    flash(f'Notificación enviada a {target}', 'success')
    return redirect(url_for('admin.notifications'))


@admin_bp.route('/notifications/delete/<int:notif_id>', methods=['POST'])
@admin_required
def notification_delete(notif_id):
    notif = Notification.query.get_or_404(notif_id)
    db.session.delete(notif)
    db.session.commit()
    flash('Notificación eliminada', 'success')
    return redirect(url_for('admin.notifications'))
