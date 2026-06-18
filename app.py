"""
Arranque para Koyeb (u otro host de contenedores).
- Levanta un servidor HTTP mínimo en $PORT (Koyeb hace health-check ahí).
- Corre el bot de Telegram en POLLING (Koyeb sí alcanza api.telegram.org).
"""
import os
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import bot as botmod

PORT = int(os.environ.get("PORT", "8000"))


class Health(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.end_headers()
        self.wfile.write("Calcpad bot OK - traductor + moderador activo".encode("utf-8"))

    def log_message(self, *args):
        pass  # silenciar logs HTTP


def serve_health():
    HTTPServer(("0.0.0.0", PORT), Health).serve_forever()


if __name__ == "__main__":
    # Koyeb hace health-check en $PORT -> servidor de salud en hilo de fondo.
    threading.Thread(target=serve_health, daemon=True).start()
    # El bot corre en POLLING (no hay SPACE_HOST fuera de HF -> bot.py usa run_polling).
    botmod.main()
