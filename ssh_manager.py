"""
Módulo para gestionar usuarios en el sistema Linux/Dropbear.
Soporta dos modos:
  - 'local': ejecuta comandos directamente en el sistema (Linux con Dropbear)
  - 'script': ejecuta un script externo para manipulación remota
"""
import subprocess
import os
import sys
from datetime import datetime
from flask import current_app

# Módulos solo disponibles en Unix/Linux
if sys.platform.startswith('linux') or sys.platform == 'darwin':
    import pwd
    import grp


def _run_command(cmd, timeout=10):
    """Ejecuta un comando del sistema y devuelve el resultado"""
    try:
        result = subprocess.run(
            cmd,
            shell=True,
            capture_output=True,
            text=True,
            timeout=timeout
        )
        return {
            'success': result.returncode == 0,
            'stdout': result.stdout.strip(),
            'stderr': result.stderr.strip(),
            'returncode': result.returncode
        }
    except subprocess.TimeoutExpired:
        return {'success': False, 'stdout': '', 'stderr': 'Timeout', 'returncode': -1}
    except Exception as e:
        return {'success': False, 'stdout': '', 'stderr': str(e), 'returncode': -1}


def system_create_user(username, password, expires_at, max_connections=1, shell='/bin/false'):
    """
    Crea un usuario en el sistema Linux.
    En modo 'local' usa comandos del sistema directamente.
    """
    backend = current_app.config.get('SSH_BACKEND', 'local')
    
    if backend == 'local':
        return _local_create_user(username, password, expires_at, max_connections, shell)
    else:
        return _script_create_user(username, password, expires_at, max_connections, shell)


def system_delete_user(username):
    """Elimina un usuario del sistema"""
    backend = current_app.config.get('SSH_BACKEND', 'local')
    if backend == 'local':
        return _local_delete_user(username)
    else:
        return _script_delete_user(username)


def system_block_user(username):
    """Bloquea un usuario (bloquea su shell)"""
    backend = current_app.config.get('SSH_BACKEND', 'local')
    if backend == 'local':
        return _local_block_user(username)
    else:
        return _script_block_user(username)


def system_unblock_user(username, shell='/bin/false'):
    """Desbloquea un usuario"""
    backend = current_app.config.get('SSH_BACKEND', 'local')
    if backend == 'local':
        return _local_unblock_user(username, shell)
    else:
        return _script_unblock_user(username, shell)


def system_change_password(username, new_password):
    """Cambia la contraseña de un usuario del sistema"""
    backend = current_app.config.get('SSH_BACKEND', 'local')
    if backend == 'local':
        return _local_change_password(username, new_password)
    else:
        return _script_change_password(username, new_password)


def system_get_online_users():
    """
    Obtiene usuarios conectados vía SSH/Dropbear.
    Retorna lista de dicts con username y count de conexiones.
    """
    backend = current_app.config.get('SSH_BACKEND', 'local')
    if backend == 'local':
        return _local_get_online_users()
    else:
        return _script_get_online_users()


def system_disconnect_user(username):
    """Desconecta todas las sesiones de un usuario"""
    backend = current_app.config.get('SSH_BACKEND', 'local')
    if backend == 'local':
        return _local_disconnect_user(username)
    else:
        return _script_disconnect_user(username)


def system_set_expiry(username, expires_at):
    """Configura la fecha de expiración de un usuario del sistema"""
    backend = current_app.config.get('SSH_BACKEND', 'local')
    if backend == 'local':
        return _local_set_expiry(username, expires_at)
    else:
        return _script_set_expiry(username, expires_at)


# ================ IMPLEMENTACIÓN LOCAL (Linux con Dropbear) ================

def _local_create_user(username, password, expires_at, max_connections, shell):
    commands = [
        f'useradd -M -s {shell} {username} 2>/dev/null',
        f'echo "{username}:{password}" | chpasswd',
        f'chage -E {expires_at.strftime("%Y-%m-%d")} {username}',
    ]
    
    for cmd in commands:
        r = _run_command(cmd)
        if not r['success']:
            return r
    
    return {'success': True, 'stdout': f'Usuario {username} creado exitosamente', 'stderr': '', 'returncode': 0}


def _local_delete_user(username):
    commands = [
        f'pkill -u {username} 2>/dev/null',
        f'userdel {username} 2>/dev/null',
    ]
    for cmd in commands:
        _run_command(cmd)
    return {'success': True, 'stdout': f'Usuario {username} eliminado', 'stderr': '', 'returncode': 0}


def _local_block_user(username):
    """Bloquea un usuario: bloquea contraseña, expira cuenta y mata sesiones"""
    commands = [
        f'passwd -l {username} 2>/dev/null',                    # Bloquear contraseña
        f'usermod -e 1 {username} 2>/dev/null',                  # Expirar cuenta
        f'pkill -u {username} 2>/dev/null',                      # Matar procesos
        f"ss -tnp 2>/dev/null | grep dropbear | awk '{{print $6}}' | grep -o 'pid=[0-9]*' | cut -d= -f2 | xargs -r kill 2>/dev/null",  # Matar conexiones dropbear
    ]
    for cmd in commands:
        _run_command(cmd)
    return {'success': True, 'stdout': f'Usuario {username} bloqueado', 'stderr': '', 'returncode': 0}


def _local_unblock_user(username, shell):
    """Desbloquea un usuario: desbloquea contraseña y remueve expiración"""
    # Determinar shell (si el panel usa /bin/false, lo dejamos)
    if not shell or shell == '/usr/sbin/nologin':
        shell = '/bin/false'
    
    commands = [
        f'passwd -u {username} 2>/dev/null',                     # Desbloquear contraseña
        f'usermod -e -1 {username} 2>/dev/null',                 # Remover expiración
        f'usermod -s {shell} {username} 2>/dev/null',            # Restaurar shell
    ]
    for cmd in commands:
        _run_command(cmd)
    return {'success': True, 'stdout': f'Usuario {username} desbloqueado', 'stderr': '', 'returncode': 0}


def _local_disconnect_user(username):
    """Desconecta todas las sesiones de un usuario"""
    commands = [
        f'pkill -u {username} 2>/dev/null',                      # Matar todos sus procesos
        f"kill $(ps -u {username} -o pid= 2>/dev/null) 2>/dev/null",  # Otra forma de matar
        f"ss -tnp 2>/dev/null | grep dropbear | awk '{{print $6}}' | grep -o 'pid=[0-9]*' | cut -d= -f2 | xargs -r kill 2>/dev/null",  # Matar dropbear connections
    ]
    for cmd in commands:
        _run_command(cmd)
    return {'success': True, 'stdout': f'Usuario {username} desconectado', 'stderr': '', 'returncode': 0}


def _local_change_password(username, new_password):
    r = _run_command(f'echo "{username}:{new_password}" | chpasswd')
    return r


def _local_get_online_users():
    """Obtiene usuarios conectados vía SSH/Dropbear usando PID para correlacionar con auth.log"""
    # Por cada conexion ESTAB a dropbear, extraer el PID y buscar en auth.log
    cmd = """ss -tnp 2>/dev/null | grep dropbear | grep ESTAB | grep -oP 'pid=\\K[0-9]+' | sort -u | while read pid; do
  grep -a "dropbear\\[$pid\\].*Password auth succeeded" /var/log/auth.log 2>/dev/null | tail -1 | grep -oP "for '\\K[^']+"
done | sort | uniq -c | sort -rn
"""
    r = _run_command(cmd)
    
    if r['success'] and r['stdout']:
        users = []
        for line in r['stdout'].strip().split('\n'):
            line = line.strip()
            if not line: continue
            parts = line.split()
            if len(parts) >= 2:
                username = parts[1]
                count = int(parts[0])
                if username not in ('root', 'daemon', 'bin', 'sys', 'nobody'):
                    users.append({'username': username, 'connections': count})
        
        if users:
            return {'success': True, 'users': users, 'stdout': r['stdout'], 'stderr': '', 'returncode': 0}
    
    # Fallback: who
    r2 = _run_command("who | awk '{print $1}' | sort | uniq -c | sort -rn")
    if r2['success'] and r2['stdout']:
        users = []
        for line in r2['stdout'].strip().split('\n'):
            line = line.strip()
            if not line: continue
            parts = line.split()
            if len(parts) >= 2:
                username = parts[1]
                count = int(parts[0])
                if username not in ('root', 'daemon', 'bin', 'sys', 'nobody', 'messagebus'):
                    users.append({'username': username, 'connections': count})
        if users:
            return {'success': True, 'users': users, 'stdout': r2['stdout'], 'stderr': '', 'returncode': 0}
    
    return {'success': True, 'users': [], 'stdout': 'No hay usuarios conectados', 'stderr': '', 'returncode': 0}


# ============================================================
# FUNCIONES REMOTAS (Multi-servidor via SSH con paramiko)
# ============================================================
import shlex

def _get_remote_server(server_id):
    """Obtiene objeto Server por ID"""
    from models import Server
    return Server.query.get(server_id)


def _execute_remote(server, command):
    import paramiko
    import os
    try:
        ssh = paramiko.SSHClient()
        ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        kwargs = {'timeout': 15}
        if server.auth_method == 'password' and server.password:
            kwargs['password'] = server.password
        else:
            kwargs['key_filename'] = server.ssh_key_path or os.path.expanduser('~/.ssh/id_rsa')
        ssh.connect(server.host, port=server.port, username=server.ssh_user, **kwargs)
        stdin, stdout, stderr = ssh.exec_command(command, timeout=30)
        out = stdout.read().decode('utf-8', errors='replace').strip()
        err = stderr.read().decode('utf-8', errors='replace').strip()
        rc = stdout.channel.recv_exit_status()
        ssh.close()
        return {'success': rc == 0, 'stdout': out, 'stderr': err, 'returncode': rc}
    except Exception as e:
        return {'success': False, 'stdout': '', 'stderr': str(e), 'returncode': -1}


def _remote_create_user(server, username, password, expires_at, max_connections, shell='/bin/false'):
    """Crea usuario en servidor remoto"""
    u = shlex.quote(username)
    p = shlex.quote(password)
    s = shlex.quote(shell)
    commands = [
        f'useradd -M -s {s} {u}',
        f'echo "{u}:{p}" | chpasswd',
        f'chage -E {expires_at.strftime("%Y-%m-%d")} {u}',
    ]
    for cmd in commands:
        r = _execute_remote(server, cmd)
        if not r['success'] and 'already exists' not in r['stderr']:
            return r
    return {'success': True, 'stdout': f'Usuario {username} creado en servidor remoto', 'stderr': '', 'returncode': 0}


def _remote_delete_user(server, username):
    u = shlex.quote(username)
    return _execute_remote(server, f'userdel {u}')


def _remote_block_user(server, username):
    u = shlex.quote(username)
    commands = [
        f'passwd -l {u}',
        f'usermod -e 1 {u}',
        f'pkill -u {u}',
        f"ss -tnp | grep dropbear | awk '{{print $6}}' | grep -o 'pid=[0-9]*' | cut -d= -f2 | xargs -r kill",
    ]
    for cmd in commands:
        _execute_remote(server, cmd)
    return {'success': True, 'stdout': f'Usuario {username} bloqueado en remoto', 'stderr': '', 'returncode': 0}


def _remote_unblock_user(server, username):
    u = shlex.quote(username)
    commands = [
        f'passwd -u {u}',
        f'usermod -e -1 {u}',
    ]
    for cmd in commands:
        _execute_remote(server, cmd)
    return {'success': True, 'stdout': f'Usuario {username} desbloqueado en remoto', 'stderr': '', 'returncode': 0}


def _remote_change_password(server, username, new_password):
    u = shlex.quote(username)
    p = shlex.quote(new_password)
    return _execute_remote(server, f'echo "{u}:{p}" | chpasswd')


def _remote_disconnect_user(server, username):
    u = shlex.quote(username)
    commands = [
        f'pkill -u {u}',
        f"ss -tnp | grep dropbear | awk '{{print $6}}' | grep -o 'pid=[0-9]*' | cut -d= -f2 | xargs -r kill",
    ]
    for cmd in commands:
        _execute_remote(server, cmd)
    return {'success': True, 'stdout': f'Usuario {username} desconectado en remoto', 'stderr': '', 'returncode': 0}


def _remote_set_expiry(server, username, expires_at):
    u = shlex.quote(username)
    return _execute_remote(server, f'chage -E {expires_at.strftime("%Y-%m-%d")} {u}')


def _remote_get_online_users(server):
    """Obtiene usuarios online en servidor remoto"""
    cmd = """ss -tnp 2>/dev/null | grep dropbear | grep ESTAB | grep -oP 'pid=\\K[0-9]+' | sort -u | while read pid; do
  grep -a "dropbear\\[$pid\\].*Password auth succeeded" /var/log/auth.log 2>/dev/null | tail -1 | grep -oP "for '\\K[^']+"
done | sort | uniq -c | sort -rn
"""
    r = _execute_remote(server, cmd)
    if r['success'] and r['stdout']:
        users = []
        for line in r['stdout'].split('\n'):
            line = line.strip()
            if not line: continue
            parts = line.split()
            if len(parts) >= 2:
                username = parts[1]
                count = int(parts[0])
                if username not in ('root', 'daemon', 'bin', 'sys', 'nobody'):
                    users.append({'username': username, 'connections': count})
        return {'success': True, 'users': users}
    return {'success': True, 'users': []}


def system_execute(user, func_name, *args, **kwargs):
    """Dispatcher: ejecuta local o remoto segun server_id del usuario"""
    if user and user.server_id:
        from models import Server
        server = Server.query.get(user.server_id)
        if not server:
            return {'success': False, 'stdout': '', 'stderr': 'Servidor no encontrado', 'returncode': -1}
        
        remote_funcs = {
            'create_user': _remote_create_user,
            'delete_user': _remote_delete_user,
            'block_user': _remote_block_user,
            'unblock_user': _remote_unblock_user,
            'change_password': _remote_change_password,
            'disconnect_user': _remote_disconnect_user,
            'set_expiry': _remote_set_expiry,
        }
        func = remote_funcs.get(func_name)
        if func:
            return func(server, *args, **kwargs)
    
    # Fallback: local
    local_funcs = {
        'create_user': system_create_user,
        'delete_user': system_delete_user,
        'block_user': system_block_user,
        'unblock_user': system_unblock_user,
        'change_password': system_change_password,
        'disconnect_user': system_disconnect_user,
        'set_expiry': system_set_expiry,
    }
    func = local_funcs.get(func_name)
    if func:
        return func(*args, **kwargs)
    return {'success': False, 'stdout': '', 'stderr': 'Funcion no encontrada', 'returncode': -1}


def system_get_online_all():
    """Obtiene online de TODOS los servidores (locales + remotos)"""
    from models import Server
    all_users = []
    
    # Servidores remotos
    for server in Server.query.filter_by(is_active=True).all():
        r = _remote_get_online_users(server)
        for u in r.get('users', []):
            u['server_id'] = server.id
            u['server_name'] = server.name
        all_users.extend(r.get('users', []))
    
    # Local (servidor actual)
    r = system_get_online_users()
    for u in r.get('users', []):
        u['server_id'] = None
        u['server_name'] = 'Local'
    all_users.extend(r.get('users', []))
    
    return all_users


def _local_set_expiry(username, expires_at):
    r = _run_command(f'chage -E {expires_at.strftime("%Y-%m-%d")} {username}')
    return r


# ================ IMPLEMENTACIÓN POR SCRIPT EXTERNO ================

def _script_create_user(username, password, expires_at, max_connections, shell):
    script = current_app.config.get('SSH_SCRIPT_PATH', '/usr/local/bin/ssh-manager.sh')
    r = _run_command(f'{script} create {username} {password} {expires_at.strftime("%Y-%m-%d")} {max_connections}')
    return r


def _script_delete_user(username):
    script = current_app.config.get('SSH_SCRIPT_PATH', '/usr/local/bin/ssh-manager.sh')
    r = _run_command(f'{script} delete {username}')
    return r


def _script_block_user(username):
    script = current_app.config.get('SSH_SCRIPT_PATH', '/usr/local/bin/ssh-manager.sh')
    r = _run_command(f'{script} block {username}')
    return r


def _script_unblock_user(username, shell):
    script = current_app.config.get('SSH_SCRIPT_PATH', '/usr/local/bin/ssh-manager.sh')
    r = _run_command(f'{script} unblock {username}')
    return r


def _script_change_password(username, new_password):
    script = current_app.config.get('SSH_SCRIPT_PATH', '/usr/local/bin/ssh-manager.sh')
    r = _run_command(f'{script} passwd {username} {new_password}')
    return r


def _script_get_online_users():
    script = current_app.config.get('SSH_SCRIPT_PATH', '/usr/local/bin/ssh-manager.sh')
    r = _run_command(f'{script} online')
    return r


def _script_disconnect_user(username):
    script = current_app.config.get('SSH_SCRIPT_PATH', '/usr/local/bin/ssh-manager.sh')
    r = _run_command(f'{script} disconnect {username}')
    return r


def _script_set_expiry(username, expires_at):
    script = current_app.config.get('SSH_SCRIPT_PATH', '/usr/local/bin/ssh-manager.sh')
    r = _run_command(f'{script} expiry {username} {expires_at.strftime("%Y-%m-%d")}')
    return r
