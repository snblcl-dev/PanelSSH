import os
from app import app

if __name__ == '__main__':
    from waitress import serve
    port = int(os.environ.get('MASTER_PORT', 5100))
    serve(app, host='0.0.0.0', port=port)
