# ============================================================
# UNIGRAM — Flask + SQLite, Render-ready
# Local: python main.py (port 5566)
# Render: gunicorn main:app --bind 0.0.0.0:$PORT
# ============================================================
import os
import sqlite3
import time
import secrets
import base64
import re
import json
from functools import wraps
from flask import Flask, request, jsonify, send_from_directory, g, make_response
from werkzeug.security import generate_password_hash, check_password_hash

# --- CORS (ixtiyoriy, lekin foydali) ---
try:
    from flask_cors import CORS
    HAS_CORS = True
except ImportError:
    HAS_CORS = False

# ============================================================
# KONFIGURATSIYA (Environment variables)
# ============================================================
IS_RENDER = os.environ.get('RENDER') == 'true' or os.environ.get('RENDER') == '1'
PORT = int(os.environ.get('PORT', 5566))
DEBUG = os.environ.get('FLASK_DEBUG', '0') == '1' and not IS_RENDER

# Render'da persistent disk bo'lsa, shu yo'lga yozing
# Disk mount path: /var/data (Render dashboard'da sozlanadi)
DATA_DIR = os.environ.get('DATA_DIR', '.')

DB_FILE = os.path.join(DATA_DIR, 'unigram.db')
UPLOAD_DIR = os.path.join(DATA_DIR, 'uploads')

SECRET_KEY = os.environ.get('SECRET_KEY', secrets.token_urlsafe(32))
ADMIN_SECRET = os.environ.get('ADMIN_SECRET', 'admin123')

# Ruxsat etilgan origin'lar (CORS uchun)
ALLOWED_ORIGINS = os.environ.get(
    'ALLOWED_ORIGINS',
    '*' if not IS_RENDER else os.environ.get('RENDER_EXTERNAL_URL', '*')
)

os.makedirs(UPLOAD_DIR, exist_ok=True)

# ============================================================
# FLASK APP
# ============================================================
app = Flask(__name__, static_folder='.', static_url_path='')
app.config['SECRET_KEY'] = SECRET_KEY
app.config['MAX_CONTENT_LENGTH'] = 12 * 1024 * 1024  # 12MB
app.config['JSON_AS_ASCII'] = False
app.config['JSON_SORT_KEYS'] = False

if HAS_CORS:
    CORS(app, origins=ALLOWED_ORIGINS, supports_credentials=True,
         allow_headers=['Content-Type', 'Authorization'])
    print(f'✅ CORS enabled for: {ALLOWED_ORIGINS}')

# ============================================================
# YORDAMCHILAR
# ============================================================
def now_ms():
    return int(time.time() * 1000)

def db():
    conn = sqlite3.connect(DB_FILE, timeout=30, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA foreign_keys = ON')
    conn.execute('PRAGMA journal_mode = WAL')
    conn.execute('PRAGMA busy_timeout = 5000')
    return conn

def json_body():
    return request.get_json(silent=True) or {}

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
    is_private INTEGER DEFAULT 0,
    verified_requested INTEGER DEFAULT 0,
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
CREATE INDEX IF NOT EXISTS idx_tokens_token ON api_tokens(token);

CREATE TABLE IF NOT EXISTS posts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    type TEXT DEFAULT 'post',
    text TEXT DEFAULT '',
    image_url TEXT,
    location TEXT,
    audio_id INTEGER,
    tags TEXT DEFAULT '',
    pinned INTEGER DEFAULT 0,
    archived INTEGER DEFAULT 0,
    views INTEGER DEFAULT 0,
    created_at INTEGER NOT NULL,
    FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_posts_type ON posts(type, archived, created_at);
CREATE INDEX IF NOT EXISTS idx_posts_user ON posts(user_id, archived);

CREATE TABLE IF NOT EXISTS comments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    post_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    parent_id INTEGER,
    text TEXT NOT NULL,
    created_at INTEGER NOT NULL,
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
    FOREIGN KEY(post_id) REFERENCES posts(id) ON DELETE CASCADE,
    FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS saves (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    post_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    created_at INTEGER NOT NULL,
    UNIQUE(post_id, user_id),
    FOREIGN KEY(post_id) REFERENCES posts(id) ON DELETE CASCADE,
    FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS follows (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    follower_id INTEGER NOT NULL,
    following_id INTEGER NOT NULL,
    created_at INTEGER NOT NULL,
    UNIQUE(follower_id, following_id),
    FOREIGN KEY(follower_id) REFERENCES users(id) ON DELETE CASCADE,
    FOREIGN KEY(following_id) REFERENCES users(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_follows_follower ON follows(follower_id);
CREATE INDEX IF NOT EXISTS idx_follows_following ON follows(following_id);

CREATE TABLE IF NOT EXISTS follow_requests (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    from_id INTEGER NOT NULL,
    to_id INTEGER NOT NULL,
    created_at INTEGER NOT NULL,
    UNIQUE(from_id, to_id),
    FOREIGN KEY(from_id) REFERENCES users(id) ON DELETE CASCADE,
    FOREIGN KEY(to_id) REFERENCES users(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS close_friends (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    friend_id INTEGER NOT NULL,
    UNIQUE(user_id, friend_id),
    FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE,
    FOREIGN KEY(friend_id) REFERENCES users(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS blocks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    blocker_id INTEGER NOT NULL,
    blocked_id INTEGER NOT NULL,
    created_at INTEGER NOT NULL,
    UNIQUE(blocker_id, blocked_id),
    FOREIGN KEY(blocker_id) REFERENCES users(id) ON DELETE CASCADE,
    FOREIGN KEY(blocked_id) REFERENCES users(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS reports (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    reporter_id INTEGER NOT NULL,
    target_type TEXT NOT NULL,
    target_id INTEGER NOT NULL,
    reason TEXT,
    status TEXT DEFAULT 'pending',
    created_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS hashtag_follows (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    hashtag TEXT NOT NULL,
    UNIQUE(user_id, hashtag),
    FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS groups_t (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    emoji TEXT DEFAULT '👥',
    description TEXT DEFAULT '',
    created_by INTEGER,
    created_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS group_members (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    group_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    UNIQUE(group_id, user_id),
    FOREIGN KEY(group_id) REFERENCES groups_t(id) ON DELETE CASCADE,
    FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
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
    text TEXT DEFAULT '',
    type TEXT DEFAULT 'text',
    media_url TEXT,
    reply_to_id INTEGER,
    read_at INTEGER,
    created_at INTEGER NOT NULL,
    FOREIGN KEY(chat_id) REFERENCES chats(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_messages_chat ON messages(chat_id, id);

CREATE TABLE IF NOT EXISTS market (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    price INTEGER DEFAULT 0,
    description TEXT DEFAULT '',
    emoji TEXT DEFAULT '📦',
    user_id INTEGER NOT NULL,
    created_at INTEGER NOT NULL,
    FOREIGN KEY(user_id) REFERENCES users(id)
);

CREATE TABLE IF NOT EXISTS stories (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    emoji TEXT DEFAULT '✨',
    text TEXT DEFAULT '',
    image_url TEXT,
    audience TEXT DEFAULT 'public',
    stickers TEXT,
    created_at INTEGER NOT NULL,
    expires_at INTEGER NOT NULL,
    FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS story_views (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    story_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    UNIQUE(story_id, user_id),
    FOREIGN KEY(story_id) REFERENCES stories(id) ON DELETE CASCADE,
    FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS notifications (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    actor_id INTEGER,
    kind TEXT NOT NULL,
    ref_id INTEGER,
    text TEXT NOT NULL,
    read INTEGER DEFAULT 0,
    created_at INTEGER NOT NULL,
    FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_notif_user ON notifications(user_id, read, created_at);
"""

def seed():
    with db() as c:
        if c.execute('SELECT COUNT(*) FROM users').fetchone()[0] > 0:
            return
        t = now_ms()
        ph = generate_password_hash('demo123')

        demo = [
            ('Dilnoza Karimova',   '🌻', 'UI/UX dizayner · 5 yil tajriba', 1),
            ('Javlon Toshmatov',   '🚀', 'Startap asoschisi · EdTech',     1),
            ('Malika Yusupova',    '🦋', 'Fotograf · sayyoh',              0),
            ('Bekzod Rahimov',     '🐺', 'Backend dasturchi · Python',     1),
            ('Sevinch Aliyeva',    '🌙', 'Kitob klubi asoschisi',          0),
            ('Nodira Ismoilova',   '🌻', 'Marketolog · SMM',               0),
            ('Aziz Sobirov',       '⚡', 'Frontend · React/Vue',           1),
            ('Kamola Nazarova',    '🐨', 'Ingliz tili o‘qituvchisi',       0),
        ]
        for name, av, bio, v in demo:
            c.execute('''INSERT INTO users(name,password_hash,avatar,bio,verified,created_at,last_seen)
                         VALUES(?,?,?,?,?,?,?)''', (name, ph, av, bio, v, t, t))

        groups = [
            ('Dizaynerlar',        '🎨', 'UI/UX, grafik va illyustratsiya'),
            ('Startapchilar',      '🚀', 'G‘oya → MVP → bozor'),
            ('Toshkent Tanishlar', '📍', 'Shahardagi tadbirlar'),
            ('Kitobxonlar',        '📚', 'Har hafta bitta kitob'),
            ('Dasturchilar',       '💻', 'Kod, savol-javob, freelance'),
            ('Fotografiya',        '📷', 'Suratlar, texnika, sirlar'),
        ]
        for name, em, desc in groups:
            c.execute('INSERT INTO groups_t(name,emoji,description,created_by,created_at) VALUES(?,?,?,1,?)',
                      (name, em, desc, t))

        items = [
            ('Noutbuk Lenovo IdeaPad', 4200000, '8GB RAM, 512GB SSD', '💻', 7),
            ('Grafik planşet Wacom',   850000,  'Dizaynerlar uchun',   '🎨', 1),
            ('Velosiped Trek',         1900000, '21 tezlik',          '🚲', 2),
            ('Ingliz tili darslari',   300000,  'IELTS 7.5',          '📘', 8),
            ('Kofe apparati',          2100000, 'DeLonghi, 2 oy',     '☕', 4),
        ]
        for title, price, desc, em, seller in items:
            c.execute('INSERT INTO market(title,price,description,emoji,user_id,created_at) VALUES(?,?,?,?,?,?)',
                      (title, price, desc, em, seller, t))

        posts_data = [
            (1, 'post', "Unigram'ga xush kelibsiz! 🎉", 'unigram,yangilik'),
            (2, 'post', "Bepul dizayn darslari. Kim qatnashmoqchi? 👇", 'dizayn,talim'),
            (3, 'reel', "Bugungi kod yozish marafoni 🚀", 'dasturlash,reel'),
            (5, 'post', "Kitob almashish klubi ochdik 📚", 'kitob,tadbirlar'),
            (4, 'post', "Python o'rganmoqchi bo'lganlar yozing.", 'dasturlash,talim'),
            (7, 'reel', "React tutorial qisqa video 🎬", 'react,dasturlash'),
        ]
        for uid, ptype, text, tags in posts_data:
            c.execute('INSERT INTO posts(user_id,type,text,tags,created_at) VALUES(?,?,?,?,?)',
                      (uid, ptype, text, tags, t - 3600000))

        for a, b in [(1,2),(1,3),(2,3),(3,1),(4,1),(5,2),(6,3),(7,1)]:
            c.execute('INSERT OR IGNORE INTO follows(follower_id,following_id,created_at) VALUES(?,?,?)',
                      (a, b, t))

        for uid, em, text in [(1, '🌅', 'Ertalabki Toshkent'), (2, '💻', 'Yangi loyiha'), (5, '📚', 'Bugungi kitob')]:
            c.execute('INSERT INTO stories(user_id,emoji,text,audience,created_at,expires_at) VALUES(?,?,?,?,?,?)',
                      (uid, em, text, 'public', t - 1800000, t + 22*3600000))

        print('✅ Seed data yaratildi')

def init_db():
    with db() as c:
        c.executescript(SCHEMA)
    seed()
    print(f'✅ Baza tayyor: {DB_FILE}')

# ============================================================
# AUTH
# ============================================================
def auth(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        hdr = request.headers.get('Authorization', '')
        token = hdr.replace('Bearer ', '').strip()
        if not token:
            return jsonify({'error': 'Token kerak', 'code': 'no_token'}), 401
        with db() as c:
            row = c.execute('''SELECT u.* FROM users u
                JOIN api_tokens t ON t.user_id = u.id
                WHERE t.token = ?''', (token,)).fetchone()
        if not row:
            return jsonify({'error': 'Token yaroqsiz', 'code': 'bad_token'}), 401
        g.user = dict(row)
        g.token = token
        # last_seen update
        if now_ms() - (g.user.get('last_seen') or 0) > 5 * 60000:
            with db() as c:
                c.execute('UPDATE users SET last_seen=? WHERE id=?', (now_ms(), g.user['id']))
        return f(*args, **kwargs)
    return wrapper

# ============================================================
# YORDAMCHI FUNKSIYALAR
# ============================================================
ALLOWED_EXT = {'png', 'jpg', 'jpeg', 'gif', 'webp'}

def save_image(b64_data, prefix='img'):
    if not b64_data or not isinstance(b64_data, str):
        return None
    if not b64_data.startswith('data:image/'):
        return None
    try:
        header, b64 = b64_data.split(',', 1)
        m = re.search(r'image/(\w+)', header)
        if not m:
            return None
        ext = m.group(1).lower()
        if ext == 'jpeg':
            ext = 'jpg'
        if ext not in ALLOWED_EXT:
            return None
        raw = base64.b64decode(b64)
        if len(raw) > 4 * 1024 * 1024:
            return None
        fname = f'{prefix}_{secrets.token_hex(10)}.{ext}'
        with open(os.path.join(UPLOAD_DIR, fname), 'wb') as f:
            f.write(raw)
        return '/uploads/' + fname
    except Exception as e:
        print(f'Image save error: {e}')
        return None

def notify(user_id, actor_id, kind, text, ref_id=None):
    if user_id == actor_id or not user_id:
        return
    with db() as c:
        c.execute('''INSERT INTO notifications(user_id,actor_id,kind,ref_id,text,created_at)
                     VALUES(?,?,?,?,?,?)''', (user_id, actor_id, kind, ref_id, text, now_ms()))

def post_dict(p, me_id):
    with db() as c:
        author = c.execute('SELECT * FROM users WHERE id=?', (p['user_id'],)).fetchone()
        cmts = c.execute('''SELECT cm.*, u.name, u.avatar, u.verified
            FROM comments cm JOIN users u ON u.id = cm.user_id
            WHERE cm.post_id = ? ORDER BY cm.created_at''', (p['id'],)).fetchall()
        re_rows = c.execute('SELECT emoji, COUNT(*) as n FROM reactions WHERE post_id=? GROUP BY emoji',
                            (p['id'],)).fetchall()
        my_re = c.execute('SELECT emoji FROM reactions WHERE post_id=? AND user_id=?',
                          (p['id'], me_id)).fetchone()
        my_sv = c.execute('SELECT 1 FROM saves WHERE post_id=? AND user_id=?',
                          (p['id'], me_id)).fetchone()

    return {
        'id': p['id'],
        'type': p['type'] if 'type' in p.keys() else 'post',
        'authorId': p['user_id'],
        'authorName': author['name'] if author else 'Deleted',
        'avatar': author['avatar'] if author else '⭐',
        'verified': bool(author['verified']) if author else False,
        'text': p['text'],
        'image': p['image_url'],
        'tags': p['tags'].split(',') if p['tags'] else [],
        'at': p['created_at'],
        'likes': sum(r['n'] for r in re_rows),
        'liked': my_re is not None,
        'myReaction': my_re['emoji'] if my_re else None,
        'reactions': {r['emoji']: r['n'] for r in re_rows},
        'saved': my_sv is not None,
        'comments': [
            {
                'id': x['id'],
                'authorId': x['user_id'],
                'author': x['name'],
                'avatar': x['avatar'],
                'verified': bool(x['verified']),
                'text': x['text'],
                'at': x['created_at'],
            } for x in cmts
        ],
        'commentCount': len(cmts)
    }

def user_brief(r, me_id):
    with db() as c:
        following = c.execute('SELECT 1 FROM follows WHERE follower_id=? AND following_id=?',
                              (me_id, r['id'])).fetchone() is not None
        followers = c.execute('SELECT COUNT(*) FROM follows WHERE following_id=?',
                              (r['id'],)).fetchone()[0]
        following_count = c.execute('SELECT COUNT(*) FROM follows WHERE follower_id=?',
                                    (r['id'],)).fetchone()[0]
        posts_count = c.execute('SELECT COUNT(*) FROM posts WHERE user_id=? AND archived=0',
                                (r['id'],)).fetchone()[0]
    return {
        'id': r['id'], 'name': r['name'], 'avatar': r['avatar'],
        'bio': r['bio'], 'verified': bool(r['verified']),
        'isPrivate': bool(r['is_private']) if 'is_private' in r.keys() else False,
        'following': following,
        'followers': followers, 'followingCount': following_count,
        'postsCount': posts_count,
        'lastSeen': r['last_seen'] if 'last_seen' in r.keys() else None
    }

def ai_reply(text):
    t = (text or '').lower()
    if any(k in t for k in ['salom', 'assalom', 'hey', 'hi', 'qalay']):
        return 'Salom! Qalaysiz? 😊 Sizga qanday yordam bera olaman?'
    if 'trend' in t or 'top' in t:
        return 'Trend Top bo‘limida eng qizg‘in postlar 🔥'
    if 'guruh' in t:
        return 'Guruhlar bo‘limida jamoa toping 👥'
    if 'bozor' in t or 'narx' in t:
        return 'Bozor bo‘limida e’lon joylashingiz mumkin 🛍️'
    if 'reel' in t:
        return 'Reels — qisqa videolar bo‘limi 🎬'
    if 'story' in t:
        return 'Story 24 soatdan keyin o‘chadi 📸'
    if 'rahmat' in t:
        return 'Arzimaydi! 😊'
    if '?' in t:
        return 'Yaxshi savol! Demo versiyada oddiy javob beraman 🤖'
    return 'Tushundim 👍'

def maybe_auto_reply(chat_id, me_id):
    with db() as c:
        chat = c.execute('SELECT * FROM chats WHERE id=?', (chat_id,)).fetchone()
        if not chat:
            return
        last = c.execute('SELECT * FROM messages WHERE chat_id=? ORDER BY id DESC LIMIT 1',
                         (chat_id,)).fetchone()
        if not last or last['sender_id'] != me_id:
            return
        if now_ms() - last['created_at'] < 2500:
            return
        if chat['is_ai']:
            c.execute('INSERT INTO messages(chat_id,sender_id,text,created_at) VALUES(?,?,?,?)',
                      (chat_id, 0, ai_reply(last['text']), now_ms()))
        else:
            other = chat['user2_id'] if chat['user1_id'] == me_id else chat['user1_id']
            if 1 <= other <= 8:
                c.execute('INSERT INTO messages(chat_id,sender_id,text,created_at) VALUES(?,?,?,?)',
                          (chat_id, other, ai_reply(last['text']), now_ms()))

# ============================================================
# HEALTH CHECK (Render uchun MUHIM!)
# ============================================================
@app.get('/health')
@app.get('/api/health')
def health():
    try:
        with db() as c:
            c.execute('SELECT 1').fetchone()
        return jsonify({
            'ok': True,
            'time': now_ms(),
            'version': '4.0',
            'render': IS_RENDER,
            'db': 'ok'
        }), 200
    except Exception as e:
        return jsonify({'ok': False, 'error': str(e)}), 500

# ============================================================
# AUTH API
# ============================================================
@app.post('/api/auth/register')
def api_register():
    d = json_body()
    name = (d.get('name') or '').strip()
    pwd = d.get('password') or ''
    av = (d.get('avatar') or '⭐').strip()[:4]
    if len(name) < 2:
        return jsonify({'error': 'Ism kamida 2 belgi', 'code': 'name_short'}), 400
    if len(name) > 30:
        return jsonify({'error': 'Ism 30 belgidan oshmasin', 'code': 'name_long'}), 400
    if len(pwd) < 4:
        return jsonify({'error': 'Parol kamida 4 belgi', 'code': 'pwd_short'}), 400
    with db() as c:
        if c.execute('SELECT 1 FROM users WHERE name=?', (name,)).fetchone():
            return jsonify({'error': 'Bu ism band', 'code': 'name_taken'}), 409
        c.execute('INSERT INTO users(name,password_hash,avatar,created_at,last_seen) VALUES(?,?,?,?,?)',
                  (name, generate_password_hash(pwd), av, now_ms(), now_ms()))
        uid = c.execute('SELECT last_insert_rowid()').fetchone()[0]
        token = secrets.token_urlsafe(32)
        c.execute('INSERT INTO api_tokens(user_id,token,created_at) VALUES(?,?,?)',
                  (uid, token, now_ms()))
    return jsonify({'token': token, 'id': uid, 'name': name, 'avatar': av,
                    'bio': '', 'verified': False}), 201

@app.post('/api/auth/login')
def api_login():
    d = json_body()
    name = (d.get('name') or '').strip()
    pwd = d.get('password') or ''
    if not name or not pwd:
        return jsonify({'error': 'Ism va parol kerak', 'code': 'missing'}), 400
    with db() as c:
        u = c.execute('SELECT * FROM users WHERE name=?', (name,)).fetchone()
        if not u or not check_password_hash(u['password_hash'], pwd):
            return jsonify({'error': 'Ism yoki parol xato', 'code': 'invalid_creds'}), 401
        token = secrets.token_urlsafe(32)
        c.execute('INSERT INTO api_tokens(user_id,token,created_at) VALUES(?,?,?)',
                  (u['id'], token, now_ms()))
        c.execute('UPDATE users SET last_seen=? WHERE id=?', (now_ms(), u['id']))
    return jsonify({'token': token, 'id': u['id'], 'name': u['name'],
                    'avatar': u['avatar'], 'bio': u['bio'],
                    'verified': bool(u['verified'])})

@app.post('/api/auth/logout')
@auth
def api_logout():
    with db() as c:
        c.execute('DELETE FROM api_tokens WHERE token=?', (g.token,))
    return jsonify({'ok': True})

@app.get('/api/me')
@auth
def api_me():
    with db() as c:
        followers = c.execute('SELECT COUNT(*) FROM follows WHERE following_id=?',
                              (g.user['id'],)).fetchone()[0]
        following = c.execute('SELECT COUNT(*) FROM follows WHERE follower_id=?',
                              (g.user['id'],)).fetchone()[0]
        posts = c.execute('SELECT COUNT(*) FROM posts WHERE user_id=? AND archived=0',
                          (g.user['id'],)).fetchone()[0]
    return jsonify({
        'id': g.user['id'], 'name': g.user['name'], 'avatar': g.user['avatar'],
        'bio': g.user['bio'], 'verified': bool(g.user['verified']),
        'followers': followers, 'following': following, 'posts': posts
    })

@app.patch('/api/me')
@auth
def api_update_me():
    d = json_body()
    fields, params = [], []
    if d.get('avatar'):
        fields.append('avatar=?')
        params.append(str(d['avatar'])[:4])
    if 'bio' in d:
        fields.append('bio=?')
        params.append(str(d['bio'])[:200])
    if not fields:
        return jsonify({'error': 'hech narsa'}), 400
    params.append(g.user['id'])
    with db() as c:
        c.execute(f"UPDATE users SET {','.join(fields)} WHERE id=?", params)
    return jsonify({'ok': True})

# ============================================================
# POSTS
# ============================================================
@app.get('/api/posts')
@auth
def api_list_posts():
    me = g.user['id']
    with db() as c:
        rows = c.execute('SELECT * FROM posts WHERE archived=0 ORDER BY pinned DESC, created_at DESC').fetchall()
    return jsonify([post_dict(r, me) for r in rows])

@app.post('/api/posts')
@auth
def api_create_post():
    d = json_body()
    text = (d.get('text') or '').strip()
    ptype = d.get('type', 'post')
    if ptype not in ('post', 'reel', 'album'):
        ptype = 'post'
    img = save_image(d.get('image'), 'post')
    tags = ','.join([str(t).strip() for t in (d.get('tags') or []) if t])[:300]
    if not text and not img:
        return jsonify({'error': 'bo‘sh post'}), 400
    me = g.user['id']
    with db() as c:
        c.execute('''INSERT INTO posts(user_id,type,text,image_url,tags,created_at)
                     VALUES(?,?,?,?,?,?)''', (me, ptype, text, img, tags, now_ms()))
        pid = c.execute('SELECT last_insert_rowid()').fetchone()[0]
        row = c.execute('SELECT * FROM posts WHERE id=?', (pid,)).fetchone()
    return jsonify(post_dict(row, me)), 201

@app.delete('/api/posts/<int:pid>')
@auth
def api_delete_post(pid):
    with db() as c:
        row = c.execute('SELECT user_id FROM posts WHERE id=?', (pid,)).fetchone()
        if not row:
            return jsonify({'error': 'topilmadi'}), 404
        if row['user_id'] != g.user['id']:
            return jsonify({'error': 'ruxsat yo‘q'}), 403
        c.execute('DELETE FROM posts WHERE id=?', (pid,))
    return jsonify({'ok': True})

@app.post('/api/posts/<int:pid>/react')
@auth
def api_react(pid):
    emoji = (json_body().get('emoji') or '❤️')[:8]
    me = g.user['id']
    with db() as c:
        p = c.execute('SELECT * FROM posts WHERE id=?', (pid,)).fetchone()
        if not p:
            return jsonify({'error': 'topilmadi'}), 404
        ex = c.execute('SELECT emoji FROM reactions WHERE post_id=? AND user_id=?',
                       (pid, me)).fetchone()
        if ex and ex['emoji'] == emoji:
            c.execute('DELETE FROM reactions WHERE post_id=? AND user_id=?', (pid, me))
            my = None
        else:
            c.execute('''INSERT INTO reactions(post_id,user_id,emoji,created_at)
                         VALUES(?,?,?,?)
                         ON CONFLICT(post_id,user_id) DO UPDATE SET emoji=excluded.emoji''',
                      (pid, me, emoji, now_ms()))
            my = emoji
            if p['user_id'] != me:
                notify(p['user_id'], me, 'reaction',
                       f'{g.user["name"]} postingizga {emoji} qo‘ydi', pid)
        cnt = c.execute('SELECT COUNT(*) FROM reactions WHERE post_id=?', (pid,)).fetchone()[0]
    return jsonify({'liked': my is not None, 'myReaction': my, 'count': cnt})

@app.post('/api/posts/<int:pid>/save')
@auth
def api_save_post(pid):
    me = g.user['id']
    with db() as c:
        ex = c.execute('SELECT 1 FROM saves WHERE post_id=? AND user_id=?', (pid, me)).fetchone()
        if ex:
            c.execute('DELETE FROM saves WHERE post_id=? AND user_id=?', (pid, me))
            saved = False
        else:
            c.execute('INSERT INTO saves(post_id,user_id,created_at) VALUES(?,?,?)',
                      (pid, me, now_ms()))
            saved = True
        cnt = c.execute('SELECT COUNT(*) FROM saves WHERE post_id=?', (pid,)).fetchone()[0]
    return jsonify({'saved': saved, 'count': cnt})

@app.post('/api/posts/<int:pid>/comment')
@auth
def api_comment(pid):
    d = json_body()
    text = (d.get('text') or '').strip()
    if not text:
        return jsonify({'error': 'bo‘sh'}), 400
    me = g.user['id']
    with db() as c:
        p = c.execute('SELECT user_id FROM posts WHERE id=?', (pid,)).fetchone()
        if not p:
            return jsonify({'error': 'topilmadi'}), 404
        c.execute('INSERT INTO comments(post_id,user_id,text,created_at) VALUES(?,?,?,?)',
                  (pid, me, text[:1000], now_ms()))
        cid = c.execute('SELECT last_insert_rowid()').fetchone()[0]
        if p['user_id'] != me:
            notify(p['user_id'], me, 'comment',
                   f'{g.user["name"]} izoh qoldirdi: {text[:40]}', pid)
    return jsonify({'id': cid}), 201

# ============================================================
# TREND
# ============================================================
@app.get('/api/trend')
@auth
def api_trend():
    rng = request.args.get('range', 'all')
    now = now_ms()
    cut = {'day': 86400000, 'week': 604800000}.get(rng)
    me = g.user['id']
    with db() as c:
        rows = c.execute('SELECT * FROM posts WHERE archived=0 ORDER BY created_at DESC').fetchall()
        items = []
        for r in rows:
            if cut and now - r['created_at'] > cut:
                continue
            d = post_dict(r, me)
            age_h = max(0.1, (now - r['created_at']) / 3600000)
            eng = d['likes'] * 3 + len(d['comments']) * 6 + sum(d['reactions'].values())
            d['score'] = round((eng + 1) / ((age_h + 2) ** 1.35) * 100, 2)
            items.append(d)
        items.sort(key=lambda x: -x['score'])
        authors = c.execute('''SELECT u.id, u.name, u.avatar, u.verified,
            COUNT(p.id) as posts,
            COALESCE((SELECT COUNT(*) FROM reactions r JOIN posts pp ON pp.id=r.post_id
                      WHERE pp.user_id=u.id),0) as likes
            FROM users u LEFT JOIN posts p ON p.user_id=u.id
            GROUP BY u.id HAVING posts>0 ORDER BY likes DESC LIMIT 6''').fetchall()
    return jsonify({
        'posts': items[:30],
        'authors': [{'id': a['id'], 'name': a['name'], 'avatar': a['avatar'],
                     'verified': bool(a['verified']), 'posts': a['posts'],
                     'likes': a['likes']} for a in authors]
    })

# ============================================================
# PEOPLE / FOLLOW
# ============================================================
@app.get('/api/people')
@auth
def api_people():
    me = g.user['id']
    with db() as c:
        rows = c.execute('SELECT * FROM users WHERE id != ? ORDER BY created_at DESC',
                         (me,)).fetchall()
    return jsonify([user_brief(r, me) for r in rows])

@app.post('/api/people/<int:uid>/follow')
@auth
def api_follow(uid):
    me = g.user['id']
    if uid == me:
        return jsonify({'error': 'o‘zingizni kuzatolmaysiz'}), 400
    with db() as c:
        u = c.execute('SELECT name FROM users WHERE id=?', (uid,)).fetchone()
        if not u:
            return jsonify({'error': 'topilmadi'}), 404
        ex = c.execute('SELECT 1 FROM follows WHERE follower_id=? AND following_id=?',
                       (me, uid)).fetchone()
        if ex:
            c.execute('DELETE FROM follows WHERE follower_id=? AND following_id=?', (me, uid))
            following = False
        else:
            c.execute('INSERT INTO follows(follower_id,following_id,created_at) VALUES(?,?,?)',
                      (me, uid, now_ms()))
            following = True
            notify(uid, me, 'follow', f'{g.user["name"]} sizni kuzatishni boshladi')
        cnt = c.execute('SELECT COUNT(*) FROM follows WHERE following_id=?',
                        (uid,)).fetchone()[0]
    return jsonify({'following': following, 'followers': cnt})

# ============================================================
# GROUPS
# ============================================================
@app.get('/api/groups')
@auth
def api_groups():
    me = g.user['id']
    with db() as c:
        rows = c.execute('SELECT * FROM groups_t ORDER BY id').fetchall()
        out = []
        for r in rows:
            joined = c.execute('SELECT 1 FROM group_members WHERE group_id=? AND user_id=?',
                               (r['id'], me)).fetchone() is not None
            cnt = c.execute('SELECT COUNT(*) FROM group_members WHERE group_id=?',
                            (r['id'],)).fetchone()[0]
            out.append({'id': r['id'], 'name': r['name'], 'emoji': r['emoji'],
                        'desc': r['description'], 'members': cnt + 100, 'joined': joined})
    return jsonify(out)

@app.post('/api/groups')
@auth
def api_create_group():
    d = json_body()
    name = (d.get('name') or '').strip()[:80]
    if not name:
        return jsonify({'error': 'nom kerak'}), 400
    me = g.user['id']
    with db() as c:
        c.execute('INSERT INTO groups_t(name,emoji,description,created_by,created_at) VALUES(?,?,?,?,?)',
                  (name, (d.get('emoji') or '👥')[:4], (d.get('desc') or '')[:300], me, now_ms()))
        gid = c.execute('SELECT last_insert_rowid()').fetchone()[0]
        c.execute('INSERT INTO group_members(group_id,user_id) VALUES(?,?)', (gid, me))
    return jsonify({'id': gid, 'name': name, 'emoji': d.get('emoji', '👥'),
                    'desc': d.get('desc', ''), 'members': 1, 'joined': True})

@app.post('/api/groups/<int:gid>/join')
@auth
def api_join_group(gid):
    me = g.user['id']
    with db() as c:
        ex = c.execute('SELECT 1 FROM group_members WHERE group_id=? AND user_id=?',
                       (gid, me)).fetchone()
        if ex:
            c.execute('DELETE FROM group_members WHERE group_id=? AND user_id=?', (gid, me))
            joined = False
        else:
            c.execute('INSERT INTO group_members(group_id,user_id) VALUES(?,?)', (gid, me))
            joined = True
        cnt = c.execute('SELECT COUNT(*) FROM group_members WHERE group_id=?',
                        (gid,)).fetchone()[0]
    return jsonify({'joined': joined, 'members': cnt + 100})

# ============================================================
# MARKET
# ============================================================
@app.get('/api/market')
@auth
def api_market():
    with db() as c:
        rows = c.execute('''SELECT m.*, u.name AS seller_name FROM market m
            JOIN users u ON u.id=m.user_id ORDER BY m.created_at DESC''').fetchall()
    return jsonify([{'id': r['id'], 'title': r['title'], 'price': r['price'],
                     'desc': r['description'], 'emoji': r['emoji'],
                     'seller': r['seller_name'], 'sellerId': r['user_id'],
                     'at': r['created_at']} for r in rows])

@app.post('/api/market')
@auth
def api_add_market():
    d = json_body()
    title = (d.get('title') or '').strip()[:120]
    if not title:
        return jsonify({'error': 'nom kerak'}), 400
    me = g.user['id']
    with db() as c:
        c.execute('''INSERT INTO market(title,price,description,emoji,user_id,created_at)
                     VALUES(?,?,?,?,?,?)''',
                  (title, int(d.get('price', 0) or 0), (d.get('desc') or '')[:500],
                   (d.get('emoji') or '📦')[:4], me, now_ms()))
        iid = c.execute('SELECT last_insert_rowid()').fetchone()[0]
        r = c.execute('''SELECT m.*, u.name AS seller_name FROM market m
            JOIN users u ON u.id=m.user_id WHERE m.id=?''', (iid,)).fetchone()
    return jsonify({'id': r['id'], 'title': r['title'], 'price': r['price'],
                    'desc': r['description'], 'emoji': r['emoji'],
                    'seller': r['seller_name'], 'sellerId': r['user_id'],
                    'at': r['created_at']}), 201

# ============================================================
# CHAT
# ============================================================
@app.get('/api/chats')
@auth
def api_chats():
    me = g.user['id']
    with db() as c:
        rows = c.execute('SELECT * FROM chats WHERE user1_id=? OR user2_id=? ORDER BY created_at DESC',
                         (me, me)).fetchall()
        out = []
        for r in rows:
            if r['is_ai']:
                name, av, kind = 'Unigram AI', '🤖', 'ai'
            else:
                other = r['user2_id'] if r['user1_id'] == me else r['user1_id']
                u = c.execute('SELECT * FROM users WHERE id=?', (other,)).fetchone()
                if not u:
                    continue
                name, av, kind = u['name'], u['avatar'], 'user'
            last = c.execute('SELECT text FROM messages WHERE chat_id=? ORDER BY id DESC LIMIT 1',
                             (r['id'],)).fetchone()
            out.append({'id': r['id'], 'name': name, 'avatar': av, 'kind': kind,
                        'last': last['text'] if last else 'Xabar yo‘q'})
        out.sort(key=lambda x: (x['kind'] != 'ai', -x['id']))
    return jsonify(out)

@app.post('/api/chats/ai')
@auth
def api_chat_ai():
    me = g.user['id']
    with db() as c:
        row = c.execute('SELECT * FROM chats WHERE user1_id=? AND is_ai=1', (me,)).fetchone()
        if not row:
            c.execute('INSERT INTO chats(user1_id,user2_id,is_ai,created_at) VALUES(?,?,1,?)',
                      (me, 0, now_ms()))
            cid = c.execute('SELECT last_insert_rowid()').fetchone()[0]
            c.execute('INSERT INTO messages(chat_id,sender_id,text,created_at) VALUES(?,?,?,?)',
                      (cid, 0, 'Salom! Men Unigram AI man. Menga savol bering 🙂', now_ms()))
            row = c.execute('SELECT * FROM chats WHERE id=?', (cid,)).fetchone()
    return jsonify({'id': row['id']})

@app.post('/api/chats/with/<int:uid>')
@auth
def api_chat_with(uid):
    me = g.user['id']
    if uid == me:
        return jsonify({'error': 'o‘zingiz bilan chat yo‘q'}), 400
    with db() as c:
        u = c.execute('SELECT id FROM users WHERE id=?', (uid,)).fetchone()
        if not u:
            return jsonify({'error': 'topilmadi'}), 404
        a, b = (me, uid) if me < uid else (uid, me)
        row = c.execute('SELECT * FROM chats WHERE is_ai=0 AND user1_id=? AND user2_id=?',
                        (a, b)).fetchone()
        if not row:
            c.execute('INSERT INTO chats(user1_id,user2_id,is_ai,created_at) VALUES(?,?,0,?)',
                      (a, b, now_ms()))
            cid = c.execute('SELECT last_insert_rowid()').fetchone()[0]
            row = c.execute('SELECT * FROM chats WHERE id=?', (cid,)).fetchone()
    return jsonify({'id': row['id']})

@app.get('/api/chats/<int:cid>/messages')
@auth
def api_get_messages(cid):
    me = g.user['id']
    with db() as c:
        chat = c.execute('SELECT * FROM chats WHERE id=? AND (user1_id=? OR user2_id=?)',
                         (cid, me, me)).fetchone()
        if not chat:
            return jsonify({'error': 'topilmadi'}), 404
    maybe_auto_reply(cid, me)
    after = request.args.get('after', type=int) or 0
    with db() as c:
        rows = c.execute('SELECT * FROM messages WHERE chat_id=? AND id>? ORDER BY id',
                         (cid, after)).fetchall()
        c.execute('UPDATE messages SET read_at=? WHERE chat_id=? AND sender_id != ? AND read_at IS NULL',
                  (now_ms(), cid, me))
    return jsonify([{'id': r['id'], 'from': 'me' if r['sender_id'] == me else 'them',
                     'text': r['text'], 'at': r['created_at'],
                     'read': bool(r['read_at']) if 'read_at' in r.keys() else False} for r in rows])

@app.post('/api/chats/<int:cid>/messages')
@auth
def api_send_message(cid):
    d = json_body()
    text = (d.get('text') or '').strip()[:2000]
    if not text:
        return jsonify({'error': 'bo‘sh'}), 400
    me = g.user['id']
    with db() as c:
        chat = c.execute('SELECT * FROM chats WHERE id=? AND (user1_id=? OR user2_id=?)',
                         (cid, me, me)).fetchone()
        if not chat:
            return jsonify({'error': 'topilmadi'}), 404
        c.execute('INSERT INTO messages(chat_id,sender_id,text,created_at) VALUES(?,?,?,?)',
                  (cid, me, text, now_ms()))
        mid = c.execute('SELECT last_insert_rowid()').fetchone()[0]
    return jsonify({'id': mid, 'from': 'me', 'text': text, 'at': now_ms()}), 201

# ============================================================
# NOTIFICATIONS
# ============================================================
@app.get('/api/notifications')
@auth
def api_notifications():
    with db() as c:
        rows = c.execute('SELECT * FROM notifications WHERE user_id=? ORDER BY created_at DESC LIMIT 50',
                         (g.user['id'],)).fetchall()
    return jsonify([{'id': r['id'], 'kind': r['kind'], 'text': r['text'],
                     'ref_id': r['ref_id'], 'read': bool(r['read']),
                     'at': r['created_at']} for r in rows])

@app.get('/api/notifications/count')
@auth
def api_notifications_count():
    with db() as c:
        n = c.execute('SELECT COUNT(*) FROM notifications WHERE user_id=? AND read=0',
                      (g.user['id'],)).fetchone()[0]
    return jsonify({'unread': n})

@app.post('/api/notifications/read')
@auth
def api_notifications_read():
    with db() as c:
        c.execute('UPDATE notifications SET read=1 WHERE user_id=?', (g.user['id'],))
    return jsonify({'ok': True})

# ============================================================
# STORIES
# ============================================================
@app.get('/api/stories')
@auth
def api_stories():
    me = g.user['id']
    now = now_ms()
    with db() as c:
        rows = c.execute('''SELECT s.*, u.name, u.avatar FROM stories s
            JOIN users u ON u.id=s.user_id WHERE s.expires_at>?
            ORDER BY s.created_at DESC''', (now,)).fetchall()
        seen = {r['story_id'] for r in c.execute(
            'SELECT story_id FROM story_views WHERE user_id=?', (me,)).fetchall()}
    return jsonify([{'id': r['id'], 'userId': r['user_id'], 'who': r['name'],
                     'av': r['avatar'], 'emoji': r['emoji'], 'text': r['text'],
                     'image': r['image_url'], 'at': r['created_at'],
                     'isMine': r['user_id'] == me, 'seen': r['id'] in seen}
                    for r in rows])

@app.post('/api/stories')
@auth
def api_create_story():
    d = json_body()
    img = save_image(d.get('image'), 'story')
    emoji = (d.get('emoji') or '✨')[:4]
    text = (d.get('text') or '').strip()[:200]
    if not text and not img:
        return jsonify({'error': 'bo‘sh'}), 400
    me = g.user['id']
    with db() as c:
        c.execute('''INSERT INTO stories(user_id,emoji,text,image_url,created_at,expires_at)
                     VALUES(?,?,?,?,?,?)''',
                  (me, emoji, text, img, now_ms(), now_ms() + 24 * 3600000))
        sid = c.execute('SELECT last_insert_rowid()').fetchone()[0]
    return jsonify({'id': sid}), 201

@app.post('/api/stories/<int:sid>/view')
@auth
def api_view_story(sid):
    with db() as c:
        try:
            c.execute('INSERT INTO story_views(story_id,user_id) VALUES(?,?)',
                      (sid, g.user['id']))
        except sqlite3.IntegrityError:
            pass
    return jsonify({'ok': True})

# ============================================================
# SEARCH
# ============================================================
@app.get('/api/search')
@auth
def api_search():
    q = (request.args.get('q') or '').strip().lower()
    tab = request.args.get('tab', 'posts')
    me = g.user['id']
    if not q:
        return jsonify([])
    q_clean = q.lstrip('#')
    with db() as c:
        if tab == 'posts':
            rows = c.execute('''SELECT * FROM posts WHERE archived=0
                AND (LOWER(text) LIKE ? OR LOWER(tags) LIKE ?)
                ORDER BY created_at DESC LIMIT 50''',
                (f'%{q}%', f'%{q_clean}%')).fetchall()
            return jsonify([post_dict(r, me) for r in rows])
        if tab == 'people':
            rows = c.execute('''SELECT * FROM users WHERE id != ?
                AND (LOWER(name) LIKE ? OR LOWER(bio) LIKE ?) LIMIT 30''',
                (me, f'%{q}%', f'%{q}%')).fetchall()
            return jsonify([user_brief(r, me) for r in rows])
        if tab == 'groups':
            rows = c.execute('''SELECT * FROM groups_t
                WHERE LOWER(name) LIKE ? OR LOWER(description) LIKE ? LIMIT 30''',
                (f'%{q}%', f'%{q}%')).fetchall()
            return jsonify([{'id': r['id'], 'name': r['name'], 'emoji': r['emoji'],
                             'desc': r['description']} for r in rows])
        if tab == 'market':
            rows = c.execute('''SELECT m.*, u.name AS seller_name FROM market m
                JOIN users u ON u.id=m.user_id
                WHERE LOWER(m.title) LIKE ? OR LOWER(m.description) LIKE ? LIMIT 30''',
                (f'%{q}%', f'%{q}%')).fetchall()
            return jsonify([{'id': r['id'], 'title': r['title'], 'price': r['price'],
                             'desc': r['description'], 'emoji': r['emoji'],
                             'seller': r['seller_name']} for r in rows])
    return jsonify([])

# ============================================================
# ADMIN
# ============================================================
def admin_check():
    return request.args.get('key') == ADMIN_SECRET

@app.get('/api/admin/users')
def api_admin_users():
    if not admin_check():
        return jsonify({'error': 'admin kalit kerak'}), 403
    with db() as c:
        rows = c.execute('''SELECT id, name, avatar, bio, verified,
            created_at, last_seen,
            (SELECT COUNT(*) FROM posts WHERE user_id=users.id) as posts,
            (SELECT COUNT(*) FROM api_tokens WHERE user_id=users.id) as sessions
            FROM users ORDER BY created_at DESC''').fetchall()
    return jsonify([dict(r) for r in rows])

@app.get('/api/admin/stats')
def api_admin_stats():
    if not admin_check():
        return jsonify({'error': 'admin'}), 403
    with db() as c:
        return jsonify({k: c.execute(f'SELECT COUNT(*) FROM {t}').fetchone()[0]
            for k, t in [
                ('users', 'users'), ('posts', 'posts'), ('comments', 'comments'),
                ('reactions', 'reactions'), ('chats', 'chats'), ('messages', 'messages'),
                ('stories', 'stories'), ('market', 'market'), ('sessions', 'api_tokens')
            ]})

# ============================================================
# STATIC FILES
# ============================================================
@app.get('/')
@app.get('/index.html')
def index():
    return send_from_directory('.', 'index.html')

@app.get('/uploads/<path:filename>')
def uploads(filename):
    return send_from_directory(UPLOAD_DIR, filename)

# SPA fallback
@app.errorhandler(404)
def not_found(e):
    if request.path.startswith('/api/'):
        return jsonify({'error': 'topilmadi'}), 404
    return send_from_directory('.', 'index.html')

@app.errorhandler(413)
def too_large(e):
    return jsonify({'error': 'fayl juda katta (12MB)'}), 413

@app.errorhandler(500)
def server_error(e):
    return jsonify({'error': 'server xatosi', 'detail': str(e)}), 500

# ============================================================
# ISHGA TUSHIRISH
# ============================================================
init_db()

if __name__ == '__main__':
    print()
    print('=' * 62)
    print('  📸  UNIGRAM — ishga tushdi')
    print('=' * 62)
    print(f'  🌐  http://localhost:{PORT}')
    print(f'  🏥  Health: http://localhost:{PORT}/health')
    print(f'  📂  Baza: {os.path.abspath(DB_FILE)}')
    print(f'  🖼️   Uploads: {os.path.abspath(UPLOAD_DIR)}')
    print(f'  🔧  Render: {IS_RENDER}')
    print(f'  🐛  Debug: {DEBUG}')
    print()
    print('  Demo userlar (parol: demo123):')
    print('    · Dilnoza Karimova · Javlon Toshmatov · Bekzod Rahimov')
    print()
    print('  To‘xtatish: Ctrl+C')
    print('=' * 62)
    print()
    app.run(host='0.0.0.0', port=PORT, debug=DEBUG, threaded=True)
