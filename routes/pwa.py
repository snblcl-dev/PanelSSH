from flask import Blueprint, make_response, current_app
import struct, zlib

pwa_bp = Blueprint('pwa', __name__)


def _make_png(width, height, color=(15, 23, 42)):
    """Genera un PNG solido con color RGB (sin Pillow)"""
    def _chunk(ctype, data):
        c = ctype + data
        crc = struct.pack('>I', zlib.crc32(c) & 0xffffffff)
        return struct.pack('>I', len(data)) + c + crc

    header = b'\x89PNG\r\n\x1a\n'
    ihdr = _chunk(b'IHDR', struct.pack('>IIBBBBB', width, height, 8, 2, 0, 0, 0))

    raw = b''
    for _ in range(height):
        raw += b'\x00' + bytes(color) * width

    idat = _chunk(b'IDAT', zlib.compress(raw))
    iend = _chunk(b'IEND', b'')
    return header + ihdr + idat + iend


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
        "icons": [
            {"src": "/pwa-icon-192.png", "sizes": "192x192", "type": "image/png", "purpose": "any"},
            {"src": "/pwa-icon-512.png", "sizes": "512x512", "type": "image/png", "purpose": "any maskable"},
            {"src": "/pwa-icon.svg", "sizes": "512x512", "type": "image/svg+xml", "purpose": "any"}
        ]
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


@pwa_bp.route('/pwa-icon-192.png')
def icon_192():
    response = make_response(_make_png(192, 192))
    response.headers['Content-Type'] = 'image/png'
    return response


@pwa_bp.route('/pwa-icon-512.png')
def icon_512():
    response = make_response(_make_png(512, 512))
    response.headers['Content-Type'] = 'image/png'
    return response


@pwa_bp.route('/.well-known/assetlinks.json')
def assetlinks():
    import os as _os
    sha256 = _os.environ.get('PWA_SHA256', '')
    package = _os.environ.get('PWA_PACKAGE_NAME', 'com.luxvpn.panel')
    data = [{
        "relation": ["delegate_permission/common.handle_all_urls"],
        "target": {
            "namespace": "android_app",
            "package_name": package,
            "sha256_cert_fingerprints": [sha256]
        }
    }] if sha256 else []
    response = make_response(data)
    response.headers['Content-Type'] = 'application/json'
    return response
