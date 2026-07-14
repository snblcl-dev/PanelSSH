#!/usr/bin/env python3
"""
Script para sincronizar usuarios expirados.
Ejecutar via cron cada 5 minutos:
  */5 * * * * cd /ruta/panel-ssh && venv/bin/python sync_expired.py
"""
import sys
import os

# Asegurar que encuentra los módulos del proyecto
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app import create_app
from ssh_manager import system_sync_expired_users

app = create_app()
with app.app_context():
    count = system_sync_expired_users()
    if count > 0:
        print(f"[SYNC] {count} usuario(s) expirado(s) bloqueado(s)")
