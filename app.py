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
    app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///sshpanel.db'
    
    db.init_app(app)
    login_manager.init_app(app)
    csrf.init_app(app)
    
    with app.app_context():
        from routes.auth import auth_bp
        from routes.admin import admin_bp
        from routes.reseller_routes import reseller_bp
        from routes.api import api_bp
        
        app.register_blueprint(auth_bp, url_prefix='/auth')
        app.register_blueprint(admin_bp, url_prefix='/admin')
        app.register_blueprint(reseller_bp, url_prefix='/reseller')
        app.register_blueprint(api_bp, url_prefix='/api')
        
        # Excluir rutas API de CSRF (usadas por JS y curl)
        csrf.exempt(api_bp)
        
        # Redirigir raíz al login
        @app.route('/')
        def index():
            from flask import redirect
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
