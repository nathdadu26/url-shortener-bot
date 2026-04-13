from http.server import HTTPServer, BaseHTTPRequestHandler
import threading
import os
import time
import urllib.request

PORT = int(os.getenv("PORT", 8000))
KOYEB_URL = os.getenv("KOYEB_URL", "")  # e.g. https://your-app-name.koyeb.app


class HealthHandler(BaseHTTPRequestHandler):

    def do_GET(self):
        if self.path == "/health":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"status":"ok"}')
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, format, *args):
        # Suppress default request logs
        pass


def self_ping():
    """
    Apne aap ko har 5 minute mein ping karo taaki
    Koyeb free tier service sleep na ho.
    KOYEB_URL environment variable set honi chahiye.
    """
    if not KOYEB_URL:
        print("[Self-Ping] KOYEB_URL not set, self-ping disabled.")
        return

    ping_url = f"{KOYEB_URL.rstrip('/')}/health"
    print(f"[Self-Ping] Will ping {ping_url} every 5 minutes.")

    while True:
        time.sleep(5 * 60)  # 5 minute wait
        try:
            with urllib.request.urlopen(ping_url, timeout=10) as response:
                print(f"[Self-Ping] OK - Status: {response.status}")
        except Exception as e:
            print(f"[Self-Ping] Failed: {e}")


def start_health_server():
    server = HTTPServer(("0.0.0.0", PORT), HealthHandler)

    # Health server thread
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()
    print(f"Health check server running on port {PORT}")

    # Self-ping thread (Koyeb sleep rokne ke liye)
    ping_thread = threading.Thread(target=self_ping, daemon=True)
    ping_thread.start()
