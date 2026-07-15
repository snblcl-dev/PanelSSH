"""
Panel Maestro — Gestión de instancias SaaS
Acceso solo para el admin principal.
"""
import os
import json
import sqlite3
import secrets
import hashlib
import subprocess
from datetime import datetime, timedelta
from collections import defaultdict
from pathlib import Path
from flask import Flask, render_template, redirect, url_for, flash, request, session
from functools import wraps
from werkzeug.security import generate_password_hash, check_password_hash

app = Flask(__name__)
BASE_DIR = Path(__file__).parent.parent
INSTANCES_DIR = BASE_DIR / 'instances'
MASTER_DB = Path(__file__).parent / 'instances.json'
CONFIG_FILE = Path(__file__).parent / 'config.json'

# ── Configuración ──
def load_config():
    if CONFIG_FILE.exists():
        return json.loads(CONFIG_FILE.read_text())
    # Primera ejecución: generar secret y password default
    config = {
        'secret_key': secrets.token_hex(32),
        'password_hash': generate_password_hash('admin123'),
        'login_attempts': 0,
        'locked_until': None,
    }
    save_config(config)
    return config


def save_config(config):
    CONFIG_FILE.write_text(json.dumps(config, indent=2))


config = load_config()
app.secret_key = config['secret_key']
app.config['WTF_CSRF_ENABLED'] = False  # Usamos nuestro propio CSRF simple

# ── Rate limiting ──
_rate_limits = defaultdict(list)

def rate_limit(max_requests=10, window_seconds=60):
    def decorator(f):
        @wraps(f)
        def wrapped(*args, **kwargs):
            ip = request.remote_addr or '127.0.0.1'
            now = datetime.utcnow()
            window = timedelta(seconds=window_seconds)
            _rate_limits[ip] = [t for t in _rate_limits[ip] if now - t < window]
            if len(_rate_limits[ip]) >= max_requests:
                return render_template('login.html', error='Demasiados intentos. Espera un minuto.'), 429
            _rate_limits[ip].append(now)
            return f(*args, **kwargs)
        return wrapped
    return decorator


# ── Datos ──
def load_instances():
    if MASTER_DB.exists():
        return json.loads(MASTER_DB.read_text())
    return []


def save_instances(data):
    MASTER_DB.write_text(json.dumps(data, indent=2, default=str))


def scan_filesystem():
    registered = {i['slug'] for i in load_instances()}
    if not INSTANCES_DIR.exists():
        return
    for d in INSTANCES_DIR.iterdir():
        if d.is_dir() and d.name not in registered:
            port = get_instance_port(d.name)
            data = load_instances()
            data.append({
                'slug': d.name,
                'subdomain': '',
                'port': port,
                'created_at': datetime.fromtimestamp(d.stat().st_ctime).isoformat(),
                'expires_at': None,
                'notes': '',
                'systemd_service': f'sshpanel-{d.name}',
                'is_active': check_service_status(d.name),
            })
            save_instances(data)


def get_instance_port(slug):
    service_file = Path(f'/etc/systemd/system/sshpanel-{slug}.service')
    if service_file.exists():
        content = service_file.read_text()
        for line in content.split('\n'):
            if 'SSHPANEL_PORT=' in line:
                return int(line.split('=')[-1].strip())
    return 0


def check_service_status(slug):
    result = subprocess.run(
        ['systemctl', 'is-active', f'sshpanel-{slug}'],
        capture_output=True, text=True
    )
    return result.stdout.strip() == 'active'


def get_instance_stats(slug):
    stats = {'users': 0, 'ram_mb': 0}
    inst_dir = INSTANCES_DIR / slug
    db_path = inst_dir / 'sshpanel.db'
    if db_path.exists():
        try:
            conn = sqlite3.connect(str(db_path))
            stats['users'] = conn.execute('SELECT COUNT(*) FROM ssh_users').fetchone()[0]
            conn.close()
        except Exception:
            pass
    result = subprocess.run(
        ['systemctl', 'show', f'sshpanel-{slug}', '--property=MemoryCurrent'],
        capture_output=True, text=True
    )
    if result.returncode == 0:
        mem = result.stdout.strip().replace('MemoryCurrent=', '')
        try:
            stats['ram_mb'] = round(int(mem) / (1024 * 1024), 1)
        except ValueError:
            pass
    return stats


def generate_csrf_token():
    if '_csrf_token' not in session:
        session['_csrf_token'] = secrets.token_hex(32)
    return session['_csrf_token']


# ── Auth ──
def master_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get('master_authenticated'):
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated


# ── Rutas ──

@app.route('/login', methods=['GET', 'POST'])
@rate_limit(10, 60)
def login():
    config_data = load_config()

    # Verificar bloqueo
    if config_data.get('locked_until'):
        lock_time = datetime.fromisoformat(config_data['locked_until'])
        if datetime.utcnow() < lock_time:
            mins = int((lock_time - datetime.utcnow()).total_seconds() / 60) + 1
            return render_template('login.html', error=f'Cuenta bloqueada. Intenta en {mins} minuto(s).')

    if request.method == 'POST':
        password = request.form.get('password', '')

        if check_password_hash(config_data['password_hash'], password):
            config_data['login_attempts'] = 0
            config_data['locked_until'] = None
            save_config(config_data)
            session['master_authenticated'] = True
            session['_csrf_token'] = secrets.token_hex(32)
            return redirect(url_for('dashboard'))
        else:
            config_data['login_attempts'] = config_data.get('login_attempts', 0) + 1
            if config_data['login_attempts'] >= 5:
                config_data['locked_until'] = (datetime.utcnow() + timedelta(minutes=15)).isoformat()
                save_config(config_data)
                return render_template('login.html', error='Demasiados intentos. Cuenta bloqueada por 15 minutos.')
            save_config(config_data)
            remaining = 5 - config_data['login_attempts']
            return render_template('login.html', error=f'Contraseña incorrecta. {remaining} intento(s) restante(s).')

    return render_template('login.html')


@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))


@app.route('/')
@master_required
def dashboard():
    scan_filesystem()
    instances = load_instances()
    for inst in instances:
        inst['is_active'] = check_service_status(inst['slug'])
        inst['stats'] = get_instance_stats(inst['slug'])
    save_instances(instances)
    now_iso = datetime.utcnow().isoformat()[:10]
    csrf_token = generate_csrf_token()
    return render_template('dashboard.html', instances=instances, now_iso=now_iso, csrf_token=csrf_token)


@app.route('/profile', methods=['GET', 'POST'])
@master_required
def profile():
    if request.method == 'POST':
        current_pw = request.form.get('current_password', '')
        new_pw = request.form.get('new_password', '')
        confirm_pw = request.form.get('confirm_password', '')

        config_data = load_config()
        if not check_password_hash(config_data['password_hash'], current_pw):
            flash('Contraseña actual incorrecta', 'danger')
            return redirect(url_for('profile'))

        if len(new_pw) < 6:
            flash('La nueva contraseña debe tener al menos 6 caracteres', 'danger')
            return redirect(url_for('profile'))

        if new_pw != confirm_pw:
            flash('Las contraseñas no coinciden', 'danger')
            return redirect(url_for('profile'))

        config_data['password_hash'] = generate_password_hash(new_pw)
        save_config(config_data)
        flash('Contraseña cambiada exitosamente', 'success')
        return redirect(url_for('dashboard'))

    return render_template('profile.html')


@app.route('/create', methods=['POST'])
@master_required
def create():
    slug = request.form.get('slug', '').strip().lower()
    subdomain = request.form.get('subdomain', '').strip()

    if not slug or not subdomain:
        flash('Slug y subdominio son requeridos', 'danger')
        return redirect(url_for('dashboard'))

    if not slug.replace('-', '').replace('_', '').isalnum():
        flash('Slug inválido: solo letras, números, guiones', 'danger')
        return redirect(url_for('dashboard'))

    if Path(INSTANCES_DIR / slug).exists():
        flash(f'La instancia "{slug}" ya existe', 'danger')
        return redirect(url_for('dashboard'))

    script = BASE_DIR / 'crear-cliente.sh'
    result = subprocess.run(
        ['bash', str(script), slug, subdomain],
        capture_output=True, text=True, timeout=120
    )

    if result.returncode == 0:
        flash(f'Instancia "{slug}" creada exitosamente en https://{subdomain}', 'success')
        scan_filesystem()
    else:
        flash(f'Error al crear: {result.stderr[:200]}', 'danger')

    return redirect(url_for('dashboard'))


@app.route('/delete/<slug>', methods=['POST'])
@master_required
def delete(slug):
    script = BASE_DIR / 'eliminar-cliente.sh'
    result = subprocess.run(
        ['bash', str(script), slug],
        capture_output=True, text=True, timeout=60
    )

    if result.returncode == 0:
        data = [i for i in load_instances() if i['slug'] != slug]
        save_instances(data)
        flash(f'Instancia "{slug}" eliminada', 'success')
    else:
        flash(f'Error al eliminar: {result.stderr[:200]}', 'danger')

    return redirect(url_for('dashboard'))


@app.route('/set-expiry/<slug>', methods=['POST'])
@master_required
def set_expiry(slug):
    expires_at = request.form.get('expires_at', '').strip()
    data = load_instances()
    for inst in data:
        if inst['slug'] == slug:
            inst['expires_at'] = expires_at if expires_at else None
            break
    save_instances(data)

    if expires_at and expires_at < datetime.utcnow().isoformat()[:10]:
        subprocess.run(['systemctl', 'stop', f'sshpanel-{slug}'], timeout=30)
        flash(f'Instancia "{slug}" vencida — servicio detenido', 'warning')
    else:
        flash(f'Vencimiento de "{slug}" actualizado', 'success')
    return redirect(url_for('dashboard'))


@app.route('/restart/<slug>', methods=['POST'])
@master_required
def restart(slug):
    subprocess.run(['systemctl', 'restart', f'sshpanel-{slug}'], timeout=30)
    flash(f'Servicio de "{slug}" reiniciado', 'success')
    return redirect(url_for('dashboard'))


@app.route('/api/instances')
@master_required
def api_instances():
    from flask import jsonify
    scan_filesystem()
    instances = load_instances()
    for inst in instances:
        inst['is_active'] = check_service_status(inst['slug'])
    return jsonify(instances)


if __name__ == '__main__':
    scan_filesystem()
    app.run(host='0.0.0.0', port=5100, debug=False)
