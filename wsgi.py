import os
from app import create_app

app = create_app()

if __name__ == '__main__':
    from waitress import serve
    port = int(os.environ.get('SSHPANEL_PORT', 5000))
    serve(app, host='0.0.0.0', port=port)
