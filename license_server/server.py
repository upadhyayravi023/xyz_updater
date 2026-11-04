import base64
import hashlib
import hmac
import json
import os
import secrets
import time
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.request import Request, urlopen

from bson import ObjectId
from pymongo import MongoClient, ReturnDocument

ADMIN_USER = os.environ.get("ADMIN_USER", "")
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "")
PEPPER = os.environ.get("LICENSE_PEPPER", "")
MONGODB_URI = os.environ.get("MONGODB_URI", "")
DB_NAME = os.environ.get("MONGODB_DB", "opentxt_license")
OPENAI_KEY = os.environ.get("OPENAI_API_KEY", "")
GEMINI_KEY = os.environ.get("GEMINI_API_KEY", "")
LEASE_SECONDS = 300
mongo = None
licenses = None


def digest(value):
    return hmac.new(PEPPER.encode(), value.encode(), hashlib.sha256).hexdigest()


def utc(ts):
    return datetime.fromtimestamp(ts, timezone.utc).isoformat()


def clean(s, limit):
    return str(s or "").strip()[:limit]


class Handler(BaseHTTPRequestHandler):
    server_version = "OpenTxtLicense"

    def log_message(self, fmt, *args):
        print("%s - %s" % (self.client_address[0], fmt % args))

    def send_json(self, code, obj):
        raw = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(raw)

    def body(self):
        n = int(self.headers.get("Content-Length", "0"))
        if n > 2_500_000:
            raise ValueError("Request too large")
        return json.loads(self.rfile.read(n) or b"{}")

    def admin(self):
        try:
            scheme, encoded = self.headers.get("Authorization", "").split(" ", 1)
            if scheme.lower() != "basic":
                return False
            user, pw = base64.b64decode(encoded, validate=True).decode().split(":", 1)
        except Exception:
            return False
        return hmac.compare_digest(user, ADMIN_USER) and hmac.compare_digest(pw, ADMIN_PASSWORD)

    def lease(self):
        token = self.headers.get("Authorization", "").removeprefix("Bearer ").strip()
        if not token:
            return None
        now = int(time.time())
        row = licenses.find_one({"lease_hash": digest(token), "revoked": False, "expires": {"$gt": now}, "lease_until": {"$gt": now}})
        return row

    def do_GET(self):
        if self.path == "/health":
            return self.send_json(200, {"ok": True})
        if self.path == "/admin":
            return self.admin_page()
        if self.path == "/api/admin/licenses":
            if not self.admin():
                return self.send_json(401, {"error": "Admin login required"})
            rows = licenses.find({}, {"key_hash": 0, "lease_hash": 0}).sort("created", -1).limit(500)
            result = []
            for row in rows:
                result.append({"id": str(row["_id"]), "label": row.get("label", ""), "created": row["created"], "activated": row.get("activated"), "expires": row.get("expires"), "assigned": bool(row.get("device")), "revoked": row.get("revoked", False)})
            return self.send_json(200, result)
        self.send_json(404, {"error": "Not found"})

    def do_POST(self):
        try:
            data = self.body()
            if self.path.startswith("/api/admin/"):
                if not self.admin():
                    return self.send_json(401, {"error": "Admin login required"})
                return self.admin_post(data)
            if self.path == "/api/activate":
                return self.activate(data)
            row = self.lease()
            if not row:
                return self.send_json(403, {"error": "License invalid, expired, or inactive. Activate again or contact the administrator."})
            device = clean(data.get("device"), 128)
            if not hmac.compare_digest(row.get("device", ""), device):
                return self.send_json(403, {"error": "License is assigned to another computer."})
            if self.path == "/api/heartbeat":
                until = min(int(time.time()) + LEASE_SECONDS, row["expires"])
                licenses.update_one({"_id": row["_id"], "lease_hash": row["lease_hash"]}, {"$set": {"lease_until": until}})
                return self.send_json(200, {"expires_at": utc(row["expires"]), "lease_until": until})
            if self.path == "/api/release":
                licenses.update_one({"_id": row["_id"]}, {"$unset": {"device": "", "lease_hash": "", "lease_until": ""}})
                return self.send_json(200, {"ok": True})
            if self.path == "/api/ask":
                return self.ask(data)
            self.send_json(404, {"error": "Not found"})
        except (ValueError, KeyError, TypeError) as e:
            self.send_json(400, {"error": str(e)[:200] or "Invalid request"})
        except Exception as e:
            print("request error:", type(e).__name__)
            self.send_json(500, {"error": "Server error"})

    def activate(self, data):
        key = clean(data.get("key"), 256)
        device = clean(data.get("device"), 128)
        if not key or len(device) != 64:
            return self.send_json(400, {"error": "Enter a valid license key and restart the app."})
        now = int(time.time())
        query = {"key_hash": digest(key), "revoked": False, "$or": [{"expires": None}, {"expires": {"$gt": now}}]}
        current = licenses.find_one(query)
        if not current:
            return self.send_json(403, {"error": "License key is invalid, expired, or revoked."})
        if current.get("device") and current["device"] != device and current.get("lease_until", 0) > now:
            return self.send_json(409, {"error": "This license is active on another computer. Contact the administrator to release it."})
        expires = current.get("expires") or now + current["duration"]
        token = secrets.token_urlsafe(32)
        until = min(now + LEASE_SECONDS, expires)
        can_claim = {"_id": current["_id"], "revoked": False, "$or": [{"device": device}, {"device": {"$exists": False}}, {"lease_until": {"$lte": now}}, {"lease_until": {"$exists": False}}]}
        updated = licenses.find_one_and_update(can_claim, {"$set": {"activated": current.get("activated", now), "expires": expires, "device": device, "lease_hash": digest(token), "lease_until": until}}, return_document=ReturnDocument.AFTER)
        if not updated:
            return self.send_json(409, {"error": "This license was just activated on another computer. Contact the administrator."})
        return self.send_json(200, {"lease": token, "expires_at": utc(expires), "lease_until": until})

    def admin_post(self, data):
        if self.path == "/api/admin/create":
            label = clean(data.get("label"), 100) or "License"
            days = int(data.get("days", 0))
            if not 1 <= days <= 3650:
                return self.send_json(400, {"error": "Duration must be from 1 to 3650 days."})
            key = "OTX-" + secrets.token_hex(4).upper() + "-" + secrets.token_hex(4).upper() + "-" + secrets.token_hex(4).upper()
            licenses.insert_one({"key_hash": digest(key), "label": label, "created": int(time.time()), "duration": days * 86400, "revoked": False})
            return self.send_json(201, {"key": key, "label": label, "days": days})
        if self.path in ("/api/admin/revoke", "/api/admin/release"):
            try:
                ident = ObjectId(clean(data.get("id"), 24))
            except Exception:
                return self.send_json(400, {"error": "Invalid license id"})
            if self.path.endswith("/revoke"):
                result = licenses.update_one({"_id": ident}, {"$set": {"revoked": True}, "$unset": {"device": "", "lease_hash": "", "lease_until": ""}})
            else:
                result = licenses.update_one({"_id": ident}, {"$unset": {"device": "", "lease_hash": "", "lease_until": ""}})
            return self.send_json(200 if result.matched_count else 404, {"ok": bool(result.matched_count)})
        self.send_json(404, {"error": "Not found"})

    def ask(self, data):
        provider = clean(data.get("provider"), 16).lower()
        model = clean(data.get("model"), 80)
        prompt = clean(data.get("prompt"), 100000)
        image = data.get("image")
        allowed = {"openai": {"gpt-4o-mini", "gpt-4o"}, "gemini": {"gemini-3.8-flash"}}
        if provider not in allowed or model not in allowed[provider]:
            return self.send_json(400, {"error": "Unsupported provider or model."})
        key = OPENAI_KEY if provider == "openai" else GEMINI_KEY
        if not key:
            return self.send_json(503, {"error": provider.title() + " API is not configured on the service."})
        content = [{"type": "text", "text": prompt or "Please describe the attached image."}]
        if image:
            if len(image) > 2_000_000:
                return self.send_json(413, {"error": "Image is too large. Attach a smaller image."})
            content.append({"type": "image_url", "image_url": {"url": "data:image/jpeg;base64," + image}})
        url = "https://api.openai.com/v1/chat/completions" if provider == "openai" else "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions"
        req = Request(url, data=json.dumps({"model": model, "messages": [{"role": "user", "content": content}]}).encode(), headers={"Authorization": "Bearer " + key, "Content-Type": "application/json"})
        try:
            with urlopen(req, timeout=75) as response:
                result = json.loads(response.read())
            return self.send_json(200, {"answer": result["choices"][0]["message"]["content"]})
        except Exception as e:
            print("provider error:", type(e).__name__)
            return self.send_json(502, {"error": "AI provider request failed. Check the service configuration and try again."})

    def admin_page(self):
        html = '''<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width"><title>License Admin</title><style>body{font:15px system-ui;max-width:1000px;margin:40px auto;padding:0 16px;color:#182230}input,button{padding:9px;margin:4px}button{cursor:pointer}table{border-collapse:collapse;width:100%;margin-top:20px}td,th{border-bottom:1px solid #ddd;padding:9px;text-align:left}.key{background:#f4f6f8;padding:12px;word-break:break-all}</style><h1>License administration</h1><p>Create time-limited keys. Keys appear once; copy them securely.</p><form id="create"><input name="label" placeholder="Customer or note"><input name="days" type="number" min="1" max="3650" value="30"><button>Create license</button></form><div id="new"></div><p id="msg"></p><table><thead><tr><th>Label</th><th>Created</th><th>Expires</th><th>Device</th><th>Actions</th></tr></thead><tbody id="rows"></tbody></table><script>const auth='Basic '+btoa(prompt('Admin username')+':'+prompt('Admin password'));async function api(p,d){let r=await fetch(p,{method:d?'POST':'GET',headers:{Authorization:auth,'Content-Type':'application/json'},body:d?JSON.stringify(d):undefined});let x=await r.json();if(!r.ok)throw Error(x.error||'Request failed');return x}async function load(){try{let a=await api('/api/admin/licenses');rows.innerHTML=a.map(x=>`<tr><td>${esc(x.label)}</td><td>${new Date(x.created*1000).toLocaleString()}</td><td>${x.expires?new Date(x.expires*1000).toLocaleString():'Starts at activation'}</td><td>${x.assigned?'Assigned':'Free'}</td><td>${x.revoked?'Revoked':`<button onclick="act('release','${x.id}')">Release</button><button onclick="act('revoke','${x.id}')">Revoke</button>`}</td></tr>`).join('')}catch(e){msg.textContent=e.message}}function esc(s){return s.replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]))}async function act(a,id){try{await api('/api/admin/'+a,{id});load()}catch(e){alert(e.message)}}create.onsubmit=async e=>{e.preventDefault();try{let x=await api('/api/admin/create',{label:create.label.value,days:create.days.value});document.querySelector('#new').innerHTML='<p class="key">New key (shown once): <b>'+esc(x.key)+'</b></p>';load()}catch(e){msg.textContent=e.message}};load()</script>'''
        raw = html.encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(raw)


def main():
    global mongo, licenses
    if not (ADMIN_USER and ADMIN_PASSWORD and len(PEPPER) >= 32 and MONGODB_URI):
        raise SystemExit("Set MONGODB_URI, ADMIN_USER, ADMIN_PASSWORD, and LICENSE_PEPPER (32+ random characters).")
    mongo = MongoClient(MONGODB_URI, serverSelectionTimeoutMS=10000, tz_aware=True)
    mongo.admin.command("ping")
    licenses = mongo[DB_NAME]["licenses"]
    licenses.create_index("key_hash", unique=True)
    licenses.create_index("lease_hash", sparse=True)
    if not (OPENAI_KEY or GEMINI_KEY):
        print("Warning: no AI provider keys configured; /api/ask is disabled.")
    host = os.environ.get("HOST", "127.0.0.1")
    port = int(os.environ.get("PORT", "8765"))
    print(f"Listening on http://{host}:{port} (admin: /admin)")
    try:
        ThreadingHTTPServer((host, port), Handler).serve_forever()
    finally:
        mongo.close()


if __name__ == "__main__":
    main()



