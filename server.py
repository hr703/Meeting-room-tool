#!/usr/bin/env python3
import json, os, urllib.request
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse

DATA_FILE    = os.path.join(os.path.dirname(__file__), 'data.json')
DATABASE_URL = os.environ.get('DATABASE_URL')

# ── EMAIL CONFIG (Brevo HTTP API) ────────────────────────────────
BREVO_API_KEY = os.environ.get('BREVO_API_KEY', '')
EMAIL_FROM    = 'hr@cuemath.com'
# ─────────────────────────────────────────────────────────────────

# ── STORAGE (PostgreSQL on cloud, JSON file locally) ─────────────
_db_ok = False
if DATABASE_URL:
    try:
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
        _db_ok = True
        print('[DB] PostgreSQL connected successfully')
    except Exception as e:
        print(f'[DB] Connection failed, falling back to file storage: {e}')
        _db_ok = False

if _db_ok:
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

if not _db_ok:
    def load_data():
        if os.path.exists(DATA_FILE):
            with open(DATA_FILE, 'r') as f:
                return json.load(f)
        return {'rooms':[],'bookings':[],'notifications':[]}

    def save_data(data):
        with open(DATA_FILE, 'w') as f:
            json.dump(data, f, indent=2)
# ─────────────────────────────────────────────────────────────────

def _time_to_mins(t):
    try:
        h, m = t.split(':')
        return int(h) * 60 + int(m)
    except Exception:
        return None

BLOCKING_STATUSES = ('Pending', 'Approved')

def find_new_conflict(old_bookings, new_bookings):
    """Return a conflict message if any new/edited booking newly overlaps
    another active (Pending/Approved) booking for the same room+date.
    Pre-existing conflicts already in old_bookings are left untouched."""
    old_by_id = {b.get('id'): b for b in old_bookings}
    new_by_id = {b.get('id'): b for b in new_bookings}

    def touched(b):
        old = old_by_id.get(b.get('id'))
        if old is None:
            return True
        return (old.get('roomId') != b.get('roomId') or old.get('date') != b.get('date') or
                old.get('start') != b.get('start') or old.get('end') != b.get('end') or
                old.get('status') != b.get('status'))

    for b in new_bookings:
        if b.get('status') not in BLOCKING_STATUSES:
            continue
        if not touched(b):
            continue
        bs, be = _time_to_mins(b.get('start', '')), _time_to_mins(b.get('end', ''))
        if bs is None or be is None:
            continue
        for other in new_bookings:
            if other.get('id') == b.get('id'):
                continue
            if other.get('status') not in BLOCKING_STATUSES:
                continue
            if other.get('roomId') != b.get('roomId') or other.get('date') != b.get('date'):
                continue
            os_, oe = _time_to_mins(other.get('start', '')), _time_to_mins(other.get('end', ''))
            if os_ is None or oe is None:
                continue
            if bs < oe and be > os_:
                return (f"Booking conflict: room already has an active booking "
                        f"({other.get('organizer','')} — {other.get('start')}-{other.get('end')} "
                        f"on {other.get('date')}) for this room/date/time.")
    return None

def send_email_async(to_email, subject, body):
    try:
        payload = json.dumps({
            'sender':      {'name': 'Meeting Room Booking', 'email': EMAIL_FROM},
            'to':          [{'email': to_email}],
            'subject':     subject,
            'textContent': body
        }).encode()
        req = urllib.request.Request(
            'https://api.brevo.com/v3/smtp/email',
            data=payload,
            headers={'api-key': BREVO_API_KEY, 'Content-Type': 'application/json'}
        )
        urllib.request.urlopen(req, timeout=15)
        print(f'[EMAIL] Sent to {to_email} | {subject}')
        return {'ok': True, 'msg': 'Email sent'}
    except Exception as e:
        print(f'[EMAIL ERROR] {e}')
        return {'ok': False, 'msg': str(e)}


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
            incoming = json.loads(body_bytes)
            current  = load_data()
            conflict = find_new_conflict(current.get('bookings', []), incoming.get('bookings', []))
            if conflict:
                self.send_response(409)
                self.send_header('Content-Type', 'application/json')
                self.send_cors(); self.end_headers()
                self.wfile.write(json.dumps({'ok': False, 'error': conflict}).encode())
                return
            save_data(incoming)
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
                result = send_email_async(to, subject, body)
                resp = json.dumps(result).encode()
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
