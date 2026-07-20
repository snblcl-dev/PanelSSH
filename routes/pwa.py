from flask import Blueprint, make_response, current_app

pwa_bp = Blueprint('pwa', __name__)


@pwa_bp.route('/manifest.json')
def manifest():
    name = current_app.config.get('PWA_NAME', 'PanelSSH')
    short = current_app.config.get('PWA_SHORT', 'Panel')
    theme = current_app.config.get('PWA_THEME', '#0f172a')
    bg = current_app.config.get('PWA_BG', '#0f172a')

    manifest = {
        "name": name,
        "short_name": short,
        "start_url": "/",
        "display": "standalone",
        "background_color": bg,
        "theme_color": theme,
        "icons": [{
            "src": "/pwa-icon.svg",
            "sizes": "512x512",
            "type": "image/svg+xml",
            "purpose": "any"
        }]
    }
    response = make_response(manifest)
    response.headers['Content-Type'] = 'application/manifest+json'
    return response


@pwa_bp.route('/sw.js')
def service_worker():
    js = """
const CACHE = "panelssh-v1";
self.addEventListener("install", e => {
    e.waitUntil(self.skipWaiting());
});
self.addEventListener("activate", e => {
    e.waitUntil(self.clients.claim());
});
self.addEventListener("fetch", e => {
    e.respondWith(
        fetch(e.request).catch(() => caches.match(e.request))
    );
});
"""
    response = make_response(js)
    response.headers['Content-Type'] = 'application/javascript'
    return response


@pwa_bp.route('/pwa-icon.svg')
def icon():
    svg = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 512 512">
  <rect width="512" height="512" rx="100" fill="#0f172a"/>
  <g fill="none" stroke="#38bdf8" stroke-width="28" stroke-linecap="round">
    <rect x="100" y="140" width="312" height="230" rx="20"/>
    <line x1="160" y1="200" x2="352" y2="200"/>
    <line x1="160" y1="260" x2="300" y2="260"/>
    <line x1="160" y1="320" x2="260" y2="320"/>
    <circle cx="370" cy="370" r="50" stroke-width="22"/>
    <line x1="370" y1="345" x2="370" y2="395"/>
    <line x1="345" y1="370" x2="395" y2="370"/>
  </g>
</svg>"""
    response = make_response(svg)
    response.headers['Content-Type'] = 'image/svg+xml'
    return response
