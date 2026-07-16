"""
Panel Maestro — Gestión de instancias SaaS (v2)
Dashboard de recursos VPS, stats globales, limites, mantenimiento, logs.
"""
import os
import json
import sqlite3
import secrets
import shutil
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
LOG_FILE = Path(__file__).parent / 'activity.log'

# ── Config ──
def load_config():
    if CONFIG_FILE.exists():
        return json.loads(CONFIG_FILE.read_text())
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

# ── Helpers ──
def load_instances():
    if MASTER_DB.exists():
        return json.loads(MASTER_DB.read_text())
    return []

def save_instances(data):
    MASTER_DB.write_text(json.dumps(data, indent=2, default=str))

def log_activity(action, detail=''):
    """Registra actividad del panel maestro"""
    entry = {
        'time': datetime.utcnow().isoformat(),
        'action': action,
        'detail': detail,
    }
    try:
        existing = json.loads(LOG_FILE.read_text()) if LOG_FILE.exists() else []
    except:
        existing = []
    existing.insert(0, entry)
    LOG_FILE.write_text(json.dumps(existing[:100], indent=2))

def get_system_stats():
    """CPU, RAM, Disco del VPS"""
    stats = {'cpu': 0, 'ram_pct': 0, 'ram_used': '0G', 'ram_total': '0G', 'disk_pct': 0, 'disk_used': '0G', 'disk_total': '0G'}

    # RAM
    try:
        mem = subprocess.run(['free', '-m'], capture_output=True, text=True)
        lines = mem.stdout.strip().split('\n')
        if len(lines) > 1:
            parts = lines[1].split()
            total = int(parts[1])
            used = int(parts[2])
            stats['ram_pct'] = round(used / total * 100, 1)
            stats['ram_used'] = f'{used/1024:.1f}G'
            stats['ram_total'] = f'{total/1024:.1f}G'
    except: pass

    # CPU
    try:
        load = subprocess.run(['cat', '/proc/loadavg'], capture_output=True, text=True)
        stats['cpu'] = round(float(load.stdout.split()[0]) * 100 / os.cpu_count(), 1)
    except: pass

    # Disco
    try:
        disk = shutil.disk_usage('/')
        stats['disk_pct'] = round(disk.used / disk.total * 100, 1)
        stats['disk_used'] = f'{disk.used/(1024**3):.1f}G'
        stats['disk_total'] = f'{disk.total/(1024**3):.1f}G'
    except: pass

    return stats

def get_global_stats():
    """Suma stats de todas las instancias"""
    total_users = 0
    total_resellers = 0
    total_servers = 0
    active_instances = 0

    for d in (INSTANCES_DIR.iterdir() if INSTANCES_DIR.exists() else []):
        if not d.is_dir(): continue
        db_path = d / 'sshpanel.db'
        if db_path.exists():
            try:
                conn = sqlite3.connect(str(db_path))
                total_users += conn.execute('SELECT COUNT(*) FROM ssh_users').fetchone()[0]
                total_resellers += conn.execute('SELECT COUNT(*) FROM resellers').fetchone()[0]
                total_servers += conn.execute('SELECT COUNT(*) FROM servers').fetchone()[0]
                conn.close()
                active_instances += 1
            except: pass

    return {
        'total_users': total_users,
        'total_resellers': total_resellers,
        'total_servers': total_servers,
        'active_instances': active_instances,
        'total_instances': len([d for d in (INSTANCES_DIR.iterdir() if INSTANCES_DIR.exists() else []) if d.is_dir()]),
    }

def get_instance_port(slug):
    sf = Path(f'/etc/systemd/system/sshpanel-{slug}.service')
    if sf.exists():
        for line in sf.read_text().split('\n'):
            if 'SSHPANEL_PORT=' in line:
                return int(line.split('=')[-1].strip())
    return 0

def check_service_status(slug):
    r = subprocess.run(['systemctl', 'is-active', f'sshpanel-{slug}'], capture_output=True, text=True)
    return r.stdout.strip() == 'active'

def scan_filesystem():
    registered = {i['slug'] for i in load_instances()}
    if not INSTANCES_DIR.exists(): return
    for d in INSTANCES_DIR.iterdir():
        if d.is_dir() and d.name not in registered:
            port = get_instance_port(d.name)
            data = load_instances()
            data.append({
                'slug': d.name, 'subdomain': '', 'port': port,
                'created_at': datetime.fromtimestamp(d.stat().st_ctime).isoformat(),
                'expires_at': None, 'notes': '',
                'limits': {'max_users': -1, 'max_resellers': -1, 'max_servers': -1},
                'maintenance': False, 'maintenance_msg': '',
                'systemd_service': f'sshpanel-{d.name}',
                'is_active': check_service_status(d.name),
            })
            save_instances(data)

def get_instance_stats(slug):
    stats = {'users': 0, 'resellers': 0, 'servers': 0, 'ram_mb': 0}
    inst_dir = INSTANCES_DIR / slug
    db_path = inst_dir / 'sshpanel.db'
    if db_path.exists():
        try:
            conn = sqlite3.connect(str(db_path))
            stats['users'] = conn.execute('SELECT COUNT(*) FROM ssh_users').fetchone()[0]
            stats['resellers'] = conn.execute('SELECT COUNT(*) FROM resellers').fetchone()[0]
            stats['servers'] = conn.execute('SELECT COUNT(*) FROM servers').fetchone()[0]
            conn.close()
        except: pass
    r = subprocess.run(['systemctl', 'show', f'sshpanel-{slug}', '--property=MemoryCurrent'], capture_output=True, text=True)
    if r.returncode == 0:
        mem = r.stdout.strip().replace('MemoryCurrent=', '')
        try: stats['ram_mb'] = round(int(mem)/(1024*1024), 1)
        except: pass
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
    if config_data.get('locked_until'):
        lock_time = datetime.fromisoformat(config_data['locked_until'])
        if datetime.utcnow() < lock_time:
            mins = int((lock_time - datetime.utcnow()).total_seconds()/60)+1
            return render_template('login.html', error=f'Cuenta bloqueada. Intenta en {mins} minuto(s).')
    if request.method == 'POST':
        password = request.form.get('password','')
        if check_password_hash(config_data['password_hash'], password):
            config_data['login_attempts'] = 0
            config_data['locked_until'] = None
            save_config(config_data)
            session['master_authenticated'] = True
            session['_csrf_token'] = secrets.token_hex(32)
            log_activity('login')
            return redirect(url_for('dashboard'))
        else:
            config_data['login_attempts'] = config_data.get('login_attempts',0)+1
            if config_data['login_attempts'] >= 5:
                config_data['locked_until'] = (datetime.utcnow()+timedelta(minutes=15)).isoformat()
                save_config(config_data)
                return render_template('login.html', error='Demasiados intentos. Bloqueado 15 minutos.')
            save_config(config_data)
            remaining = 5 - config_data['login_attempts']
            return render_template('login.html', error=f'Contraseña incorrecta. {remaining} intento(s).')
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
        inst['status_icon'] = 'check-circle' if inst['is_active'] else 'times-circle'
    save_instances(instances)
    now_iso = datetime.utcnow().isoformat()[:10]
    sys_stats = get_system_stats()
    global_stats = get_global_stats()
    csrf_token = generate_csrf_token()
    # Logs del maestro
    try: activity_log = json.loads(LOG_FILE.read_text()) if LOG_FILE.exists() else []
    except: activity_log = []
    return render_template('dashboard.html', instances=instances, now_iso=now_iso,
                          csrf_token=csrf_token, sys_stats=sys_stats,
                          global_stats=global_stats, activity_log=activity_log[:20])

@app.route('/profile', methods=['GET','POST'])
@master_required
def profile():
    if request.method == 'POST':
        cp = request.form.get('current_password','')
        np = request.form.get('new_password','')
        cf = request.form.get('confirm_password','')
        config_data = load_config()
        if not check_password_hash(config_data['password_hash'], cp):
            flash('Contraseña actual incorrecta','danger'); return redirect(url_for('profile'))
        if len(np) < 6:
            flash('Mínimo 6 caracteres','danger'); return redirect(url_for('profile'))
        if np != cf:
            flash('No coinciden','danger'); return redirect(url_for('profile'))
        config_data['password_hash'] = generate_password_hash(np)
        save_config(config_data)
        log_activity('change_password')
        flash('Contraseña cambiada','success'); return redirect(url_for('dashboard'))
    return render_template('profile.html')

@app.route('/create', methods=['POST'])
@master_required
def create():
    slug = request.form.get('slug','').strip().lower()
    subdomain = request.form.get('subdomain','').strip()
    max_users = request.form.get('max_users','-1').strip()
    max_resellers = request.form.get('max_resellers','-1').strip()
    max_servers = request.form.get('max_servers','-1').strip()

    if not slug or not subdomain:
        flash('Slug y subdominio requeridos','danger'); return redirect(url_for('dashboard'))
    if not slug.replace('-','').replace('_','').isalnum():
        flash('Slug inválido','danger'); return redirect(url_for('dashboard'))
    if Path(INSTANCES_DIR / slug).exists():
        flash(f'"{slug}" ya existe','danger'); return redirect(url_for('dashboard'))

    script = BASE_DIR / 'crear-cliente.sh'
    result = subprocess.run(['bash', str(script), slug, subdomain], capture_output=True, text=True, timeout=120)
    if result.returncode == 0:
        scan_filesystem()
        # Guardar límites
        data = load_instances()
        for i in data:
            if i['slug'] == slug:
                i['limits'] = {'max_users': int(max_users) if max_users != '-1' else -1,
                               'max_resellers': int(max_resellers) if max_resellers != '-1' else -1,
                               'max_servers': int(max_servers) if max_servers != '-1' else -1}
        save_instances(data)
        log_activity('create_instance', slug)
        flash(f'Instancia "{slug}" creada en https://{subdomain}','success')
    else:
        flash(f'Error: {result.stderr[:200]}','danger')
    return redirect(url_for('dashboard'))

@app.route('/delete/<slug>', methods=['POST'])
@master_required
def delete(slug):
    script = BASE_DIR / 'eliminar-cliente.sh'
    subprocess.run(['bash', str(script), slug], capture_output=True, text=True, timeout=60)
    data = [i for i in load_instances() if i['slug'] != slug]
    save_instances(data)
    log_activity('delete_instance', slug)
    flash(f'Instancia "{slug}" eliminada','success')
    return redirect(url_for('dashboard'))

@app.route('/set-expiry/<slug>', methods=['POST'])
@master_required
def set_expiry(slug):
    expires_at = request.form.get('expires_at','').strip()
    data = load_instances()
    for inst in data:
        if inst['slug'] == slug:
            inst['expires_at'] = expires_at if expires_at else None; break
    save_instances(data)
    if expires_at and expires_at < datetime.utcnow().isoformat()[:10]:
        subprocess.run(['systemctl', 'stop', f'sshpanel-{slug}'], timeout=30)
        flash(f'"{slug}" vencida — detenida','warning')
    else:
        flash(f'Vencimiento de "{slug}" actualizado','success')
    return redirect(url_for('dashboard'))

@app.route('/set-limits/<slug>', methods=['POST'])
@master_required
def set_limits(slug):
    max_users = request.form.get('max_users','-1')
    max_resellers = request.form.get('max_resellers','-1')
    max_servers = request.form.get('max_servers','-1')
    data = load_instances()
    for inst in data:
        if inst['slug'] == slug:
            inst['limits'] = {'max_users': int(max_users) if max_users != '-1' else -1,
                              'max_resellers': int(max_resellers) if max_resellers != '-1' else -1,
                              'max_servers': int(max_servers) if max_servers != '-1' else -1}; break
    save_instances(data)
    log_activity('set_limits', slug)
    flash(f'Límites de "{slug}" actualizados','success')
    return redirect(url_for('dashboard'))

@app.route('/toggle-maintenance/<slug>', methods=['POST'])
@master_required
def toggle_maintenance(slug):
    msg = request.form.get('message','Panel en mantenimiento. Vuelve pronto.')
    data = load_instances()
    for inst in data:
        if inst['slug'] == slug:
            inst['maintenance'] = not inst.get('maintenance', False)
            inst['maintenance_msg'] = msg if inst['maintenance'] else ''; break
    save_instances(data)
    status = 'activado' if [i for i in data if i['slug']==slug][0]['maintenance'] else 'desactivado'
    log_activity('maintenance', f'{slug} -> {status}')
    flash(f'Mantenimiento {status} para "{slug}"','success')
    return redirect(url_for('dashboard'))

@app.route('/mass-update', methods=['POST'])
@master_required
def mass_update():
    """git pull en todas las instancias"""
    results = []
    for d in (INSTANCES_DIR.iterdir() if INSTANCES_DIR.exists() else []):
        if not d.is_dir(): continue
        r = subprocess.run(['git', '-C', str(d), 'pull'], capture_output=True, text=True, timeout=30)
        results.append({'slug': d.name, 'ok': r.returncode == 0, 'msg': r.stdout.strip()[-100:]})
    log_activity('mass_update', f'{len(results)} instancias actualizadas')
    flash(f'{len([r for r in results if r["ok"]])}/{len(results)} instancias actualizadas','success')
    return redirect(url_for('dashboard'))

@app.route('/restart/<slug>', methods=['POST'])
@master_required
def restart(slug):
    subprocess.run(['systemctl', 'restart', f'sshpanel-{slug}'], timeout=30)
    flash(f'Servicio "{slug}" reiniciado','success')
    return redirect(url_for('dashboard'))

@app.route('/backup/<slug>')
@master_required
def backup(slug):
    """Descargar backup de la DB de una instancia"""
    from flask import send_file
    db_path = INSTANCES_DIR / slug / 'sshpanel.db'
    if not db_path.exists():
        flash('DB no encontrada','danger'); return redirect(url_for('dashboard'))
    log_activity('backup', slug)
    return send_file(str(db_path), as_attachment=True, download_name=f'{slug}-backup-{datetime.utcnow().strftime("%Y%m%d")}.db')

if __name__ == '__main__':
    scan_filesystem()
    app.run(host='0.0.0.0', port=5100, debug=False)
