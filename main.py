# ============================================================
# UNIGRAM — Real backend, Flask + SQLite, port 5566
# Ishlatish:
#   1. pip install flask
#   2. python main.py
#   3. Brauzer: http://localhost:5566
# ============================================================
import os
import sqlite3
import time
import secrets
import base64
import re
from functools import wraps
from flask import Flask, request, jsonify, send_from_directory, g, abort
from werkzeug.security import generate_password_hash, check_password_hash

# ---------- KONFIGURATSIYA ----------
app = Flask(__name__, static_folder='.', static_url_path='')
app.config['MAX_CONTENT_LENGTH'] = 8 * 1024 * 1024  # 8 MB
app.config['JSON_AS_ASCII'] = False

DB_FILE = 'unigram.db'
UPLOAD_DIR = 'uploads'
PORT = 5566

os.makedirs(UPLOAD_DIR, exist_ok=True)

# ---------- YORDAMCHILAR ----------
def now_ms():
    return int(time.time() * 1000)

def db():
    """Har chaqiriqda yangi ulanish (sqlite3 thread-safe emas)."""
    conn = sqlite3.connect(DB_FILE, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA foreign_keys = ON')
    conn.execute('PRAGMA journal_mode = WAL')
    return conn

def json_body():
    return request.get_json(silent=True) or {}

# ---------- SCHEMA ----------
SCHEMA = """
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
    created_at INTEGER NOT NULL,
    FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_tokens_token ON api_tokens(token);

CREATE TABLE IF NOT EXISTS posts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    text TEXT DEFAULT '',
    image_url TEXT,
    tags TEXT DEFAULT '',
    created_at INTEGER NOT NULL,
    FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS comments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    post_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
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
    text TEXT NOT NULL,
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
CREATE INDEX IF NOT EXISTS idx_notif_user ON notifications(user_id, read);
"""

# ---------- SEED ----------
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
            c.execute('''INSERT INTO users(name,password_hash,avatar,bio,verified,created_at)
                         VALUES(?,?,?,?,?,?)''', (name, ph, av, bio, v, t))

        # Guruhlar
        groups = [
            ('Dizaynerlar',        '🎨', 'UI/UX, grafik va illyustratsiya haqida jonli suhbat'),
            ('Startapchilar',      '🚀', 'G‘oya → MVP → bozor. Tajriba almashish maydoni'),
            ('Toshkent Tanishlar', '📍', 'Shahardagi tadbirlar, uchrashuvlar va yangiliklar'),
            ('Kitobxonlar',        '📚', 'Har hafta bitta kitob, bitta taassurot'),
            ('Dasturchilar',       '💻', 'Kod, savol-javob va freelance imkoniyatlar'),
            ('Fotografiya',        '📷', 'Suratlar, texnika va mahorat sirlari'),
        ]
        for name, em, desc in groups:
            c.execute('''INSERT INTO groups_t(name,emoji,description,created_by,created_at)
                         VALUES(?,?,?,1,?)''', (name, em, desc, t))

        # Bozor
        items = [
            ('Noutbuk Lenovo IdeaPad', 4200000, 'Ideal holatda, 8GB RAM, 512GB SSD. Kafolat bor.', '💻', 7),
            ('Grafik planşet Wacom',   850000,  'Dizaynerlar uchun. Ishlatilgan, lekin yangidek.',  '🎨', 1),
            ('Velosiped Trek',         1900000, '21 tezlik, yaxshi holatda. Toshkent ichida yetkazaman.', '🚲', 2),
            ('Ingliz tili darslari',   300000,  'Oyda 8 dars, onlayn. IELTS 7.5 gacha tayyorlayman.', '📘', 8),
            ('Kofe apparati DeLonghi', 2100000, 'Ofis uchun ideal. 2 oy ishlatilgan.',              '☕', 4),
        ]
        for title, price, desc, em, seller in items:
            c.execute('''INSERT INTO market(title,price,description,emoji,user_id,created_at)
                         VALUES(?,?,?,?,?,?)''', (title, price, desc, em, seller, t))

        # Postlar
        posts_data = [
            (1, "Unigram'ga xush kelibsiz! 🎉\nOdamlarni qiziqish, maqsad va ishonch asosida bog‘lovchi tarmoq.", "unigram,yangilik"),
            (2, "Bugun yangi loyihamni boshladim — Toshkentdagi kichik bizneslar uchun bepul dizayn darslari. Kim qatnashmoqchi? 👇", "dizayn,talim"),
            (3, "3 oyda 10 000 foydalanuvchi — startapim haqida qisqacha.\nEng katta saboq: mahsulotni emas, muammoni seving.", "startap,biznes"),
            (5, "Kitob almashish klubi ochdik. Har shanba, Navoiy ko‘chasi. Kitobingizni olib keling, yangisini olib keting 📚", "kitob,tadbirlar"),
            (4, "Kim Python o‘rganmoqchi?\nHar kuni 1 soat, onlayn, bepul. Faqat jiddiy niyat qilganlar yozing.", "dasturlash,talim"),
            (7, "Yangi React loyiham tayyor! Dashboard + real-time chart'lar. Kimga kerak, yozing.", "dasturlash,react"),
            (3, "Toshkentda ertalabki suratga tushish uchun eng zo‘r joylar: Chorsu, Mustaqillik maydoni, Metro bekati.", "foto,toshkent"),
        ]
        post_ids = []
        for i, (uid, text, tags) in enumerate(posts_data):
            created = t - (len(posts_data) - i) * 3600000
            c.execute('''INSERT INTO posts(user_id,text,tags,created_at) VALUES(?,?,?,?)''',
                      (uid, text, tags, created))
            post_ids.append(c.execute('SELECT last_insert_rowid()').fetchone()[0])

        # Demo reaksiyalar (trend uchun)
        reactions = [
            (post_ids[0], 2, '❤️'), (post_ids[0], 3, '🔥'), (post_ids[0], 4, '❤️'),
            (post_ids[0], 5, '😂'), (post_ids[0], 6, '❤️'), (post_ids[0], 7, '🔥'),
            (post_ids[1], 1, '❤️'), (post_ids[1], 3, '❤️'), (post_ids[1], 5, '🔥'),
            (post_ids[2], 1, '❤️'), (post_ids[2], 2, '🔥'), (post_ids[2], 4, '❤️'),
            (post_ids[2], 6, '😮'), (post_ids[2], 8, '❤️'),
            (post_ids[3], 1, '❤️'), (post_ids[3], 2, '❤️'),
            (post_ids[4], 3, '🔥'), (post_ids[4], 6, '❤️'), (post_ids[4], 8, '❤️'),
        ]
        for pid, uid, em in reactions:
            c.execute('''INSERT OR IGNORE INTO reactions(post_id,user_id,emoji,created_at)
                         VALUES(?,?,?,?)''', (pid, uid, em, t - 1800000))

        # Demo izohlar
        comments = [
            (post_ids[0], 2, 'Ajoyib g‘oya! 🔥'),
            (post_ids[0], 3, 'Qo‘shilaman! Bu tarmoq kerak edi.'),
            (post_ids[1], 4, 'Men qatnashaman! Qachon boshlanadi?'),
            (post_ids[2], 5, 'Motivatsiya uchun rahmat! 💪'),
            (post_ids[4], 1, 'Men ham qatnashmoqchiman!'),
        ]
        for pid, uid, text in comments:
            c.execute('''INSERT INTO comments(post_id,user_id,text,created_at)
                         VALUES(?,?,?,?)''', (pid, uid, text, t - 900000))

        # Stories
        stories = [
            (1, '🌅', 'Ertalabki Toshkent — go‘zal!'),
            (2, '💻', 'Yangi loyiha boshlanmoqda 🚀'),
            (5, '📚', 'Bugungi kitob: "Atomic Habits"'),
            (3, '🎨', 'Yangi dizayn eskizi'),
            (4, '☕', 'Kofe va kod ☕'),
        ]
        for uid, em, text in stories:
            c.execute('''INSERT INTO stories(user_id,emoji,text,created_at,expires_at)
                         VALUES(?,?,?,?,?)''', (uid, em, text, t - 3600000, t + 23*3600000))

def init_db():
    with db() as c:
        c.executescript(SCHEMA)
    seed()
    print(f'✅ Baza tayyor: {DB_FILE}')

# ---------- AUTH DECORATOR ----------
def auth(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        hdr = request.headers.get('Authorization', '')
        token = hdr.replace('Bearer ', '').strip()
        if not token:
            return jsonify({'error': 'Token kerak'}), 401
        with db() as c:
            row = c.execute('''
                SELECT u.* FROM users u
                JOIN api_tokens t ON t.user_id = u.id
                WHERE t.token = ?
            ''', (token,)).fetchone()
        if not row:
            return jsonify({'error': 'Token yaroqsiz'}), 401
        g.user = dict(row)
        g.token = token
        return f(*args, **kwargs)
    return wrapper

# ---------- RASM SAQLASH ----------
ALLOWED_EXT = {'png', 'jpg', 'jpeg', 'gif', 'webp'}

def save_image(b64_data, prefix='img'):
    """base64 data URL -> uploads/ fayl. URL qaytaradi yoki None."""
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
        if len(raw) > 3 * 1024 * 1024:
            return None
        fname = f'{prefix}_{secrets.token_hex(10)}.{ext}'
        path = os.path.join(UPLOAD_DIR, fname)
        with open(path, 'wb') as f:
            f.write(raw)
        return '/uploads/' + fname
    except Exception:
        return None

# ---------- BILDIRISHNOMA ----------
def notify(user_id, actor_id, kind, text, ref_id=None):
    if user_id == actor_id or not user_id:
        return
    with db() as c:
        c.execute('''INSERT INTO notifications(user_id,actor_id,kind,ref_id,text,created_at)
                     VALUES(?,?,?,?,?,?)''', (user_id, actor_id, kind, ref_id, text, now_ms()))

# ---------- POST DICT ----------
def post_dict(p, me_id):
    with db() as c:
        author = c.execute('SELECT * FROM users WHERE id=?', (p['user_id'],)).fetchone()
        cmts = c.execute('''
            SELECT cm.*, u.name, u.avatar, u.verified
            FROM comments cm JOIN users u ON u.id = cm.user_id
            WHERE cm.post_id = ? ORDER BY cm.created_at
        ''', (p['id'],)).fetchall()
        re_rows = c.execute('''SELECT emoji, COUNT(*) as n FROM reactions
                              WHERE post_id=? GROUP BY emoji''', (p['id'],)).fetchall()
        my_re = c.execute('SELECT emoji FROM reactions WHERE post_id=? AND user_id=?',
                          (p['id'], me_id)).fetchone()
        my_sv = c.execute('SELECT 1 FROM saves WHERE post_id=? AND user_id=?',
                          (p['id'], me_id)).fetchone()

    return {
        'id': p['id'],
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
        ]
    }

# ---------- AI JAVOB ----------
def ai_reply(text):
    t = (text or '').lower()
    if any(k in t for k in ['salom', 'assalom', 'hey', 'hi', 'qalay', 'qanday']):
        return 'Salom! Qalaysiz? 😊 Sizga qanday yordam bera olaman?'
    if 'trend' in t or 'top' in t:
        return 'Trend Top bo‘limida eng qizg‘in postlar 🔥 Algoritm tezlik + yangilik + sifatni hisobga oladi.'
    if 'guruh' in t:
        return 'Guruhlar bo‘limida qiziqishingizga mos jamoa toping 👥 Yoki o‘zingiz "＋ Yangi guruh" bilan yarating.'
    if 'bozor' in t or 'narx' in t or 'sot' in t:
        return 'Bozor bo‘limida e’lon joylashingiz yoki mavjudlarini ko‘rishingiz mumkin 🛍️'
    if 'do‘st' in t or 'dost' in t or 'kuzat' in t or 'follow' in t:
        return 'Yaqin atrofda odamlarni toping va "Kuzatish" ni bosing 👥'
    if 'rahmat' in t or 'tashakkur' in t:
        return 'Arzimaydi! 😊 Yana savolingiz bo‘lsa, bemalol yozing.'
    if 'post' in t:
        return 'Post yozish uchun lentadagi matn maydonidan foydalaning ✍️'
    if 'story' in t:
        return 'Story qo‘shish uchun lentadagi "＋ Story" tugmasini bosing 📸'
    if 'parol' in t or 'kirish' in t:
        return 'Parolni bilmasangiz, demo userlar uchun: demo123 🔑'
    if '?' in t:
        return 'Yaxshi savol! Bu demo versiyada oddiy javob beraman. To‘liq AI keyingi versiyada 🤖'
    return 'Tushundim 👍 Yana nima?'

# ---------- AVTO-JAVOB (demo userlar va AI) ----------
def maybe_auto_reply(chat_id, me_id):
    """Faqat foydalanuvchi xabar yozgan va 2.5s+ o‘tgan bo‘lsa javob qaytaradi."""
    with db() as c:
        chat = c.execute('SELECT * FROM chats WHERE id=?', (chat_id,)).fetchone()
        if not chat:
            return
        last = c.execute('SELECT * FROM messages WHERE chat_id=? ORDER BY id DESC LIMIT 1',
                         (chat_id,)).fetchone()
        if not last or last['sender_id'] != me_id:
            return
        # 2.5s dan kam bo‘lsa — foydalanuvchi hali yozayotgan bo‘lishi mumkin
        if now_ms() - last['created_at'] < 2500:
            return

        if chat['is_ai']:
            c.execute('''INSERT INTO messages(chat_id,sender_id,text,created_at)
                         VALUES(?,?,?,?)''', (chat_id, 0, ai_reply(last['text']), now_ms()))
        else:
            other_id = chat['user2_id'] if chat['user1_id'] == me_id else chat['user1_id']
            # Faqat demo userlarga (id 1-8) avto-javob
            if other_id < 1 or other_id > 8:
                return
            c.execute('''INSERT INTO messages(chat_id,sender_id,text,created_at)
                         VALUES(?,?,?,?)''', (chat_id, other_id, ai_reply(last['text']), now_ms()))

# ============================================================
# API: AUTH
# ============================================================
@app.post('/api/auth/register')
def api_register():
    d = json_body()
    name = (d.get('name') or '').strip()
    pwd = d.get('password') or ''
    av = (d.get('avatar') or '⭐').strip()[:4]
    if len(name) < 2:
        return jsonify({'error': 'ism juda qisqa'}), 400
    if len(pwd) < 4:
        return jsonify({'error': 'parol kamida 4 belgi'}), 400
    with db() as c:
        if c.execute('SELECT 1 FROM users WHERE name=?', (name,)).fetchone():
            return jsonify({'error': 'bu ism band'}), 409
        ph = generate_password_hash(pwd)
        c.execute('''INSERT INTO users(name,password_hash,avatar,created_at)
                     VALUES(?,?,?,?)''', (name, ph, av, now_ms()))
        uid = c.execute('SELECT last_insert_rowid()').fetchone()[0]
        token = secrets.token_urlsafe(32)
        c.execute('INSERT INTO api_tokens(user_id,token,created_at) VALUES(?,?,?)',
                  (uid, token, now_ms()))
    return jsonify({'token': token, 'id': uid, 'name': name, 'avatar': av,
                    'verified': False}), 201

@app.post('/api/auth/login')
def api_login():
    d = json_body()
    name = (d.get('name') or '').strip()
    pwd = d.get('password') or ''
    if not name or not pwd:
        return jsonify({'error': 'ism va parol kerak'}), 400
    with db() as c:
        u = c.execute('SELECT * FROM users WHERE name=?', (name,)).fetchone()
        if not u or not check_password_hash(u['password_hash'], pwd):
            return jsonify({'error': 'topilmadi'}), 401
        token = secrets.token_urlsafe(32)
        c.execute('INSERT INTO api_tokens(user_id,token,created_at) VALUES(?,?,?)',
                  (u['id'], token, now_ms()))
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
    return jsonify({
        'id': g.user['id'], 'name': g.user['name'],
        'avatar': g.user['avatar'], 'bio': g.user['bio'],
        'verified': bool(g.user['verified'])
    })

@app.patch('/api/me')
@auth
def api_update_me():
    d = json_body()
    fields, params = [], []
    if 'avatar' in d and d['avatar']:
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
# API: POSTS
# ============================================================
@app.get('/api/posts')
@auth
def api_list_posts():
    me = g.user['id']
    with db() as c:
        rows = c.execute('SELECT * FROM posts ORDER BY created_at DESC').fetchall()
    return jsonify([post_dict(r, me) for r in rows])

@app.post('/api/posts')
@auth
def api_create_post():
    d = json_body()
    text = (d.get('text') or '').strip()
    img = save_image(d.get('image'), 'post')
    tags = ','.join([str(t).strip() for t in (d.get('tags') or []) if t])[:300]
    if not text and not img:
        return jsonify({'error': 'bo‘sh post'}), 400
    me = g.user['id']
    with db() as c:
        c.execute('''INSERT INTO posts(user_id,text,image_url,tags,created_at)
                     VALUES(?,?,?,?,?)''', (me, text, img, tags, now_ms()))
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
    d = json_body()
    emoji = (d.get('emoji') or '❤️')[:8]
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
        ex = c.execute('SELECT 1 FROM saves WHERE post_id=? AND user_id=?',
                       (pid, me)).fetchone()
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
# API: TREND
# ============================================================
@app.get('/api/trend')
@auth
def api_trend():
    rng = request.args.get('range', 'all')
    now = now_ms()
    cut = {'day': 86400000, 'week': 604800000}.get(rng)
    me = g.user['id']
    with db() as c:
        rows = c.execute('SELECT * FROM posts ORDER BY created_at DESC').fetchall()
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
        authors = c.execute('''
            SELECT u.id, u.name, u.avatar, u.verified,
                   COUNT(p.id) as posts,
                   COALESCE((
                       SELECT COUNT(*) FROM reactions r
                       JOIN posts pp ON pp.id = r.post_id
                       WHERE pp.user_id = u.id
                   ), 0) as likes
            FROM users u
            LEFT JOIN posts p ON p.user_id = u.id
            GROUP BY u.id
            HAVING posts > 0
            ORDER BY likes DESC, posts DESC
            LIMIT 6
        ''').fetchall()

    return jsonify({
        'posts': items[:30],
        'authors': [{
            'id': a['id'], 'name': a['name'], 'avatar': a['avatar'],
            'verified': bool(a['verified']),
            'posts': a['posts'], 'likes': a['likes']
        } for a in authors]
    })

# ============================================================
# API: GROUPS
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
            out.append({
                'id': r['id'], 'name': r['name'], 'emoji': r['emoji'],
                'desc': r['description'], 'members': cnt + 100,
                'joined': joined
            })
    return jsonify(out)

@app.post('/api/groups')
@auth
def api_create_group():
    d = json_body()
    name = (d.get('name') or '').strip()[:80]
    if not name:
        return jsonify({'error': 'nom kerak'}), 400
    me = g.user['id']
    emoji = (d.get('emoji') or '👥')[:4]
    desc = (d.get('desc') or '')[:300]
    with db() as c:
        c.execute('''INSERT INTO groups_t(name,emoji,description,created_by,created_at)
                     VALUES(?,?,?,?,?)''', (name, emoji, desc, me, now_ms()))
        gid = c.execute('SELECT last_insert_rowid()').fetchone()[0]
        c.execute('INSERT INTO group_members(group_id,user_id) VALUES(?,?)', (gid, me))
    return jsonify({'id': gid, 'name': name, 'emoji': emoji, 'desc': desc,
                    'members': 1, 'joined': True})

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
# API: PEOPLE / FOLLOW
# ============================================================
@app.get('/api/people')
@auth
def api_people():
    me = g.user['id']
    with db() as c:
        rows = c.execute('SELECT * FROM users WHERE id != ? ORDER BY created_at DESC',
                         (me,)).fetchall()
        follows = {r['following_id'] for r in c.execute(
            'SELECT following_id FROM follows WHERE follower_id=?', (me,)).fetchall()}
        fc = {r['following_id']: r['n'] for r in c.execute(
            'SELECT following_id, COUNT(*) as n FROM follows GROUP BY following_id').fetchall()}
    out = []
    for r in rows:
        dist = round(((r['id'] * 7) % 100) / 10 + 0.3, 1)
        out.append({
            'id': r['id'], 'name': r['name'], 'avatar': r['avatar'],
            'bio': r['bio'], 'verified': bool(r['verified']),
            'distance': dist,
            'following': r['id'] in follows,
            'followers': fc.get(r['id'], 0)
        })
    return jsonify(out)

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
# API: MARKET
# ============================================================
@app.get('/api/market')
@auth
def api_market():
    with db() as c:
        rows = c.execute('''
            SELECT m.*, u.name AS seller_name
            FROM market m JOIN users u ON u.id = m.user_id
            ORDER BY m.created_at DESC
        ''').fetchall()
    return jsonify([{
        'id': r['id'], 'title': r['title'], 'price': r['price'],
        'desc': r['description'], 'emoji': r['emoji'],
        'seller': r['seller_name'], 'sellerId': r['user_id'],
        'at': r['created_at']
    } for r in rows])

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
                  (title, int(d.get('price', 0) or 0),
                   (d.get('desc') or '')[:500],
                   (d.get('emoji') or '📦')[:4],
                   me, now_ms()))
        iid = c.execute('SELECT last_insert_rowid()').fetchone()[0]
        r = c.execute('''
            SELECT m.*, u.name AS seller_name
            FROM market m JOIN users u ON u.id = m.user_id
            WHERE m.id=?
        ''', (iid,)).fetchone()
    return jsonify({
        'id': r['id'], 'title': r['title'], 'price': r['price'],
        'desc': r['description'], 'emoji': r['emoji'],
        'seller': r['seller_name'], 'sellerId': r['user_id'],
        'at': r['created_at']
    }), 201

# ============================================================
# API: CHAT
# ============================================================
@app.get('/api/chats')
@auth
def api_chats():
    me = g.user['id']
    with db() as c:
        rows = c.execute('''
            SELECT * FROM chats
            WHERE user1_id=? OR user2_id=?
            ORDER BY created_at DESC
        ''', (me, me)).fetchall()
        out = []
        for r in rows:
            if r['is_ai']:
                name, av, kind = 'Unigram AI', '🤖', 'ai'
            else:
                other_id = r['user2_id'] if r['user1_id'] == me else r['user1_id']
                u = c.execute('SELECT * FROM users WHERE id=?', (other_id,)).fetchone()
                if not u:
                    continue
                name, av, kind = u['name'], u['avatar'], 'user'
            last = c.execute('SELECT text FROM messages WHERE chat_id=? ORDER BY id DESC LIMIT 1',
                             (r['id'],)).fetchone()
            out.append({
                'id': r['id'], 'name': name, 'avatar': av, 'kind': kind,
                'last': last['text'] if last else 'Xabar yo‘q'
            })
        # AI chat birinchi
        out.sort(key=lambda x: (x['kind'] != 'ai', -x['id']))
    return jsonify(out)

@app.post('/api/chats/ai')
@auth
def api_chat_ai():
    me = g.user['id']
    with db() as c:
        row = c.execute('SELECT * FROM chats WHERE user1_id=? AND is_ai=1',
                        (me,)).fetchone()
        if not row:
            c.execute('INSERT INTO chats(user1_id,user2_id,is_ai,created_at) VALUES(?,?,1,?)',
                      (me, 0, now_ms()))
            cid = c.execute('SELECT last_insert_rowid()').fetchone()[0]
            c.execute('INSERT INTO messages(chat_id,sender_id,text,created_at) VALUES(?,?,?,?)',
                      (cid, 0,
                       'Salom! Men Unigram AI man. Menga savol bering 🙂',
                       now_ms()))
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
        row = c.execute('''
            SELECT * FROM chats WHERE is_ai=0 AND user1_id=? AND user2_id=?
        ''', (a, b)).fetchone()
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
    return jsonify([{
        'id': r['id'],
        'from': 'me' if r['sender_id'] == me else 'them',
        'text': r['text'],
        'at': r['created_at']
    } for r in rows])

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
# API: NOTIFICATIONS
# ============================================================
@app.get('/api/notifications')
@auth
def api_notifications():
    me = g.user['id']
    with db() as c:
        rows = c.execute('''
            SELECT * FROM notifications WHERE user_id=?
            ORDER BY created_at DESC LIMIT 50
        ''', (me,)).fetchall()
    return jsonify([{
        'id': r['id'], 'kind': r['kind'], 'text': r['text'],
        'ref_id': r['ref_id'], 'read': bool(r['read']), 'at': r['created_at']
    } for r in rows])

@app.get('/api/notifications/count')
@auth
def api_notifications_count():
    me = g.user['id']
    with db() as c:
        n = c.execute('SELECT COUNT(*) FROM notifications WHERE user_id=? AND read=0',
                      (me,)).fetchone()[0]
    return jsonify({'unread': n})

@app.post('/api/notifications/read')
@auth
def api_notifications_read():
    with db() as c:
        c.execute('UPDATE notifications SET read=1 WHERE user_id=?', (g.user['id'],))
    return jsonify({'ok': True})

# ============================================================
# API: STORIES
# ============================================================
@app.get('/api/stories')
@auth
def api_stories():
    me = g.user['id']
    now = now_ms()
    with db() as c:
        rows = c.execute('''
            SELECT s.*, u.name, u.avatar FROM stories s
            JOIN users u ON u.id = s.user_id
            WHERE s.expires_at > ?
            ORDER BY s.created_at DESC
        ''', (now,)).fetchall()
        seen = {r['story_id'] for r in c.execute(
            'SELECT story_id FROM story_views WHERE user_id=?', (me,)).fetchall()}
    return jsonify([{
        'id': r['id'], 'userId': r['user_id'], 'who': r['name'],
        'av': r['avatar'], 'emoji': r['emoji'], 'text': r['text'],
        'image': r['image_url'], 'at': r['created_at'],
        'isMine': r['user_id'] == me, 'seen': r['id'] in seen
    } for r in rows])

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
    me = g.user['id']
    with db() as c:
        try:
            c.execute('INSERT INTO story_views(story_id,user_id) VALUES(?,?)', (sid, me))
        except sqlite3.IntegrityError:
            pass
    return jsonify({'ok': True})

# ============================================================
# API: SEARCH
# ============================================================
@app.get('/api/search')
@auth
def api_search():
    q = (request.args.get('q') or '').strip().lower()
    tab = request.args.get('tab', 'posts')
    me = g.user['id']
    if not q:
        return jsonify([])
    # Hashtag qidiruvi uchun # ni olib tashlash
    q_clean = q.lstrip('#')

    with db() as c:
        if tab == 'posts':
            rows = c.execute('''
                SELECT * FROM posts
                WHERE LOWER(text) LIKE ? OR LOWER(tags) LIKE ?
                ORDER BY created_at DESC LIMIT 50
            ''', (f'%{q}%', f'%{q_clean}%')).fetchall()
            return jsonify([post_dict(r, me) for r in rows])

        if tab == 'people':
            rows = c.execute('''
                SELECT * FROM users WHERE id != ?
                AND (LOWER(name) LIKE ? OR LOWER(bio) LIKE ?)
                LIMIT 30
            ''', (me, f'%{q}%', f'%{q}%')).fetchall()
            return jsonify([{
                'id': r['id'], 'name': r['name'], 'avatar': r['avatar'],
                'bio': r['bio'], 'verified': bool(r['verified'])
            } for r in rows])

        if tab == 'groups':
            rows = c.execute('''
                SELECT * FROM groups_t
                WHERE LOWER(name) LIKE ? OR LOWER(description) LIKE ?
                LIMIT 30
            ''', (f'%{q}%', f'%{q}%')).fetchall()
            return jsonify([{
                'id': r['id'], 'name': r['name'], 'emoji': r['emoji'],
                'desc': r['description']
            } for r in rows])

        if tab == 'market':
            rows = c.execute('''
                SELECT m.*, u.name AS seller_name
                FROM market m JOIN users u ON u.id = m.user_id
                WHERE LOWER(m.title) LIKE ? OR LOWER(m.description) LIKE ?
                LIMIT 30
            ''', (f'%{q}%', f'%{q}%')).fetchall()
            return jsonify([{
                'id': r['id'], 'title': r['title'], 'price': r['price'],
                'desc': r['description'], 'emoji': r['emoji'],
                'seller': r['seller_name']
            } for r in rows])

    return jsonify([])

# ============================================================
# STATIC
# ============================================================
@app.get('/')
@app.get('/index.html')
def index():
    return send_from_directory('.', 'index.html')

@app.get('/uploads/<path:filename>')
def uploads(filename):
    return send_from_directory(UPLOAD_DIR, filename)

@app.get('/api/health')
def health():
    return jsonify({'ok': True, 'time': now_ms()})

# ============================================================
# XATO HANDLERLARI
# ============================================================
@app.errorhandler(404)
def not_found(e):
    if request.path.startswith('/api/'):
        return jsonify({'error': 'topilmadi'}), 404
    return send_from_directory('.', 'index.html')

@app.errorhandler(413)
def too_large(e):
    return jsonify({'error': 'fayl juda katta (8MB limit)'}), 413

# ============================================================
# ISHGA TUSHIRISH
# ============================================================
if __name__ == '__main__':
    init_db()
    print()
    print('=' * 56)
    print('  📸  U N I G R A M  —  ishga tushdi')
    print('=' * 56)
    print(f'  🌐  http://localhost:{PORT}')
    print(f'  📂  Baza: {os.path.abspath(DB_FILE)}')
    print(f'  🖼️   Rasmlar: {os.path.abspath(UPLOAD_DIR)}/')
    print()
    print('  Demo userlar (parol: demo123):')
    print('    · Dilnoza Karimova   🌻')
    print('    · Javlon Toshmatov   🚀')
    print('    · Bekzod Rahimov     🐺')
    print('    · Aziz Sobirov       ⚡')
    print('    · Kamola Nazarova    🐨')
    print()
    print('  To‘xtatish: Ctrl+C')
    print('=' * 56)
    print()
    app.run(host='0.0.0.0', port=PORT, debug=False, threaded=True)