"""
Panel Maestro — Gestión de instancias SaaS
Acceso solo para el admin principal.
"""
import os
import json
import sqlite3
import subprocess
from datetime import datetime
from pathlib import Path
from flask import Flask, render_template, redirect, url_for, flash, request
from functools import wraps

app = Flask(__name__)
BASE_DIR = Path(__file__).parent.parent
INSTANCES_DIR = BASE_DIR / 'instances'
MASTER_DB = Path(__file__).parent / 'instances.json'
MASTER_PASSWORD = os.environ.get('MASTER_PASSWORD', 'admin123')

# Secret key simple para sesiones
app.secret_key = os.environ.get('MASTER_SECRET', 'cambiar-esta-clave-en-produccion')


# ── Datos ──
def load_instances():
    if MASTER_DB.exists():
        return json.loads(MASTER_DB.read_text())
    return []


def save_instances(data):
    MASTER_DB.write_text(json.dumps(data, indent=2, default=str))


def scan_filesystem():
    """Sincroniza: busca carpetas en instances/ que no estén en el registro"""
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
    """Lee el puerto del archivo systemd de la instancia"""
    service_file = Path(f'/etc/systemd/system/sshpanel-{slug}.service')
    if service_file.exists():
        content = service_file.read_text()
        for line in content.split('\n'):
            if 'SSHPANEL_PORT=' in line:
                return int(line.split('=')[-1].strip())
    return 0


def check_service_status(slug):
    """Verifica si el servicio systemd está activo"""
    result = subprocess.run(
        ['systemctl', 'is-active', f'sshpanel-{slug}'],
        capture_output=True, text=True
    )
    return result.stdout.strip() == 'active'


def get_instance_stats(slug):
    """Obtiene stats de una instancia: usuarios, RAM"""
    stats = {'users': 0, 'ram_mb': 0}
    inst_dir = INSTANCES_DIR / slug

    # Contar usuarios en la DB
    db_path = inst_dir / 'sshpanel.db'
    if db_path.exists():
        try:
            conn = sqlite3.connect(str(db_path))
            stats['users'] = conn.execute('SELECT COUNT(*) FROM ssh_users').fetchone()[0]
            conn.close()
        except Exception:
            pass

    # RAM del proceso
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


# ── Auth ──
def master_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        auth = request.authorization
        if not auth or auth.password != MASTER_PASSWORD:
            return ('Acceso restringido', 401, {
                'WWW-Authenticate': 'Basic realm="Panel Maestro"'
            })
        return f(*args, **kwargs)
    return decorated


# ── Rutas ──

@app.route('/')
@master_required
def dashboard():
    scan_filesystem()
    instances = load_instances()
    # Actualizar estado de cada instancia
    for inst in instances:
        inst['is_active'] = check_service_status(inst['slug'])
        inst['stats'] = get_instance_stats(inst['slug'])
    save_instances(instances)
    now_iso = datetime.utcnow().isoformat()[:10]
    return render_template('dashboard.html', instances=instances, now_iso=now_iso)


@app.route('/create', methods=['POST'])
@master_required
def create():
    slug = request.form.get('slug', '').strip().lower()
    subdomain = request.form.get('subdomain', '').strip()

    if not slug or not subdomain:
        flash('Slug y subdominio son requeridos', 'danger')
        return redirect(url_for('dashboard'))

    # Validar slug
    if not slug.replace('-', '').replace('_', '').isalnum():
        flash('Slug inválido: solo letras, números, guiones', 'danger')
        return redirect(url_for('dashboard'))

    # Verificar que no exista
    if Path(INSTANCES_DIR / slug).exists():
        flash(f'La instancia "{slug}" ya existe', 'danger')
        return redirect(url_for('dashboard'))

    # Ejecutar script
    script = BASE_DIR / 'crear-cliente.sh'
    result = subprocess.run(
        ['bash', str(script), slug, subdomain],
        capture_output=True, text=True, timeout=120
    )

    if result.returncode == 0:
        flash(f'Instancia "{slug}" creada exitosamente en https://{subdomain}', 'success')
        # Registrar en DB
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
        # Quitar del registro
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

    # Si la fecha ya pasó, apagar
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
