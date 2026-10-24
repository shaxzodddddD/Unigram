# main.py — Minimal, 100% ishlaydigan Flask backend
import os
import sqlite3
import time
import secrets
from functools import wraps
from flask import Flask, request, jsonify, g

try:
    from werkzeug.security import generate_password_hash, check_password_hash
except ImportError:
    pass

# Config
PORT = int(os.environ.get("PORT", 5566))
DB_FILE = os.environ.get("DB_FILE", "unigram.db")
ADMIN_SECRET = os.environ.get("ADMIN_SECRET", "admin123")

print("=" * 60)
print(f"🚀 UNIGRAM ishga tushirilmoqda")
print(f"   Port: {PORT}")
print(f"   DB: {DB_FILE}")
print(f"   Python: {os.sys.version}")
print("=" * 60)

# App
app = Flask(__name__, static_folder=".", static_url_path="")
app.config["MAX_CONTENT_LENGTH"] = 12 * 1024 * 1024
app.config["JSON_AS_ASCII"] = False

# CORS (ixtiyoriy)
try:
    from flask_cors import CORS
    CORS(app, origins="*")
    print("✅ CORS enabled")
except ImportError:
    print("⚠️ flask-cors yo'q")


# DB helpers
def get_db():
    conn = sqlite3.connect(DB_FILE, timeout=30, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def now_ms():
    return int(time.time() * 1000)


# DB init
def init_db():
    conn = get_db()
    try:
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            avatar TEXT DEFAULT '⭐',
            bio TEXT DEFAULT '',
            verified INTEGER DEFAULT 0,
            created_at INTEGER NOT NULL
        );
        CREATE TABLE IF NOT EXISTS api_tokens (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            token TEXT UNIQUE NOT NULL,
            created_at INTEGER NOT NULL
        );
        CREATE TABLE IF NOT EXISTS posts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            text TEXT DEFAULT '',
            image_url TEXT,
            tags TEXT DEFAULT '',
            created_at INTEGER NOT NULL,
            deleted_at INTEGER
        );
        CREATE TABLE IF NOT EXISTS comments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            post_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            text TEXT NOT NULL,
            created_at INTEGER NOT NULL
        );
        CREATE TABLE IF NOT EXISTS reactions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            post_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            emoji TEXT NOT NULL,
            created_at INTEGER NOT NULL,
            UNIQUE(post_id, user_id)
        );
        CREATE TABLE IF NOT EXISTS follows (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            follower_id INTEGER NOT NULL,
            following_id INTEGER NOT NULL,
            created_at INTEGER NOT NULL,
            UNIQUE(follower_id, following_id)
        );
        """)
        conn.commit()

        # Seed demo data
        if conn.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 0:
            t = now_ms()
            pwd = generate_password_hash("demo123")
            users = [
                ("Dilnoza Karimova", "🌻", "UI/UX dizayner"),
                ("Javlon Toshmatov", "🚀", "Startap asoschisi"),
                ("Malika Yusupova", "🦋", "Fotograf"),
                ("Bekzod Rahimov", "🐺", "Dasturchi"),
            ]
            for name, avatar, bio in users:
                conn.execute(
                    "INSERT INTO users(name,password_hash,avatar,bio,created_at) VALUES(?,?,?,?,?)",
                    (name, pwd, avatar, bio, t)
                )

            posts = [
                (1, "Unigram'ga xush kelibsiz! 🎉", "unigram"),
                (2, "Yangi dizayn loyihasi 🎨", "dizayn"),
                (3, "3 oyda 10K foydalanuvchi 🚀", "startap"),
            ]
            for uid, text, tags in posts:
                conn.execute(
                    "INSERT INTO posts(user_id,text,tags,created_at) VALUES(?,?,?,?)",
                    (uid, text, tags, t)
                )

            conn.commit()
            print(f"✅ Demo data: 4 users, 3 posts")
        else:
            print(f"ℹ️ Baza tayyor")

    finally:
        conn.close()


init_db()


# Auth
def auth_required(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        hdr = request.headers.get("Authorization", "")
        token = hdr.replace("Bearer ", "").strip()
        if not token:
            return jsonify({"error": "Token kerak"}), 401

        conn = get_db()
        try:
            row = conn.execute(
                "SELECT u.* FROM users u JOIN api_tokens t ON t.user_id = u.id WHERE t.token = ?",
                (token,)
            ).fetchone()
        finally:
            conn.close()

        if not row:
            return jsonify({"error": "Token yaroqsiz"}), 401

        g.user = dict(row)
        g.token = token
        return fn(*args, **kwargs)
    return wrapper


# Health
@app.route("/health")
@app.route("/api/health")
def health():
    try:
        conn = get_db()
        conn.execute("SELECT 1").fetchone()
        conn.close()
        return jsonify({"ok": True, "status": "healthy", "time": now_ms()}), 200
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/version")
def version():
    return jsonify({"name": "Unigram", "version": "1.0"})


# Auth
@app.route("/api/auth/register", methods=["POST"])
def register():
    data = request.get_json(silent=True) or {}
    name = (data.get("name") or "").strip()
    password = data.get("password", "")
    avatar = (data.get("avatar") or "⭐")[:4]

    if len(name) < 2:
        return jsonify({"error": "Ism kamida 2 belgi"}), 400
    if len(password) < 4:
        return jsonify({"error": "Parol kamida 4 belgi"}), 400

    conn = get_db()
    try:
        existing = conn.execute("SELECT 1 FROM users WHERE name=?", (name,)).fetchone()
        if existing:
            return jsonify({"error": "Bu ism band"}), 409

        conn.execute(
            "INSERT INTO users(name,password_hash,avatar,created_at) VALUES(?,?,?,?)",
            (name, generate_password_hash(password), avatar, now_ms())
        )
        uid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]

        token = secrets.token_urlsafe(48)
        conn.execute(
            "INSERT INTO api_tokens(user_id,token,created_at) VALUES(?,?,?)",
            (uid, token, now_ms())
        )
        conn.commit()
    finally:
        conn.close()

    print(f"✅ Yangi user: {name} (ID: {uid})")

    return jsonify({
        "token": token,
        "user": {"id": uid, "name": name, "avatar": avatar}
    }), 201


@app.route("/api/auth/login", methods=["POST"])
def login():
    data = request.get_json(silent=True) or {}
    name = (data.get("name") or "").strip()
    password = data.get("password", "")

    if not name or not password:
        return jsonify({"error": "Ism va parol kerak"}), 400

    conn = get_db()
    try:
        user = conn.execute("SELECT * FROM users WHERE name=?", (name,)).fetchone()
    finally:
        conn.close()

    if not user or not check_password_hash(user["password_hash"], password):
        return jsonify({"error": "Ism yoki parol xato"}), 401

    token = secrets.token_urlsafe(48)
    conn = get_db()
    try:
        conn.execute(
            "INSERT INTO api_tokens(user_id,token,created_at) VALUES(?,?,?)",
            (user["id"], token, now_ms())
        )
        conn.commit()
    finally:
        conn.close()

    return jsonify({
        "token": token,
        "user": {
            "id": user["id"],
            "name": user["name"],
            "avatar": user["avatar"],
            "verified": bool(user["verified"])
        }
    })


@app.route("/api/me")
@auth_required
def me():
    return jsonify({
        "id": g.user["id"],
        "name": g.user["name"],
        "avatar": g.user["avatar"],
        "verified": bool(g.user["verified"])
    })


# Posts
@app.route("/api/posts")
def list_posts():
    # Optional auth
    me_id = None
    hdr = request.headers.get("Authorization", "")
    if hdr:
        token = hdr.replace("Bearer ", "").strip()
        conn = get_db()
        try:
            row = conn.execute(
                "SELECT u.* FROM users u JOIN api_tokens t ON t.user_id = u.id WHERE t.token = ?",
                (token,)
            ).fetchone()
            if row:
                me_id = row["id"]
        finally:
            conn.close()

    conn = get_db()
    try:
        rows = conn.execute(
            "SELECT * FROM posts WHERE deleted_at IS NULL ORDER BY created_at DESC LIMIT 100"
        ).fetchall()

        result = []
        for r in rows:
            author = conn.execute(
                "SELECT name, avatar FROM users WHERE id=?", (r["user_id"],)
            ).fetchone()

            likes = conn.execute(
                "SELECT COUNT(*) FROM reactions WHERE post_id=?", (r["id"],)
            ).fetchone()[0]

            comments = conn.execute(
                "SELECT COUNT(*) FROM comments WHERE post_id=?", (r["id"],)
            ).fetchone()[0]

            my_reaction = None
            saved = False
            if me_id:
                r_row = conn.execute(
                    "SELECT emoji FROM reactions WHERE post_id=? AND user_id=?",
                    (r["id"], me_id)
                ).fetchone()
                my_reaction = r_row["emoji"] if r_row else None

            result.append({
                "id": r["id"],
                "authorId": r["user_id"],
                "authorName": author["name"] if author else "Deleted",
                "avatar": author["avatar"] if author else "⭐",
                "text": r["text"],
                "image": r["image_url"],
                "tags": r["tags"].split(",") if r["tags"] else [],
                "at": r["created_at"],
                "likes": likes,
                "liked": my_reaction is not None,
                "myReaction": my_reaction,
                "commentCount": comments,
                "saved": saved,
            })
    finally:
        conn.close()

    return jsonify(result)


@app.route("/api/posts", methods=["POST"])
@auth_required
def create_post():
    data = request.get_json(silent=True) or {}
    text = (data.get("text") or "").strip()

    if not text:
        return jsonify({"error": "Bo'sh post"}), 400
    if len(text) > 5000:
        return jsonify({"error": "Post juda uzun"}), 400

    import re
    tags = ",".join(re.findall(r"#(\w+)", text))[:300]

    conn = get_db()
    try:
        conn.execute(
            "INSERT INTO posts(user_id,text,tags,created_at) VALUES(?,?,?,?)",
            (g.user["id"], text, tags, now_ms())
        )
        pid = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
        conn.commit()

        row = conn.execute("SELECT * FROM posts WHERE id=?", (pid,)).fetchone()
        author = conn.execute(
            "SELECT name, avatar FROM users WHERE id=?", (g.user["id"],)
        ).fetchone()
    finally:
        conn.close()

    return jsonify({
        "id": row["id"],
        "authorId": g.user["id"],
        "authorName": author["name"],
        "avatar": author["avatar"],
        "text": row["text"],
        "tags": row["tags"].split(",") if row["tags"] else [],
        "at": row["created_at"],
        "likes": 0,
        "liked": False,
        "commentCount": 0,
        "saved": False,
    }), 201


@app.route("/api/posts/<int:pid>/react", methods=["POST"])
@auth_required
def react(pid):
    data = request.get_json(silent=True) or {}
    emoji = (data.get("emoji") or "❤️")[:8]

    conn = get_db()
    try:
        existing = conn.execute(
            "SELECT emoji FROM reactions WHERE post_id=? AND user_id=?",
            (pid, g.user["id"])
        ).fetchone()

        if existing and existing["emoji"] == emoji:
            conn.execute(
                "DELETE FROM reactions WHERE post_id=? AND user_id=?",
                (pid, g.user["id"])
            )
            my = None
        else:
            conn.execute(
                "INSERT INTO reactions(post_id,user_id,emoji,created_at) VALUES(?,?,?,?) "
                "ON CONFLICT(post_id,user_id) DO UPDATE SET emoji=excluded.emoji",
                (pid, g.user["id"], emoji, now_ms())
            )
            my = emoji

        cnt = conn.execute(
            "SELECT COUNT(*) FROM reactions WHERE post_id=?", (pid,)
        ).fetchone()[0]
        conn.commit()
    finally:
        conn.close()

    return jsonify({"liked": my is not None, "myReaction": my, "count": cnt})


@app.route("/api/people")
def people():
    conn = get_db()
    try:
        rows = conn.execute(
            "SELECT id, name, avatar, bio, verified FROM users ORDER BY created_at DESC LIMIT 100"
        ).fetchall()
    finally:
        conn.close()

    return jsonify([
        {
            "id": r["id"],
            "name": r["name"],
            "avatar": r["avatar"],
            "bio": r["bio"],
            "verified": bool(r["verified"])
        }
        for r in rows
    ])


@app.route("/api/admin/stats")
def admin_stats():
    if request.args.get("key") != ADMIN_SECRET:
        return jsonify({"error": "Admin kalit kerak"}), 403

    conn = get_db()
    try:
        stats = {
            "users": conn.execute("SELECT COUNT(*) FROM users").fetchone()[0],
            "posts": conn.execute("SELECT COUNT(*) FROM posts WHERE deleted_at IS NULL").fetchone()[0],
            "comments": conn.execute("SELECT COUNT(*) FROM comments").fetchone()[0],
            "reactions": conn.execute("SELECT COUNT(*) FROM reactions").fetchone()[0],
        }
    finally:
        conn.close()

    return jsonify(stats)


# Static
@app.route("/")
@app.route("/index.html")
def index():
    idx = os.path.join(os.path.dirname(os.path.abspath(__file__)), "index.html")
    if os.path.exists(idx):
        from flask import send_from_directory
        return send_from_directory(os.path.dirname(idx), "index.html")
    return jsonify({
        "name": "Unigram",
        "status": "running",
        "endpoints": [
            "GET  /health",
            "GET  /api/version",
            "POST /api/auth/register",
            "POST /api/auth/login",
            "GET  /api/posts",
            "POST /api/posts",
            "GET  /api/people",
            "GET  /api/admin/stats?key=admin123",
        ]
    })


# Error handlers
@app.errorhandler(404)
def error_404(e):
    if request.path.startswith("/api/"):
        return jsonify({"error": "Topilmadi"}), 404
    return jsonify({"error": "Not found"}), 404


@app.errorhandler(500)
def error_500(e):
    print(f"❌ 500 xato: {e}")
    return jsonify({"error": "Server xatosi"}), 500


print("✅ Barcha endpointlar tayyor")
print("=" * 60)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=PORT, debug=False, threaded=True)
