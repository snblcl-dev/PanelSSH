"""
SSH Panel - Aplicación Web para Administración de Usuarios Dropbear SSH
"""
from flask import Flask
from flask_login import LoginManager
from flask_wtf.csrf import CSRFProtect
from config import Config
from models import db, init_db, Admin, Reseller


login_manager = LoginManager()
login_manager.login_view = 'auth.login'
login_manager.login_message = 'Por favor inicia sesión para acceder al panel.'
csrf = CSRFProtect()


def create_app():
    app = Flask(__name__)
    app.config.from_object(Config)
    
    db.init_app(app)
    login_manager.init_app(app)
    csrf.init_app(app)
    
    with app.app_context():
        from routes.auth import auth_bp
        from routes.admin import admin_bp
        from routes.reseller_routes import reseller_bp
        from routes.api import api_bp
        from routes.pwa import pwa_bp

        app.register_blueprint(auth_bp, url_prefix='/auth')
        app.register_blueprint(admin_bp, url_prefix='/admin')
        app.register_blueprint(reseller_bp, url_prefix='/reseller')
        app.register_blueprint(api_bp, url_prefix='/api')
        app.register_blueprint(pwa_bp)

        # Excluir rutas API de CSRF (usadas por JS y curl)
        csrf.exempt(api_bp)

        # Manejar errores CSRF con mensaje amigable
        from flask_wtf.csrf import CSRFError
        @app.errorhandler(CSRFError)
        def handle_csrf_error(e):
            from flask import flash, redirect, request
            flash('La sesión expiró por inactividad. Intenta de nuevo.', 'warning')
            return redirect(request.referrer or url_for('index'))

        # Variable global para templates
        @app.context_processor
        def inject_globals():
            return {'saas_mode': app.config.get('PANEL_MODE') == 'saas'}

        # Modo mantenimiento (SaaS)
        import json as _json
        from pathlib import Path as _Path
        _maint_file = _Path(app.config.get('INSTANCE_DIR', '')) / '.maintenance'

        @app.before_request
        def check_maintenance():
            from flask import render_template, request as _req
            if _maint_file.exists() and _req.endpoint not in ('auth.login', 'auth.logout', 'static'):
                try:
                    _maint = _json.loads(_maint_file.read_text())
                    if _maint.get('active'):
                        msg = _maint.get('message', 'Panel en mantenimiento. Vuelve pronto.')
                        return render_template('maintenance.html', message=msg), 503
                except Exception:
                    pass

        # Redirigir raíz según autenticación
        @app.route('/')
        def index():
            from flask import redirect
            from flask_login import current_user
            if current_user.is_authenticated:
                if current_user.__class__.__name__ == 'Admin':
                    return redirect('/admin/')
                else:
                    return redirect('/reseller/')
            return redirect('/auth/login')

        db.create_all()
        init_db()
    
    return app


@login_manager.user_loader
def load_user(user_id):
    """Carga un usuario (admin o reseller) por ID con prefijo"""
    if '_' not in user_id:
        return None
    typ, uid = user_id.split('_', 1)
    if typ == 'a':
        return Admin.query.get(int(uid))
    elif typ == 'r':
        return Reseller.query.get(int(uid))
    return None


if __name__ == '__main__':
    app = create_app()
    app.run(host='0.0.0.0', port=5000, debug=True)
