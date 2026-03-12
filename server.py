#!/usr/bin/env python3
import json, os, smtplib, threading
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

DATA_FILE    = os.path.join(os.path.dirname(__file__), 'data.json')
DATABASE_URL = os.environ.get('DATABASE_URL')

# ── EMAIL CONFIG ─────────────────────────────────────────────────
EMAIL_HOST = 'smtp.gmail.com'
EMAIL_PORT = 587
EMAIL_USER = 'hr@cuemath.com'
EMAIL_PASS = 'xqtbmqsticepunlw'
EMAIL_FROM = 'Meeting Room Booking <hr@cuemath.com>'
# ─────────────────────────────────────────────────────────────────

# ── STORAGE (PostgreSQL on cloud, JSON file locally) ─────────────
if DATABASE_URL:
    import psycopg2

    def _conn():
        return psycopg2.connect(DATABASE_URL, sslmode='require')

    def _init():
        with _conn() as c:
            with c.cursor() as cur:
                cur.execute('''CREATE TABLE IF NOT EXISTS meeting_appdata
                               (id INT PRIMARY KEY, data TEXT NOT NULL)''')
                cur.execute('''INSERT INTO meeting_appdata (id, data)
                               VALUES (1, %s) ON CONFLICT (id) DO NOTHING''',
                            [json.dumps({'rooms':[],'bookings':[],'notifications':[]})])
                c.commit()
    _init()

    def load_data():
        try:
            with _conn() as c:
                with c.cursor() as cur:
                    cur.execute('SELECT data FROM meeting_appdata WHERE id=1')
                    row = cur.fetchone()
                    return json.loads(row[0]) if row else {'rooms':[],'bookings':[],'notifications':[]}
        except Exception as e:
            print(f'[DB READ ERROR] {e}')
            return {'rooms':[],'bookings':[],'notifications':[]}

    def save_data(data):
        try:
            with _conn() as c:
                with c.cursor() as cur:
                    cur.execute('UPDATE meeting_appdata SET data=%s WHERE id=1', [json.dumps(data)])
                    c.commit()
        except Exception as e:
            print(f'[DB WRITE ERROR] {e}')

else:
    def load_data():
        if os.path.exists(DATA_FILE):
            with open(DATA_FILE, 'r') as f:
                return json.load(f)
        return {'rooms':[],'bookings':[],'notifications':[]}

    def save_data(data):
        with open(DATA_FILE, 'w') as f:
            json.dump(data, f, indent=2)
# ─────────────────────────────────────────────────────────────────

def send_email_async(to_email, subject, body):
    def _send():
        try:
            msg = MIMEMultipart()
            msg['From']    = EMAIL_FROM
            msg['To']      = to_email
            msg['Subject'] = subject
            msg.attach(MIMEText(body, 'plain'))
            with smtplib.SMTP(EMAIL_HOST, EMAIL_PORT, timeout=15) as s:
                s.ehlo(); s.starttls()
                s.login(EMAIL_USER, EMAIL_PASS)
                s.send_message(msg)
            print(f'[EMAIL] Sent to {to_email} | {subject}')
        except Exception as e:
            print(f'[EMAIL ERROR] {e}')
    threading.Thread(target=_send, daemon=True).start()


class Handler(BaseHTTPRequestHandler):
    def log_message(self, format, *args): pass

    def send_cors(self):
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type')

    def do_OPTIONS(self):
        self.send_response(200); self.send_cors(); self.end_headers()

    def do_GET(self):
        path = urlparse(self.path).path
        if path == '/api/data':
            body = json.dumps(load_data()).encode()
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_cors(); self.end_headers()
            self.wfile.write(body)
        elif path in ('/', '/index.html'):
            with open(os.path.join(os.path.dirname(__file__), 'index.html'), 'rb') as f:
                content = f.read()
            self.send_response(200)
            self.send_header('Content-Type', 'text/html')
            self.send_cors(); self.end_headers()
            self.wfile.write(content)
        else:
            self.send_response(404); self.end_headers()

    def do_POST(self):
        path   = urlparse(self.path).path
        length = int(self.headers.get('Content-Length', 0))
        body_bytes = self.rfile.read(length)

        if path == '/api/data':
            save_data(json.loads(body_bytes))
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_cors(); self.end_headers()
            self.wfile.write(b'{"ok":true}')

        elif path == '/api/send-email':
            req     = json.loads(body_bytes)
            to      = req.get('to','')
            subject = req.get('subject','')
            body    = req.get('body','')
            if to and subject and body:
                send_email_async(to, subject, body)
                resp = b'{"ok":true,"msg":"Email queued"}'
            else:
                resp = b'{"ok":false,"msg":"Missing fields"}'
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_cors(); self.end_headers()
            self.wfile.write(resp)
        else:
            self.send_response(404); self.end_headers()


if __name__ == '__main__':
    port = int(os.environ.get('PORT', 8081))
    print(f'Meeting Room Server running at http://localhost:{port}')
    HTTPServer(('0.0.0.0', port), Handler).serve_forever()
