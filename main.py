# ============================================================================
# UNIGRAM — Render uchun ishlaydigan minimal backend
# Faqat Flask + SQLite. Gunicorn bilan ishlaydi.
# ============================================================================
import os
import sqlite3
import time
import secrets
import base64
import re
from functools import wraps
from flask import Flask, request, jsonify, send_from_directory, g

try:
    from werkzeug.security import generate_password_hash, check_password_hash
except ImportError:
    raise RuntimeError("werkzeug o'rnatilmagan. pip install werkzeug")

try:
    from flask_cors import CORS
    HAS_CORS = True
except ImportError:
    HAS_CORS = False
    CORS = None

# ============================================================
# CONFIG
# ============================================================
PORT = int(os.environ.get("PORT", 5566))
DATA_DIR = os.environ.get("DATA_DIR", ".")
DB_FILE = os.path.join(DATA_DIR, "unigram.db")
UPLOAD_DIR = os.path.join(DATA_DIR, "uploads")
ADMIN_SECRET = os.environ.get("ADMIN_SECRET", "admin123")
IS_RENDER = os.environ.get("RENDER") == "true"

os.makedirs(UPLOAD_DIR, exist_ok=True)

app = Flask(__name__, static_folder=".", static_url_path="")
app.config["MAX_CONTENT_LENGTH"] = 12 * 1024 * 1024
app.config["JSON_AS_ASCII"] = False

if HAS_CORS:
    CORS(app, origins="*")


# ============================================================
# HELPERS
# ============================================================
def now_ms():
    return int(time.time() * 1000)


def get_db():
    """Yangi DB ulanish."""
    conn = sqlite3.connect(DB_FILE, timeout=30, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA busy_timeout = 10000")
    return conn


def run_sql(sql, params=(), fetch=None):
    """SQL bajarish yordamchisi."""
    conn = get_db()
    try:
        cur = conn.execute(sql, params)
        conn.commit()
        if fetch == "one":
            return cur.fetchone()
        if fetch == "all":
            return cur.fetchall()
        return cur.lastrowid
    finally:
        conn.close()


def run_many(queries):
    """Ko'p SQL bajarish (tranzaksiya)."""
    conn = get_db()
    try:
        for sql, params in queries:
            conn.execute(sql, params)
        conn.commit()
        return True
    except Exception as e:
        conn.rollback()
        print(f"❌ SQL error: {e}")
        return False
    finally:
        conn.close()


# ============================================================
# SCHEMA
# ============================================================
SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,
    avatar TEXT DEFAULT '⭐',
    bio TEXT DEFAULT '',
    verified INTEGER DEFAULT 0,
    last_seen INTEGER,
    created_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS api_tokens (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    token TEXT UNIQUE NOT NULL,
    created_at INTEGER NOT NULL,
    FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_token ON api_tokens(token);

CREATE TABLE IF NOT EXISTS posts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    text TEXT DEFAULT '',
    image_url TEXT,
    tags TEXT DEFAULT '',
    created_at INTEGER NOT NULL,
    deleted_at INTEGER,
    FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_post_created ON posts(created_at);

CREATE TABLE IF NOT EXISTS comments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    post_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    text TEXT NOT NULL,
    created_at INTEGER NOT NULL,
    deleted_at INTEGER,
    FOREIGN KEY(post_id) REFERENCES posts(id) ON DELETE CASCADE,
    FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS reactions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    post_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    emoji TEXT NOT NULL,
    created_at INTEGER NOT NULL,
    UNIQUE(post_id, user_id),
    FOREIGN KEY(post_id) REFERENCES posts(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS saves (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    post_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    created_at INTEGER NOT NULL,
    UNIQUE(post_id, user_id),
    FOREIGN KEY(post_id) REFERENCES posts(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS follows (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    follower_id INTEGER NOT NULL,
    following_id INTEGER NOT NULL,
    created_at INTEGER NOT NULL,
    UNIQUE(follower_id, following_id)
);

CREATE TABLE IF NOT EXISTS groups_t (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    emoji TEXT DEFAULT '👥',
    description TEXT DEFAULT '',
    created_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS group_members (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    group_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    UNIQUE(group_id, user_id)
);

CREATE TABLE IF NOT EXISTS chats (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user1_id INTEGER NOT NULL,
    user2_id INTEGER NOT NULL,
    is_ai INTEGER DEFAULT 0,
    created_at INTEGER NOT NULL,
    UNIQUE(user1_id, user2_id)
);

CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id INTEGER NOT NULL,
    sender_id INTEGER NOT NULL,
    text TEXT NOT NULL,
    created_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS market (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    price INTEGER DEFAULT 0,
    description TEXT DEFAULT '',
    emoji TEXT DEFAULT '📦',
    user_id INTEGER NOT NULL,
    created_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS stories (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    emoji TEXT DEFAULT '✨',
    text TEXT DEFAULT '',
    image_url TEXT,
    created_at INTEGER NOT NULL,
    expires_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS story_views (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    story_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    UNIQUE(story_id, user_id)
);

CREATE TABLE IF NOT EXISTS notifications (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    actor_id INTEGER,
    kind TEXT NOT NULL,
    ref_id INTEGER,
    text TEXT NOT NULL,
    read INTEGER DEFAULT 0,
    created_at INTEGER NOT NULL
);
"""


# ============================================================
# INIT
# ============================================================
def init_db():
    """Bazani yaratish."""
    print(f"🔧 Baza yaratilmoqda: {DB_FILE}")
    conn = get_db()
    try:
        conn.executescript(SCHEMA)
        conn.commit()
        print("✅ Schema tayyor")
    except Exception as e:
        print(f"❌ Schema xatosi: {e}")
        raise
    finally:
        conn.close()


def seed_db():
    """Demo ma'lumotlar."""
    try:
        count = run_sql("SELECT COUNT(*) FROM users", fetch="one")[0]
        if count > 0:
            print(f"ℹ️ Baza allaqachon to'ldirilgan ({count} user)")
            return

        print("🌱 Demo data yaratilmoqda...")
        t = now_ms()
        pwd = generate_password_hash("demo123")

        demo_users = [
            ("Dilnoza Karimova", "🌻", "UI/UX dizayner", 1),
            ("Javlon Toshmatov", "🚀", "Startap asoschisi", 1),
            ("Malika Yusupova", "🦋", "Fotograf", 0),
            ("Bekzod Rahimov", "🐺", "Backend dasturchi", 1),
            ("Sevinch Aliyeva", "🌙", "Kitob klubi", 0),
            ("Nodira Ismoilova", "🌻", "Marketolog", 0),
            ("Aziz Sobirov", "⚡", "Frontend dev", 1),
            ("Kamola Nazarova", "🐨", "Ingliz tili", 0),
        ]

        conn = get_db()
        try:
            for name, avatar, bio, verified in demo_users:
                conn.execute(
                    "INSERT INTO users(name,password_hash,avatar,bio,verified,last_seen,created_at) "
                    "VALUES(?,?,?,?,?,?,?)",
                    (name, pwd, avatar, bio, verified, t, t)
                )

            demo_posts = [
                (1, "Unigram'ga xush kelibsiz! 🎉", "unigram,yangilik"),
                (2, "Yangi dizayn loyihasi boshlandi 🎨", "dizayn"),
                (3, "3 oyda 10K foydalanuvchi — saboq 🚀", "startap,biznes"),
                (4, "Python 3.12 yangi imkoniyatlari 💻", "dasturlash"),
                (5, "Kitob almashish klubi ochildi 📚", "kitob"),
                (6, "React 19 yangi xususiyatlari 🎬", "dasturlash,react"),
                (7, "Toshkent quyoshi ☀️", "toshkent,foto"),
                (8, "AI yangi modellar 🤖", "ai,texnologiya"),
            ]

            for i, (uid, text, tags) in enumerate(demo_posts):
                conn.execute(
                    "INSERT INTO posts(user_id,text,tags,created_at) VALUES(?,?,?,?)",
                    (uid, text, tags, t - i * 3600000)
                )

            demo_groups = [
                ("Dizaynerlar", "🎨", "UI/UX muhokamasi"),
                ("Startapchilar", "🚀", "G'oya va MVP"),
                ("Toshkent", "📍", "Shahar yangiliklari"),
                ("Kitobxonlar", "📚", "Kitob tavsiyalari"),
            ]
            for name, emoji, desc in demo_groups:
                conn.execute(
                    "INSERT INTO groups_t(name,emoji,description,created_at) VALUES(?,?,?,?)",
                    (name, emoji, desc, t)
                )

            demo_market = [
                ("iPhone 15 Pro", 999, "256GB Titanium", "📱"),
                ("MacBook Pro M3", 1999, "14 inch, 16GB", "💻"),
                ("AirPods Max", 549, "Space Gray", "🎧"),
            ]
            for i, (title, price, desc, emoji) in enumerate(demo_market):
                conn.execute(
                    "INSERT INTO market(title,price,description,emoji,user_id,created_at) "
                    "VALUES(?,?,?,?,?,?)",
                    (title, price, desc, emoji, i + 1, t)
                )

            # Follows
            for a, b in [(1, 2), (1, 3), (2, 3), (3, 1), (4, 1)]:
                try:
                    conn.execute(
                        "INSERT INTO follows(follower_id,following_id,created_at) VALUES(?,?,?)",
                        (a, b, t)
                    )
                except:
                    pass

            # Stories
            for i in range(3):
                conn.execute(
                    "INSERT INTO stories(user_id,emoji,text,created_at,expires_at) "
                    "VALUES(?,?,?,?,?)",
                    (i + 1, "🌅", f"Demo story {i + 1}", t, t + 24 * 3600000)
                )

            conn.commit()
            print(f"✅ Demo data: {len(demo_users)} users, {len(demo_posts)} posts")
        finally:
            conn.close()
    except Exception as e:
        print(f"⚠️ Seed xatosi (davom etiladi): {e}")


# ============================================================
# AUTH
# ============================================================
def auth_required(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        hdr = request.headers.get("Authorization", "")
        token = hdr.replace("Bearer ", "").strip()
        if not token:
            return jsonify({"error": "Token kerak"}), 401

        row = run_sql(
            "SELECT u.* FROM users u JOIN api_tokens t ON t.user_id = u.id "
            "WHERE t.token = ?",
            (token,),
            fetch="one"
        )
        if not row:
            return jsonify({"error": "Token yaroqsiz"}), 401

        g.user = dict(row)
        g.token = token

        # Update last seen (throttle)
        if now_ms() - (g.user.get("last_seen") or 0) > 60000:
            try:
                run_sql("UPDATE users SET last_seen=? WHERE id=?",
                        (now_ms(), g.user["id"]))
            except:
                pass

        return fn(*args, **kwargs)
    return wrapper


def auth_optional(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        g.user = None
        hdr = request.headers.get("Authorization", "")
        token = hdr.replace("Bearer ", "").strip()
        if token:
            row = run_sql(
                "SELECT u.* FROM users u JOIN api_tokens t ON t.user_id = u.id "
                "WHERE t.token = ?",
                (token,),
                fetch="one"
            )
            if row:
                g.user = dict(row)
        return fn(*args, **kwargs)
    return wrapper


# ============================================================
# HEALTH (render uchun MAJBURIY)
# ============================================================
@app.route("/health")
@app.route("/api/health")
def health():
    """Health check — Render har 30s da tekshiradi."""
    try:
        run_sql("SELECT 1", fetch="one")
        return jsonify({
            "ok": True,
            "status": "healthy",
            "time": now_ms(),
            "render": IS_RENDER,
            "db": "ok"
        }), 200
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/version")
def version():
    return jsonify({"name": "Unigram", "version": "5.0.0", "render": IS_RENDER})


# ============================================================
# AUTH ENDPOINTS
# ============================================================
@app.route("/api/auth/register", methods=["POST"])
def api_register():
    data = request.get_json(silent=True) or {}
    name = (data.get("name") or "").strip()
    password = data.get("password", "")
    avatar = (data.get("avatar") or "⭐")[:4]

    if len(name) < 2:
        return jsonify({"error": "Ism kamida 2 belgi"}), 400
    if len(password) < 4:
        return jsonify({"error": "Parol kamida 4 belgi"}), 400

    existing = run_sql("SELECT 1 FROM users WHERE name=?", (name,), fetch="one")
    if existing:
        return jsonify({"error": "Bu ism band"}), 409

    uid = run_sql(
        "INSERT INTO users(name,password_hash,avatar,created_at,last_seen) "
        "VALUES(?,?,?,?,?)",
        (name, generate_password_hash(password), avatar, now_ms(), now_ms())
    )

    token = secrets.token_urlsafe(48)
    run_sql(
        "INSERT INTO api_tokens(user_id,token,created_at) VALUES(?,?,?)",
        (uid, token, now_ms())
    )

    print(f"✅ Yangi user: {name} (ID: {uid})")

    return jsonify({
        "token": token,
        "user": {"id": uid, "name": name, "avatar": avatar}
    }), 201


@app.route("/api/auth/login", methods=["POST"])
def api_login():
    data = request.get_json(silent=True) or {}
    name = (data.get("name") or "").strip()
    password = data.get("password", "")

    if not name or not password:
        return jsonify({"error": "Ism va parol kerak"}), 400

    user = run_sql("SELECT * FROM users WHERE name=?", (name,), fetch="one")
    if not user or not check_password_hash(user["password_hash"], password):
        return jsonify({"error": "Ism yoki parol xato"}), 401

    token = secrets.token_urlsafe(48)
    run_sql(
        "INSERT INTO api_tokens(user_id,token,created_at) VALUES(?,?,?)",
        (user["id"], token, now_ms())
    )
    run_sql("UPDATE users SET last_seen=? WHERE id=?", (now_ms(), user["id"]))

    return jsonify({
        "token": token,
        "user": {
            "id": user["id"],
            "name": user["name"],
            "avatar": user["avatar"],
            "bio": user["bio"],
            "verified": bool(user["verified"])
        }
    })


@app.route("/api/auth/logout", methods=["POST"])
@auth_required
def api_logout():
    run_sql("DELETE FROM api_tokens WHERE token=?", (g.token,))
    return jsonify({"ok": True})


@app.route("/api/me")
@auth_required
def api_me():
    return jsonify({
        "id": g.user["id"],
        "name": g.user["name"],
        "avatar": g.user["avatar"],
        "bio": g.user["bio"],
        "verified": bool(g.user["verified"])
    })


@app.route("/api/me", methods=["PATCH"])
@auth_required
def api_update_me():
    data = request.get_json(silent=True) or {}
    if "avatar" in data:
        run_sql("UPDATE users SET avatar=? WHERE id=?",
                (str(data["avatar"])[:4], g.user["id"]))
    if "bio" in data:
        run_sql("UPDATE users SET bio=? WHERE id=?",
                (str(data["bio"])[:200], g.user["id"]))
    return jsonify({"ok": True})


# ============================================================
# POSTS
# ============================================================
def post_dict(row, me_id=None):
    """Post dict."""
    if not row:
        return None

    p = dict(row)
    author = run_sql("SELECT name, avatar, verified FROM users WHERE id=?",
                     (p["user_id"],), fetch="one")

    reactions = run_sql(
        "SELECT COUNT(*) as cnt FROM reactions WHERE post_id=?",
        (p["id"],), fetch="one"
    )
    comments = run_sql(
        "SELECT COUNT(*) as cnt FROM comments WHERE post_id=? AND deleted_at IS NULL",
        (p["id"],), fetch="one"
    )

    my_reaction = None
    my_save = False
    if me_id:
        r = run_sql("SELECT emoji FROM reactions WHERE post_id=? AND user_id=?",
                    (p["id"], me_id), fetch="one")
        my_reaction = r["emoji"] if r else None
        s = run_sql("SELECT 1 FROM saves WHERE post_id=? AND user_id=?",
                    (p["id"], me_id), fetch="one")
        my_save = s is not None

    return {
        "id": p["id"],
        "authorId": p["user_id"],
        "authorName": author["name"] if author else "Deleted",
        "avatar": author["avatar"] if author else "⭐",
        "verified": bool(author["verified"]) if author else False,
        "text": p["text"],
        "image": p["image_url"],
        "tags": p["tags"].split(",") if p["tags"] else [],
        "at": p["created_at"],
        "likes": reactions["cnt"] if reactions else 0,
        "liked": my_reaction is not None,
        "myReaction": my_reaction,
        "commentCount": comments["cnt"] if comments else 0,
        "saved": my_save,
    }


@app.route("/api/posts")
@auth_optional
def api_posts():
    me_id = g.user["id"] if g.user else None
    rows = run_sql(
        "SELECT * FROM posts WHERE deleted_at IS NULL ORDER BY created_at DESC LIMIT 100",
        fetch="all"
    )
    return jsonify([post_dict(r, me_id) for r in rows])


@app.route("/api/posts", methods=["POST"])
@auth_required
def api_create_post():
    data = request.get_json(silent=True) or {}
    text = (data.get("text") or "").strip()

    if not text:
        return jsonify({"error": "Bo'sh post"}), 400
    if len(text) > 5000:
        return jsonify({"error": "Post juda uzun"}), 400

    # Rasm
    image_url = None
    if data.get("image"):
        try:
            b64 = data["image"]
            if b64.startswith("data:image/"):
                header, data_b64 = b64.split(",", 1)
                m = re.search(r"image/(\w+)", header)
                if m:
                    ext = m.group(1).lower()
                    if ext == "jpeg":
                        ext = "jpg"
                    if ext in ("png", "jpg", "gif", "webp"):
                        raw = base64.b64decode(data_b64)
                        if len(raw) < 4 * 1024 * 1024:
                            fname = f"post_{secrets.token_hex(10)}.{ext}"
                            with open(os.path.join(UPLOAD_DIR, fname), "wb") as f:
                                f.write(raw)
                            image_url = "/uploads/" + fname
        except Exception as e:
            print(f"Rasm xatosi: {e}")

    tags = ",".join(re.findall(r"#(\w+)", text))[:300]

    pid = run_sql(
        "INSERT INTO posts(user_id,text,image_url,tags,created_at) VALUES(?,?,?,?,?)",
        (g.user["id"], text, image_url, tags, now_ms())
    )

    row = run_sql("SELECT * FROM posts WHERE id=?", (pid,), fetch="one")
    return jsonify(post_dict(row, g.user["id"])), 201


@app.route("/api/posts/<int:pid>", methods=["DELETE"])
@auth_required
def api_delete_post(pid):
    row = run_sql("SELECT user_id FROM posts WHERE id=?", (pid,), fetch="one")
    if not row:
        return jsonify({"error": "Topilmadi"}), 404
    if row["user_id"] != g.user["id"]:
        return jsonify({"error": "Ruxsat yo'q"}), 403
    run_sql("UPDATE posts SET deleted_at=? WHERE id=?", (now_ms(), pid))
    return jsonify({"ok": True})


@app.route("/api/posts/<int:pid>/react", methods=["POST"])
@auth_required
def api_react(pid):
    data = request.get_json(silent=True) or {}
    emoji = (data.get("emoji") or "❤️")[:8]

    existing = run_sql(
        "SELECT emoji FROM reactions WHERE post_id=? AND user_id=?",
        (pid, g.user["id"]), fetch="one"
    )

    if existing and existing["emoji"] == emoji:
        run_sql("DELETE FROM reactions WHERE post_id=? AND user_id=?",
                (pid, g.user["id"]))
        my = None
    else:
        run_sql(
            "INSERT INTO reactions(post_id,user_id,emoji,created_at) VALUES(?,?,?,?) "
            "ON CONFLICT(post_id,user_id) DO UPDATE SET emoji=excluded.emoji",
            (pid, g.user["id"], emoji, now_ms())
        )
        my = emoji

    cnt = run_sql("SELECT COUNT(*) FROM reactions WHERE post_id=?",
                  (pid,), fetch="one")[0]

    return jsonify({"liked": my is not None, "myReaction": my, "count": cnt})


@app.route("/api/posts/<int:pid>/save", methods=["POST"])
@auth_required
def api_save(pid):
    existing = run_sql(
        "SELECT 1 FROM saves WHERE post_id=? AND user_id=?",
        (pid, g.user["id"]), fetch="one"
    )

    if existing:
        run_sql("DELETE FROM saves WHERE post_id=? AND user_id=?",
                (pid, g.user["id"]))
        saved = False
    else:
        run_sql("INSERT INTO saves(post_id,user_id,created_at) VALUES(?,?,?)",
                (pid, g.user["id"], now_ms()))
        saved = True

    return jsonify({"saved": saved})


@app.route("/api/posts/<int:pid>/comment", methods=["POST"])
@auth_required
def api_comment(pid):
    data = request.get_json(silent=True) or {}
    text = (data.get("text") or "").strip()[:1000]

    if not text:
        return jsonify({"error": "Izoh bo'sh"}), 400

    cid = run_sql(
        "INSERT INTO comments(post_id,user_id,text,created_at) VALUES(?,?,?,?)",
        (pid, g.user["id"], text, now_ms())
    )
    return jsonify({"id": cid}), 201


# ============================================================
# TREND
# ============================================================
@app.route("/api/trend")
@auth_optional
def api_trend():
    me_id = g.user["id"] if g.user else None
    rng = request.args.get("range", "all")
    now = now_ms()
    cut = {"day": 86400000, "week": 604800000}.get(rng)

    rows = run_sql(
        "SELECT * FROM posts WHERE deleted_at IS NULL ORDER BY created_at DESC LIMIT 100",
        fetch="all"
    )

    items = []
    for r in rows:
        if cut and now - r["created_at"] > cut:
            continue
        d = post_dict(r, me_id)
        age_h = max(0.1, (now - r["created_at"]) / 3600000)
        eng = d["likes"] * 3 + d["commentCount"] * 6
        d["score"] = round((eng + 1) / ((age_h + 2) ** 1.35) * 100, 2)
        items.append(d)

    items.sort(key=lambda x: -x["score"])
    return jsonify({"posts": items[:30]})


# ============================================================
# PEOPLE / FOLLOW
# ============================================================
@app.route("/api/people")
@auth_optional
def api_people():
    me_id = g.user["id"] if g.user else None
    rows = run_sql(
        "SELECT id, name, avatar, bio, verified FROM users ORDER BY created_at DESC LIMIT 100",
        fetch="all"
    )

    result = []
    for r in rows:
        user = dict(r)
        user["verified"] = bool(user["verified"])

        if me_id:
            following = run_sql(
                "SELECT 1 FROM follows WHERE follower_id=? AND following_id=?",
                (me_id, user["id"]), fetch="one"
            )
            user["following"] = following is not None
        else:
            user["following"] = False

        followers = run_sql(
            "SELECT COUNT(*) FROM follows WHERE following_id=?",
            (user["id"],), fetch="one"
        )[0]
        user["followers"] = followers
        result.append(user)

    return jsonify(result)


@app.route("/api/people/<int:uid>/follow", methods=["POST"])
@auth_required
def api_follow(uid):
    if uid == g.user["id"]:
        return jsonify({"error": "O'zingizni kuzatolmaysiz"}), 400

    existing = run_sql(
        "SELECT 1 FROM follows WHERE follower_id=? AND following_id=?",
        (g.user["id"], uid), fetch="one"
    )

    if existing:
        run_sql("DELETE FROM follows WHERE follower_id=? AND following_id=?",
                (g.user["id"], uid))
        following = False
    else:
        run_sql(
            "INSERT INTO follows(follower_id,following_id,created_at) VALUES(?,?,?)",
            (g.user["id"], uid, now_ms())
        )
        following = True

    cnt = run_sql(
        "SELECT COUNT(*) FROM follows WHERE following_id=?",
        (uid,), fetch="one"
    )[0]

    return jsonify({"following": following, "followers": cnt})


# ============================================================
# GROUPS
# ============================================================
@app.route("/api/groups")
@auth_optional
def api_groups():
    me_id = g.user["id"] if g.user else None
    rows = run_sql("SELECT * FROM groups_t ORDER BY id", fetch="all")

    result = []
    for r in rows:
        g_info = dict(r)
        member_count = run_sql(
            "SELECT COUNT(*) FROM group_members WHERE group_id=?",
            (r["id"],), fetch="one"
        )[0]

        joined = False
        if me_id:
            joined_row = run_sql(
                "SELECT 1 FROM group_members WHERE group_id=? AND user_id=?",
                (r["id"], me_id), fetch="one"
            )
            joined = joined_row is not None

        result.append({
            "id": g_info["id"],
            "name": g_info["name"],
            "emoji": g_info["emoji"],
            "desc": g_info["description"],
            "members": member_count,
            "joined": joined,
        })

    return jsonify(result)


@app.route("/api/groups", methods=["POST"])
@auth_required
def api_create_group():
    data = request.get_json(silent=True) or {}
    name = (data.get("name") or "").strip()[:80]

    if not name:
        return jsonify({"error": "Nom kerak"}), 400

    gid = run_sql(
        "INSERT INTO groups_t(name,emoji,description,created_at) VALUES(?,?,?,?)",
        (name, (data.get("emoji") or "👥")[:4],
         (data.get("desc") or "")[:300], now_ms())
    )

    run_sql(
        "INSERT INTO group_members(group_id,user_id) VALUES(?,?)",
        (gid, g.user["id"])
    )

    return jsonify({"id": gid, "name": name, "joined": True, "members": 1})


@app.route("/api/groups/<int:gid>/join", methods=["POST"])
@auth_required
def api_join_group(gid):
    existing = run_sql(
        "SELECT 1 FROM group_members WHERE group_id=? AND user_id=?",
        (gid, g.user["id"]), fetch="one"
    )

    if existing:
        run_sql("DELETE FROM group_members WHERE group_id=? AND user_id=?",
                (gid, g.user["id"]))
        joined = False
    else:
        run_sql("INSERT INTO group_members(group_id,user_id) VALUES(?,?)",
                (gid, g.user["id"]))
        joined = True

    cnt = run_sql(
        "SELECT COUNT(*) FROM group_members WHERE group_id=?",
        (gid,), fetch="one"
    )[0]

    return jsonify({"joined": joined, "members": cnt})


# ============================================================
# CHAT
# ============================================================
@app.route("/api/chats")
@auth_required
def api_chats():
    me = g.user["id"]
    rows = run_sql(
        "SELECT * FROM chats WHERE user1_id=? OR user2_id=? ORDER BY created_at DESC",
        (me, me), fetch="all"
    )

    result = []
    for r in rows:
        if r["is_ai"]:
            name, avatar, kind = "Unigram AI", "🤖", "ai"
        else:
            other = r["user2_id"] if r["user1_id"] == me else r["user1_id"]
            u = run_sql("SELECT name, avatar FROM users WHERE id=?",
                        (other,), fetch="one")
            if not u:
                continue
            name, avatar, kind = u["name"], u["avatar"], "user"

        last = run_sql(
            "SELECT text FROM messages WHERE chat_id=? ORDER BY id DESC LIMIT 1",
            (r["id"],), fetch="one"
        )

        result.append({
            "id": r["id"],
            "name": name,
            "avatar": avatar,
            "kind": kind,
            "last": last["text"] if last else "Xabar yo'q",
        })

    result.sort(key=lambda x: (x["kind"] != "ai", -x["id"]))
    return jsonify(result)


@app.route("/api/chats/ai", methods=["POST"])
@auth_required
def api_chat_ai():
    me = g.user["id"]
    row = run_sql(
        "SELECT * FROM chats WHERE user1_id=? AND is_ai=1",
        (me,), fetch="one"
    )

    if not row:
        cid = run_sql(
            "INSERT INTO chats(user1_id,user2_id,is_ai,created_at) VALUES(?,?,1,?)",
            (me, 0, now_ms())
        )
        run_sql(
            "INSERT INTO messages(chat_id,sender_id,text,created_at) VALUES(?,?,?,?)",
            (cid, 0, "Salom! Men Unigram AI man. Menga savol bering 🙂", now_ms())
        )
        row = run_sql("SELECT * FROM chats WHERE id=?", (cid,), fetch="one")

    return jsonify({"id": row["id"]})


@app.route("/api/chats/with/<int:uid>", methods=["POST"])
@auth_required
def api_chat_with(uid):
    me = g.user["id"]
    if uid == me:
        return jsonify({"error": "O'zingiz bilan chat yo'q"}), 400

    a, b = (me, uid) if me < uid else (uid, me)

    row = run_sql(
        "SELECT * FROM chats WHERE is_ai=0 AND user1_id=? AND user2_id=?",
        (a, b), fetch="one"
    )

    if not row:
        cid = run_sql(
            "INSERT INTO chats(user1_id,user2_id,is_ai,created_at) VALUES(?,?,0,?)",
            (a, b, now_ms())
        )
        row = run_sql("SELECT * FROM chats WHERE id=?", (cid,), fetch="one")

    return jsonify({"id": row["id"]})


def ai_reply(text):
    """Oddiy AI javob."""
    t = (text or "").lower()

    if any(k in t for k in ["salom", "assalom", "hey", "hi", "qalay"]):
        return "Salom! Qalaysiz? 😊"
    if "trend" in t or "top" in t:
        return "Trend Top bo'limida eng qizg'in postlar 🔥"
    if "guruh" in t:
        return "Guruhlar bo'limida jamoa toping 👥"
    if "bozor" in t or "narx" in t:
        return "Bozor bo'limida e'lon joylashingiz mumkin 🛍️"
    if "rahmat" in t:
        return "Arzimaydi! 😊"
    if "?" in t:
        return "Yaxshi savol! Demo versiyada oddiy javob beraman 🤖"
    return "Tushundim 👍"


@app.route("/api/chats/<int:cid>/messages")
@auth_required
def api_messages(cid):
    me = g.user["id"]
    after = request.args.get("after", type=int) or 0

    chat = run_sql(
        "SELECT * FROM chats WHERE id=? AND (user1_id=? OR user2_id=?)",
        (cid, me, me), fetch="one"
    )
    if not chat:
        return jsonify({"error": "Topilmadi"}), 404

    # AI auto-reply
    if chat["is_ai"]:
        last = run_sql(
            "SELECT * FROM messages WHERE chat_id=? ORDER BY id DESC LIMIT 1",
            (cid,), fetch="one"
        )
        if last and last["sender_id"] == me and now_ms() - last["created_at"] > 1500:
            reply = ai_reply(last["text"])
            run_sql(
                "INSERT INTO messages(chat_id,sender_id,text,created_at) VALUES(?,?,?,?)",
                (cid, 0, reply, now_ms())
            )

    rows = run_sql(
        "SELECT * FROM messages WHERE chat_id=? AND id>? ORDER BY id",
        (cid, after), fetch="all"
    )

    return jsonify([
        {
            "id": r["id"],
            "from": "me" if r["sender_id"] == me else "them",
            "text": r["text"],
            "at": r["created_at"]
        }
        for r in rows
    ])


@app.route("/api/chats/<int:cid>/messages", methods=["POST"])
@auth_required
def api_send_message(cid):
    me = g.user["id"]
    data = request.get_json(silent=True) or {}
    text = (data.get("text") or "").strip()[:2000]

    if not text:
        return jsonify({"error": "Xabar bo'sh"}), 400

    chat = run_sql(
        "SELECT 1 FROM chats WHERE id=? AND (user1_id=? OR user2_id=?)",
        (cid, me, me), fetch="one"
    )
    if not chat:
        return jsonify({"error": "Topilmadi"}), 404

    mid = run_sql(
        "INSERT INTO messages(chat_id,sender_id,text,created_at) VALUES(?,?,?,?)",
        (cid, me, text, now_ms())
    )

    return jsonify({"id": mid, "from": "me", "text": text, "at": now_ms()}), 201


# ============================================================
# STORIES
# ============================================================
@app.route("/api/stories")
@auth_optional
def api_stories():
    me_id = g.user["id"] if g.user else None
    now = now_ms()

    rows = run_sql(
        "SELECT s.*, u.name, u.avatar FROM stories s JOIN users u ON u.id=s.user_id "
        "WHERE s.expires_at > ? ORDER BY s.created_at DESC",
        (now,), fetch="all"
    )

    seen_ids = set()
    if me_id:
        seen = run_sql(
            "SELECT story_id FROM story_views WHERE user_id=?",
            (me_id,), fetch="all"
        )
        seen_ids = {r["story_id"] for r in seen}

    return jsonify([
        {
            "id": r["id"],
            "userId": r["user_id"],
            "who": r["name"],
            "av": r["avatar"],
            "emoji": r["emoji"],
            "text": r["text"],
            "image": r["image_url"],
            "isMine": r["user_id"] == me_id,
            "seen": r["id"] in seen_ids,
            "at": r["created_at"]
        }
        for r in rows
    ])


@app.route("/api/stories", methods=["POST"])
@auth_required
def api_create_story():
    data = request.get_json(silent=True) or {}
    text = (data.get("text") or "").strip()[:200]
    emoji = (data.get("emoji") or "✨")[:4]

    if not text:
        return jsonify({"error": "Matn kerak"}), 400

    sid = run_sql(
        "INSERT INTO stories(user_id,emoji,text,created_at,expires_at) VALUES(?,?,?,?,?)",
        (g.user["id"], emoji, text, now_ms(), now_ms() + 24 * 3600000)
    )

    return jsonify({"id": sid}), 201


@app.route("/api/stories/<int:sid>/view", methods=["POST"])
@auth_required
def api_view_story(sid):
    try:
        run_sql(
            "INSERT INTO story_views(story_id,user_id) VALUES(?,?)",
            (sid, g.user["id"])
        )
    except:
        pass
    return jsonify({"ok": True})


# ============================================================
# MARKET
# ============================================================
@app.route("/api/market")
@auth_optional
def api_market():
    rows = run_sql(
        "SELECT m.*, u.name AS seller_name FROM market m JOIN users u ON u.id=m.user_id "
        "ORDER BY m.created_at DESC",
        fetch="all"
    )

    return jsonify([
        {
            "id": r["id"],
            "title": r["title"],
            "price": r["price"],
            "desc": r["description"],
            "emoji": r["emoji"],
            "seller": r["seller_name"],
            "at": r["created_at"]
        }
        for r in rows
    ])


@app.route("/api/market", methods=["POST"])
@auth_required
def api_create_market():
    data = request.get_json(silent=True) or {}
    title = (data.get("title") or "").strip()[:120]

    if not title:
        return jsonify({"error": "Nom kerak"}), 400

    mid = run_sql(
        "INSERT INTO market(title,price,description,emoji,user_id,created_at) "
        "VALUES(?,?,?,?,?,?)",
        (title, int(data.get("price", 0) or 0),
         (data.get("desc") or "")[:500],
         (data.get("emoji") or "📦")[:4],
         g.user["id"], now_ms())
    )

    return jsonify({"id": mid}), 201


# ============================================================
# SEARCH
# ============================================================
@app.route("/api/search")
@auth_optional
def api_search():
    q = (request.args.get("q") or "").strip().lower()
    tab = request.args.get("tab", "posts")
    me_id = g.user["id"] if g.user else None

    if not q:
        return jsonify([])

    q_clean = q.lstrip("#")

    if tab == "posts":
        rows = run_sql(
            "SELECT * FROM posts WHERE deleted_at IS NULL "
            "AND (LOWER(text) LIKE ? OR LOWER(tags) LIKE ?) "
            "ORDER BY created_at DESC LIMIT 50",
            (f"%{q}%", f"%{q_clean}%"), fetch="all"
        )
        return jsonify([post_dict(r, me_id) for r in rows])

    if tab == "people":
        rows = run_sql(
            "SELECT * FROM users WHERE LOWER(name) LIKE ? OR LOWER(bio) LIKE ? LIMIT 30",
            (f"%{q}%", f"%{q}%"), fetch="all"
        )
        return jsonify([
            {"id": r["id"], "name": r["name"], "avatar": r["avatar"],
             "bio": r["bio"], "verified": bool(r["verified"])}
            for r in rows
        ])

    if tab == "groups":
        rows = run_sql(
            "SELECT * FROM groups_t WHERE LOWER(name) LIKE ? OR LOWER(description) LIKE ? LIMIT 30",
            (f"%{q}%", f"%{q}%"), fetch="all"
        )
        return jsonify([
            {"id": r["id"], "name": r["name"], "emoji": r["emoji"],
             "desc": r["description"]}
            for r in rows
        ])

    return jsonify([])


# ============================================================
# NOTIFICATIONS
# ============================================================
@app.route("/api/notifications")
@auth_required
def api_notifications():
    rows = run_sql(
        "SELECT * FROM notifications WHERE user_id=? ORDER BY created_at DESC LIMIT 50",
        (g.user["id"],), fetch="all"
    )
    return jsonify([
        {"id": r["id"], "kind": r["kind"], "text": r["text"],
         "read": bool(r["read"]), "at": r["created_at"]}
        for r in rows
    ])


@app.route("/api/notifications/count")
@auth_required
def api_notifications_count():
    cnt = run_sql(
        "SELECT COUNT(*) FROM notifications WHERE user_id=? AND read=0",
        (g.user["id"],), fetch="one"
    )[0]
    return jsonify({"unread": cnt})


@app.route("/api/notifications/read", methods=["POST"])
@auth_required
def api_notifications_read():
    run_sql("UPDATE notifications SET read=1 WHERE user_id=?", (g.user["id"],))
    return jsonify({"ok": True})


# ============================================================
# ADMIN
# ============================================================
@app.route("/api/admin/stats")
def api_admin_stats():
    key = request.args.get("key")
    if key != ADMIN_SECRET:
        return jsonify({"error": "Admin kalit kerak"}), 403

    return jsonify({
        "users": run_sql("SELECT COUNT(*) FROM users", fetch="one")[0],
        "posts": run_sql("SELECT COUNT(*) FROM posts WHERE deleted_at IS NULL", fetch="one")[0],
        "comments": run_sql("SELECT COUNT(*) FROM comments WHERE deleted_at IS NULL", fetch="one")[0],
        "reactions": run_sql("SELECT COUNT(*) FROM reactions", fetch="one")[0],
        "chats": run_sql("SELECT COUNT(*) FROM chats", fetch="one")[0],
        "messages": run_sql("SELECT COUNT(*) FROM messages", fetch="one")[0],
        "stories": run_sql("SELECT COUNT(*) FROM stories", fetch="one")[0],
        "market": run_sql("SELECT COUNT(*) FROM market", fetch="one")[0],
    })


@app.route("/api/admin/users")
def api_admin_users():
    key = request.args.get("key")
    if key != ADMIN_SECRET:
        return jsonify({"error": "Admin kalit kerak"}), 403

    rows = run_sql(
        "SELECT id, name, avatar, bio, verified, created_at, last_seen FROM users ORDER BY created_at DESC LIMIT 100",
        fetch="all"
    )
    return jsonify([dict(r) for r in rows])


# ============================================================
# STATIC
# ============================================================
@app.route("/")
@app.route("/index.html")
def serve_index():
    idx = os.path.join(os.path.dirname(os.path.abspath(__file__)), "index.html")
    if os.path.exists(idx):
        return send_from_directory(os.path.dirname(idx), "index.html")
    return jsonify({
        "name": "Unigram",
        "status": "running",
        "message": "API ishlayapti. index.html fayli yo'q.",
        "endpoints": {
            "health": "/health",
            "version": "/api/version",
            "register": "POST /api/auth/register",
            "login": "POST /api/auth/login",
            "posts": "GET /api/posts",
        }
    })


@app.route("/uploads/<path:filename>")
def serve_upload(filename):
    return send_from_directory(UPLOAD_DIR, filename)


# ============================================================
# ERROR HANDLERS
# ============================================================
@app.errorhandler(404)
def error_404(e):
    if request.path.startswith("/api/"):
        return jsonify({"error": "Topilmadi"}), 404
    idx = os.path.join(os.path.dirname(os.path.abspath(__file__)), "index.html")
    if os.path.exists(idx):
        return send_from_directory(os.path.dirname(idx), "index.html")
    return jsonify({"error": "Not found"}), 404


@app.errorhandler(413)
def error_413(e):
    return jsonify({"error": "Fayl juda katta"}), 413


@app.errorhandler(500)
def error_500(e):
    print(f"❌ 500: {e}")
    return jsonify({"error": "Server xatosi"}), 500


# ============================================================
# BOOTSTRAP — Render import vaqtida ham ishlaydi
# ============================================================
print("=" * 60)
print(f"🚀 UNIGRAM ishga tushirilmoqda")
print(f"   Port: {PORT}")
print(f"   Render: {IS_RENDER}")
print(f"   DB: {os.path.abspath(DB_FILE)}")
print("=" * 60)

try:
    init_db()
    seed_db()
    print("✅ Bootstrap muvaffaqiyatli")
except Exception as e:
    print(f"❌ Bootstrap xatosi: {e}")

print("=" * 60)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=PORT, debug=False, threaded=True)
