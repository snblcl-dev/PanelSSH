from flask import Blueprint, make_response, current_app
import struct, zlib, math

pwa_bp = Blueprint('pwa', __name__)

# Colores
BG = (0x0f, 0x17, 0x2a)      # slate-900
ACCENT = (0x38, 0xbd, 0xf8)   # cyan-400
WHITE = (0xf1, 0xf5, 0xf9)    # slate-100
DARK = (0x1e, 0x29, 0x3b)     # slate-800


def _make_png(width, height, draw_func):
    """Genera un PNG del tamaño dado usando draw_func(pixels, w, h)"""
    pixels = [list(BG) for _ in range(width * height)]

    def set_pixel(x, y, c):
        if 0 <= x < width and 0 <= y < height:
            pixels[y * width + x] = list(c)

    def draw_rect(x, y, rw, rh, c, radius=0):
        for py in range(rh):
            for px in range(rw):
                set_pixel(x + px, y + py, c)

    def draw_circle(cx, cy, r, c):
        for py in range(cy - r, cy + r + 1):
            for px in range(cx - r, cx + r + 1):
                if (px - cx) ** 2 + (py - cy) ** 2 <= r ** 2:
                    set_pixel(px, py, c)

    def draw_line(x1, y1, x2, y2, c, thick=1):
        dx, dy = abs(x2 - x1), abs(y2 - y1)
        sx = 1 if x1 < x2 else -1
        sy = 1 if y1 < y2 else -1
        err = dx - dy
        while True:
            for t in range(-thick // 2, thick // 2 + 1):
                set_pixel(x1 + t, y1 + t, c)
            if x1 == x2 and y1 == y2:
                break
            e2 = 2 * err
            if e2 > -dy:
                err -= dy
                x1 += sx
            if e2 < dx:
                err += dx
                y1 += sy

    draw_func(set_pixel, draw_rect, draw_circle, draw_line, width, height)

    def _chunk(ctype, data):
        c = ctype + data
        crc = struct.pack('>I', zlib.crc32(c) & 0xffffffff)
        return struct.pack('>I', len(data)) + c + crc

    header = b'\x89PNG\r\n\x1a\n'
    ihdr = _chunk(b'IHDR', struct.pack('>IIBBBBB', width, height, 8, 2, 0, 0, 0))
    raw = b''
    for py in range(height):
        raw += b'\x00'
        for px in range(width):
            r, g, b = pixels[py * width + px]
            raw += bytes([r, g, b])
    idat = _chunk(b'IDAT', zlib.compress(raw))
    iend = _chunk(b'IEND', b'')
    return header + ihdr + idat + iend


def _draw_icon(set_pixel, rect, circle, line, w, h):
    """Dibuja un icono tipo terminal/SSH"""
    margin = w // 10
    # Rectangulo principal (ventana terminal)
    rect(margin, margin + h // 10, w - margin * 2, h - margin * 2 - h // 5, DARK)
    # Barra de titulo
    rect(margin, margin, w - margin * 2, h // 10, ACCENT)
    # Botones de ventana
    circle(margin + w // 16, margin + h // 20, max(3, w // 40), WHITE)
    circle(margin + w // 8, margin + h // 20, max(3, w // 40), WHITE)
    circle(margin + w // 6 + w // 16, margin + h // 20, max(3, w // 40), WHITE)
    # Lineas de texto
    text_x = margin + w // 15
    text_w = w - margin * 2 - w // 8
    lh = max(4, h // 25)
    line(text_x, margin + h // 4, text_x + text_w - w // 3, margin + h // 4, ACCENT, lh)
    line(text_x, margin + h // 4 + lh * 3, text_x + text_w, margin + h // 4 + lh * 3, WHITE, lh)
    line(text_x, margin + h // 4 + lh * 6, text_x + text_w - w // 5, margin + h // 4 + lh * 6, WHITE, lh)
    # Circulo + (SSH)
    cx, cy = w * 3 // 4, h - margin - h // 6
    cr = max(10, w // 10)
    circle(cx, cy, cr, ACCENT)
    cross = max(3, cr // 3)
    line(cx - cr // 2, cy, cx + cr // 2, cy, WHITE, cross)
    line(cx, cy - cr // 2, cx, cy + cr // 2, WHITE, cross)


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
    response = make_response(_make_png(192, 192, _draw_icon))
    response.headers['Content-Type'] = 'image/png'
    return response


@pwa_bp.route('/pwa-icon-512.png')
def icon_512():
    response = make_response(_make_png(512, 512, _draw_icon))
    response.headers['Content-Type'] = 'image/png'
    return response


@pwa_bp.route('/.well-known/assetlinks.json')
def assetlinks():
    import os
    sha256 = os.environ.get('PWA_SHA256', '')
    package = os.environ.get('PWA_PACKAGE_NAME', 'com.luxvpn.panel')
    result = [{
        "relation": ["delegate_permission/common.handle_all_urls"],
        "target": {
            "namespace": "android_app",
            "package_name": package,
            "sha256_cert_fingerprints": [sha256]
        }
    }]
    response = make_response(result)
    response.headers['Content-Type'] = 'application/json'
    return response
