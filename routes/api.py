"""
API REST para operaciones desde el frontend (JS).
"""
from datetime import datetime, timedelta
from flask import Blueprint, jsonify, request
from flask_login import login_required
from models import db, SSHUser, ActivityLog
from ssh_manager import system_get_online_users

api_bp = Blueprint('api', __name__)


@api_bp.route('/stats')
@login_required
def api_stats():
    """Estadísticas en JSON para el dashboard"""
    now = datetime.utcnow()
    
    total = SSHUser.query.count()
    active = SSHUser.query.filter(
        SSHUser.is_blocked == False,
        SSHUser.expires_at > now
    ).count()
    blocked = SSHUser.query.filter_by(is_blocked=True).count()
    expired = SSHUser.query.filter(SSHUser.expires_at <= now).count()
    
    # Creados esta semana
    week_ago = now - timedelta(days=7)
    weekly = SSHUser.query.filter(SSHUser.created_at >= week_ago).count()
    
    # Próximos a vencer (7 días)
    expiring = SSHUser.query.filter(
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
def api_online():
    """Lista de usuarios online en JSON"""
    online_data = system_get_online_users()
    users = online_data.get('users', [])
    
    result = []
    for ou in users:
        user_db = SSHUser.query.filter_by(username=ou['username']).first()
        result.append({
            'username': ou['username'],
            'connections': ou['connections'],
            'max_connections': user_db.max_connections if user_db else None,
            'exceeded': user_db.max_connections < ou['connections'] if user_db else False
        })
    
    return jsonify({'users': result})


@api_bp.route('/logs/recent')
@login_required
def api_recent_logs():
    """Últimos 20 logs en JSON"""
    logs = ActivityLog.query.order_by(ActivityLog.created_at.desc()).limit(20).all()
    return jsonify([{
        'id': l.id,
        'action': l.action,
        'description': l.description,
        'performed_by': l.performed_by,
        'performed_by_type': l.performed_by_type,
        'created_at': l.created_at.strftime('%Y-%m-%d %H:%M:%S')
    } for l in logs])
