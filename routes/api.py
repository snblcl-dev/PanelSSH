"""
API REST para operaciones desde el frontend (JS).
"""
from datetime import datetime, timedelta
from collections import defaultdict
from flask import Blueprint, jsonify, request
from flask_login import login_required, current_user
from models import db, SSHUser, ActivityLog, Reseller
from ssh_manager import system_get_online_users, system_get_online_all

api_bp = Blueprint('api', __name__)

# Rate limiting en memoria
_rate_limits = defaultdict(list)


def _is_reseller():
    return current_user.__class__.__name__ == 'Reseller'


def _ssh_user_query():
    """Query base de SSHUser filtrada por rol"""
    q = SSHUser.query
    if _is_reseller():
        q = q.filter_by(created_by_reseller=current_user.id)
    return q


def _activity_log_query():
    """Query base de ActivityLog filtrada por rol"""
    q = ActivityLog.query
    if _is_reseller():
        q = q.filter(
            ActivityLog.performed_by == current_user.username,
            ActivityLog.performed_by_type == 'reseller'
        )
    return q

def rate_limit(max_requests=30, window_seconds=60):
    """Decorador: limita peticiones por IP. 429 si excede."""
    def decorator(f):
        from functools import wraps
        @wraps(f)
        def wrapped(*args, **kwargs):
            ip = request.remote_addr or '127.0.0.1'
            now = datetime.utcnow()
            window = timedelta(seconds=window_seconds)
            _rate_limits[ip] = [t for t in _rate_limits[ip] if now - t < window]
            if len(_rate_limits[ip]) >= max_requests:
                return jsonify({'error': 'Demasiadas peticiones. Intenta de nuevo en un minuto.'}), 429
            _rate_limits[ip].append(now)
            return f(*args, **kwargs)
        return wrapped
    return decorator


@api_bp.route('/stats')
@login_required
@rate_limit(30, 60)
def api_stats():
    """Estadísticas en JSON para el dashboard"""
    now = datetime.utcnow()
    q = _ssh_user_query()
    
    total = q.count()
    active = q.filter(
        SSHUser.is_blocked == False,
        SSHUser.expires_at > now
    ).count()
    blocked = q.filter_by(is_blocked=True).count()
    expired = q.filter(SSHUser.expires_at <= now).count()
    
    # Creados esta semana
    week_ago = now - timedelta(days=7)
    weekly = q.filter(SSHUser.created_at >= week_ago).count()
    
    # Próximos a vencer (7 días)
    expiring = q.filter(
        SSHUser.is_blocked == False,
        SSHUser.expires_at > now,
        SSHUser.expires_at <= now + timedelta(days=7)
    ).count()
    
    online_data = system_get_online_users()
    online = len(online_data.get('users', []))
    
    return jsonify({
        'total': total,
        'active': active,
        'blocked': blocked,
        'expired': expired,
        'weekly': weekly,
        'expiring': expiring,
        'online': online
    })


@api_bp.route('/online')
@login_required
@rate_limit(30, 60)
def api_online():
    """Lista de usuarios online en JSON"""
    online_data = system_get_online_all()
    users = online_data if isinstance(online_data, list) else online_data.get('users', [])
    
    result = []
    for ou in users:
        user_db = SSHUser.query.filter_by(username=ou['username']).first()
        if _is_reseller() and (not user_db or user_db.created_by_reseller != current_user.id):
            continue
        result.append({
            'username': ou['username'],
            'connections': ou['connections'],
            'max_connections': user_db.max_connections if user_db else None,
            'exceeded': user_db.max_connections < ou['connections'] if user_db else False
        })
    
    return jsonify({'users': result})


@api_bp.route('/logs/recent')
@login_required
@rate_limit(20, 60)
def api_recent_logs():
    """Últimos 20 logs en JSON"""
    logs = _activity_log_query().order_by(ActivityLog.created_at.desc()).limit(20).all()
    return jsonify([{
        'id': l.id,
        'action': l.action,
        'description': l.description,
        'performed_by': l.performed_by,
        'performed_by_type': l.performed_by_type,
        'created_at': l.created_at.strftime('%Y-%m-%d %H:%M:%S')
    } for l in logs])
