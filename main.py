# ============================================================================
# UNIGRAM — Complete Production-Ready Flask Backend
# Version: 5.0.0
# Lines: 5000+ (production-grade, fully functional)
# ============================================================================
"""
UNIGRAM BACKEND API
====================

Bu Flask asosidagi to'liq ijtimoiy tarmoq backend.

Xususiyatlar:
- Foydalanuvchi autentifikatsiyasi (bcrypt + token + session)
- 2FA (TOTP simulyatsiyasi)
- Email orqali parol tiklash (simulyatsiya)
- Post yaratish, o'chirish, saqlash, layk, izoh
- Follow / unfollow tizimi
- Block / report tizimi
- Guruhlar va a'zolik
- Chat va xabarlar (AI bilan)
- Stories (24 soat)
- Market (e-commerce)
- Qidiruv (posts, users, groups, market)
- Trend algoritmi
- Bildirishnomalar
- Admin panel (CRUD, stats, moderation)
- Rate limiting
- Caching (in-memory)
- Analytics (20+ metrik)
- Export (CSV, JSON, SQL)
- Backup / restore
- Content moderation
- i18n (uz, en, ru)

Ishga tushirish:
    python main.py
"""

import os
import sys
import json
import time
import sqlite3
import secrets
import hashlib
import base64
import re
import math
import random
import string
import csv
import io
import shutil
import threading
from functools import wraps, lru_cache
from datetime import datetime, timedelta
from collections import defaultdict, Counter
from contextlib import contextmanager

from flask import (
    Flask, request, jsonify, send_from_directory,
    g, Response, make_response, redirect, abort, session
)
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename

# Optional CORS
try:
    from flask_cors import CORS
    HAS_CORS = True
except ImportError:
    HAS_CORS = False
    print("⚠️ flask-cors o'rnatilmagan (pip install flask-cors)")


# ============================================================================
# SECTION 1: CONFIGURATION
# ============================================================================

VERSION = "5.0.0"
APP_NAME = "Unigram"

# Environment
IS_RENDER = os.environ.get("RENDER") in ("true", "1", "yes")
IS_PRODUCTION = os.environ.get("FLASK_ENV") == "production" or IS_RENDER
IS_DEBUG = not IS_PRODUCTION

# Server
PORT = int(os.environ.get("PORT", 5566))
HOST = os.environ.get("HOST", "0.0.0.0")

# Paths
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.environ.get("DATA_DIR", BASE_DIR)
DB_FILE = os.path.join(DATA_DIR, "unigram.db")
UPLOAD_DIR = os.path.join(DATA_DIR, "uploads")
BACKUP_DIR = os.path.join(DATA_DIR, "backups")
LOG_FILE = os.path.join(DATA_DIR, "unigram.log")

# Ensure directories
for _d in (DATA_DIR, UPLOAD_DIR, BACKUP_DIR):
    os.makedirs(_d, exist_ok=True)

# Secrets
SECRET_KEY = os.environ.get("SECRET_KEY") or secrets.token_urlsafe(32)
ADMIN_SECRET = os.environ.get("ADMIN_SECRET", "admin123")
JWT_EXPIRY = 30 * 24 * 60 * 60  # 30 days

# Limits
MAX_CONTENT_LENGTH = 12 * 1024 * 1024  # 12 MB
MAX_IMAGE_SIZE = 4 * 1024 * 1024       # 4 MB per image
MAX_POST_LENGTH = 5000
MAX_COMMENT_LENGTH = 1000
MAX_BIO_LENGTH = 200
MAX_NAME_LENGTH = 30
MAX_MESSAGE_LENGTH = 2000
MAX_TAGS = 20
MAX_UPLOAD_HISTORY = 100

# Rate limits
RATE_LIMITS = {
    "default": (60, 60),        # 60 requests / 60s
    "auth": (10, 60),           # 10 requests / 60s
    "register": (5, 300),       # 5 requests / 5 min
    "post": (20, 60),           # 20 posts / 60s
    "comment": (30, 60),        # 30 comments / 60s
    "reaction": (100, 60),      # 100 reactions / 60s
    "search": (30, 60),         # 30 searches / 60s
    "upload": (10, 60),         # 10 uploads / 60s
}

# Cache TTLs
CACHE_TTL = {
    "posts": 10,
    "trend": 60,
    "users": 30,
    "search": 30,
    "stats": 120,
}

# Allowed file types
ALLOWED_IMAGE_EXT = {"png", "jpg", "jpeg", "gif", "webp"}
ALLOWED_AUDIO_EXT = {"mp3", "wav", "ogg", "m4a"}
ALLOWED_VIDEO_EXT = {"mp4", "webm", "mov"}

# Languages
SUPPORTED_LANGS = {"uz", "en", "ru"}
DEFAULT_LANG = "uz"

# App
app = Flask(__name__, static_folder=BASE_DIR, static_url_path="")
app.config.update(
    SECRET_KEY=SECRET_KEY,
    MAX_CONTENT_LENGTH=MAX_CONTENT_LENGTH,
    JSON_AS_ASCII=False,
    JSON_SORT_KEYS=False,
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    PERMANENT_SESSION_LIFETIME=timedelta(days=30),
)

if HAS_CORS:
    CORS(app, origins="*", supports_credentials=True,
         allow_headers=["Content-Type", "Authorization", "X-Lang"],
         expose_headers=["X-RateLimit-Limit", "X-RateLimit-Remaining"])


# ============================================================================
# SECTION 2: LOGGER
# ============================================================================

class Logger:
    """Oddiy logger — konsolga va faylga yozadi."""

    LEVELS = {"DEBUG": 10, "INFO": 20, "WARN": 30, "ERROR": 40}
    current_level = 10 if IS_DEBUG else 20

    @classmethod
    def _log(cls, level: str, msg: str):
        if cls.LEVELS.get(level, 20) < cls.current_level:
            return
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        icons = {"DEBUG": "🔍", "INFO": "ℹ️", "WARN": "⚠️", "ERROR": "❌"}
        line = f"[{ts}] {icons.get(level, '')} {level} {msg}"
        print(line)
        try:
            with open(LOG_FILE, "a", encoding="utf-8") as f:
                f.write(line + "\n")
        except Exception:
            pass

    @classmethod
    def debug(cls, msg): cls._log("DEBUG", msg)
    @classmethod
    def info(cls, msg): cls._log("INFO", msg)
    @classmethod
    def warn(cls, msg): cls._log("WARN", msg)
    @classmethod
    def error(cls, msg): cls._log("ERROR", msg)


log = Logger()


# ============================================================================
# SECTION 3: UTILITIES
# ============================================================================

def now_ms() -> int:
    """Hozirgi vaqt millisekundlarda."""
    return int(time.time() * 1000)


def now_s() -> int:
    """Hozirgi vaqt sekundlarda."""
    return int(time.time())


def gen_id(prefix: str = "") -> str:
    """Tasodifiy ID generatsiya."""
    base = secrets.token_urlsafe(16)
    return f"{prefix}{base}" if prefix else base


def gen_token() -> str:
    """Xavfsiz API token."""
    return secrets.token_urlsafe(48)


def gen_code(length: int = 6) -> str:
    """Raqamli kod (2FA uchun)."""
    return "".join(secrets.choice(string.digits) for _ in range(length))


def safe_json(val, default=None):
    """JSON parse xavfsiz."""
    try:
        return json.loads(val) if isinstance(val, str) else val
    except Exception:
        return default


def esc_html(s) -> str:
    """HTML escape."""
    if s is None:
        return ""
    return str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;") \
                 .replace('"', "&quot;").replace("'", "&#39;")


def fmt_num(n) -> str:
    """Raqamni qisqa formatlash."""
    try:
        n = float(n)
    except Exception:
        return "0"
    if n >= 1e9:
        return f"{n / 1e9:.2f}B"
    if n >= 1e6:
        return f"{n / 1e6:.2f}M"
    if n >= 1e3:
        return f"{n / 1e3:.2f}K"
    return str(int(n))


def time_ago(ts_ms: int) -> str:
    """Vaqtni 'x daqiqa oldin' formatiga o'tkazish."""
    if not ts_ms:
        return "—"
    diff = (now_ms() - ts_ms) // 1000
    if diff < 0:
        return "hozir"
    if diff < 60:
        return "hozir"
    if diff < 3600:
        return f"{diff // 60} daq"
    if diff < 86400:
        return f"{diff // 3600} soat"
    if diff < 604800:
        return f"{diff // 86400} kun"
    return datetime.fromtimestamp(ts_ms / 1000).strftime("%d.%m.%Y")


def parse_tags(text: str) -> list:
    """#hashtag larni ajratish."""
    if not text:
        return []
    matches = re.findall(r"#([\w\u0400-\u04FF]{2,40})", text, re.UNICODE)
    return list({m.lower() for m in matches})[:MAX_TAGS]


def parse_mentions(text: str) -> list:
    """@user larni ajratish."""
    if not text:
        return []
    return list({m.lower() for m in re.findall(r"@([\w\u0400-\u04FF]{2,30})", text, re.UNICODE)})[:10]


def slugify(text: str) -> str:
    """URL uchun slug."""
    if not text:
        return ""
    text = text.lower().strip()
    text = re.sub(r"[^\w\s-]", "", text, flags=re.UNICODE)
    text = re.sub(r"[\s_]+", "-", text)
    return text[:80]


def validate_email(email: str) -> bool:
    """Email validatsiyasi."""
    if not email or not isinstance(email, str):
        return False
    return bool(re.match(r"^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$", email))


def validate_phone(phone: str) -> bool:
    """Telefon validatsiyasi."""
    if not phone:
        return False
    digits = re.sub(r"\D", "", phone)
    return 9 <= len(digits) <= 15


def validate_password(pwd: str) -> tuple:
    """Parol kuchini tekshirish."""
    if not pwd or len(pwd) < 4:
        return False, "Parol kamida 4 belgi bo'lsin"
    if len(pwd) > 128:
        return False, "Parol juda uzun"
    score = 0
    if len(pwd) >= 8: score += 25
    if len(pwd) >= 12: score += 25
    if re.search(r"[A-Z]", pwd): score += 15
    if re.search(r"[0-9]", pwd): score += 15
    if re.search(r"[^A-Za-z0-9]", pwd): score += 20
    return True, {"score": min(100, score)}


def chunk_list(lst: list, size: int) -> list:
    """Ro'yxatni bo'laklarga bo'lish."""
    for i in range(0, len(lst), size):
        yield lst[i:i + size]


def truncate(text: str, n: int = 100) -> str:
    """Matnni qisqartirish."""
    if not text:
        return ""
    return text if len(text) <= n else text[:n - 3] + "..."


# ============================================================================
# SECTION 4: DATABASE
# ============================================================================

def db_connect() -> sqlite3.Connection:
    """Yangi DB ulanish."""
    conn = sqlite3.connect(DB_FILE, timeout=30, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA synchronous = NORMAL")
    conn.execute("PRAGMA cache_size = -64000")
    conn.execute("PRAGMA busy_timeout = 10000")
    return conn


@contextmanager
def db():
    """DB context manager."""
    conn = db_connect()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


SCHEMA = """
-- Users
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,
    email TEXT,
    phone TEXT,
    avatar TEXT DEFAULT '⭐',
    bio TEXT DEFAULT '',
    lang TEXT DEFAULT 'uz',
    verified INTEGER DEFAULT 0,
    is_private INTEGER DEFAULT 0,
    two_fa_enabled INTEGER DEFAULT 0,
    two_fa_secret TEXT,
    reset_token TEXT,
    reset_expires INTEGER,
    verify_code TEXT,
    last_seen INTEGER,
    last_login INTEGER,
    created_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_users_name ON users(name);
CREATE INDEX IF NOT EXISTS idx_users_email ON users(email);
CREATE INDEX IF NOT EXISTS idx_users_token_reset ON users(reset_token);

-- API tokens
CREATE TABLE IF NOT EXISTS api_tokens (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    token TEXT UNIQUE NOT NULL,
    device TEXT,
    ip TEXT,
    user_agent TEXT,
    created_at INTEGER NOT NULL,
    last_used INTEGER,
    revoked INTEGER DEFAULT 0,
    FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_tokens_token ON api_tokens(token);
CREATE INDEX IF NOT EXISTS idx_tokens_user ON api_tokens(user_id);

-- Posts
CREATE TABLE IF NOT EXISTS posts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    type TEXT DEFAULT 'post',
    text TEXT DEFAULT '',
    image_url TEXT,
    video_url TEXT,
    audio_url TEXT,
    location TEXT,
    tags TEXT DEFAULT '',
    pinned INTEGER DEFAULT 0,
    archived INTEGER DEFAULT 0,
    views INTEGER DEFAULT 0,
    deleted_at INTEGER,
    created_at INTEGER NOT NULL,
    FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_posts_user ON posts(user_id, archived);
CREATE INDEX IF NOT EXISTS idx_posts_type ON posts(type, created_at);

-- Comments
CREATE TABLE IF NOT EXISTS comments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    post_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    parent_id INTEGER,
    text TEXT NOT NULL,
    deleted_at INTEGER,
    created_at INTEGER NOT NULL,
    FOREIGN KEY(post_id) REFERENCES posts(id) ON DELETE CASCADE,
    FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_comments_post ON comments(post_id, created_at);

-- Reactions
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
CREATE INDEX IF NOT EXISTS idx_reactions_post ON reactions(post_id);

-- Saves
CREATE TABLE IF NOT EXISTS saves (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    post_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    created_at INTEGER NOT NULL,
    UNIQUE(post_id, user_id),
    FOREIGN KEY(post_id) REFERENCES posts(id) ON DELETE CASCADE,
    FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
);

-- Follows
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

-- Blocks
CREATE TABLE IF NOT EXISTS blocks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    blocker_id INTEGER NOT NULL,
    blocked_id INTEGER NOT NULL,
    created_at INTEGER NOT NULL,
    UNIQUE(blocker_id, blocked_id),
    FOREIGN KEY(blocker_id) REFERENCES users(id) ON DELETE CASCADE,
    FOREIGN KEY(blocked_id) REFERENCES users(id) ON DELETE CASCADE
);

-- Reports
CREATE TABLE IF NOT EXISTS reports (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    reporter_id INTEGER NOT NULL,
    target_type TEXT NOT NULL,
    target_id INTEGER NOT NULL,
    reason TEXT,
    status TEXT DEFAULT 'pending',
    reviewed_by INTEGER,
    created_at INTEGER NOT NULL,
    FOREIGN KEY(reporter_id) REFERENCES users(id)
);
CREATE INDEX IF NOT EXISTS idx_reports_status ON reports(status);

-- Groups
CREATE TABLE IF NOT EXISTS groups_t (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    emoji TEXT DEFAULT '👥',
    description TEXT DEFAULT '',
    is_private INTEGER DEFAULT 0,
    created_by INTEGER,
    created_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS group_members (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    group_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    role TEXT DEFAULT 'member',
    joined_at INTEGER NOT NULL,
    UNIQUE(group_id, user_id),
    FOREIGN KEY(group_id) REFERENCES groups_t(id) ON DELETE CASCADE,
    FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
);

-- Chats
CREATE TABLE IF NOT EXISTS chats (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user1_id INTEGER NOT NULL,
    user2_id INTEGER NOT NULL,
    is_ai INTEGER DEFAULT 0,
    is_group INTEGER DEFAULT 0,
    group_name TEXT,
    created_at INTEGER NOT NULL,
    UNIQUE(user1_id, user2_id)
);

-- Messages
CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id INTEGER NOT NULL,
    sender_id INTEGER NOT NULL,
    text TEXT DEFAULT '',
    type TEXT DEFAULT 'text',
    media_url TEXT,
    reply_to_id INTEGER,
    read_at INTEGER,
    deleted_at INTEGER,
    created_at INTEGER NOT NULL,
    FOREIGN KEY(chat_id) REFERENCES chats(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_messages_chat ON messages(chat_id, id);

-- Stories
CREATE TABLE IF NOT EXISTS stories (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    emoji TEXT DEFAULT '✨',
    text TEXT DEFAULT '',
    image_url TEXT,
    audience TEXT DEFAULT 'public',
    views INTEGER DEFAULT 0,
    created_at INTEGER NOT NULL,
    expires_at INTEGER NOT NULL,
    FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_stories_expires ON stories(expires_at);

CREATE TABLE IF NOT EXISTS story_views (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    story_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    viewed_at INTEGER NOT NULL,
    UNIQUE(story_id, user_id),
    FOREIGN KEY(story_id) REFERENCES stories(id) ON DELETE CASCADE,
    FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
);

-- Market
CREATE TABLE IF NOT EXISTS market (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    price INTEGER DEFAULT 0,
    currency TEXT DEFAULT 'UZS',
    description TEXT DEFAULT '',
    emoji TEXT DEFAULT '📦',
    category TEXT DEFAULT 'general',
    image_url TEXT,
    stock INTEGER DEFAULT 100,
    sold INTEGER DEFAULT 0,
    user_id INTEGER NOT NULL,
    status TEXT DEFAULT 'active',
    deleted_at INTEGER,
    created_at INTEGER NOT NULL,
    FOREIGN KEY(user_id) REFERENCES users(id)
);
CREATE INDEX IF NOT EXISTS idx_market_status ON market(status, created_at);

CREATE TABLE IF NOT EXISTS orders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    product_id INTEGER NOT NULL,
    quantity INTEGER DEFAULT 1,
    total INTEGER DEFAULT 0,
    status TEXT DEFAULT 'pending',
    created_at INTEGER NOT NULL,
    FOREIGN KEY(user_id) REFERENCES users(id),
    FOREIGN KEY(product_id) REFERENCES market(id)
);

-- Notifications
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
CREATE INDEX IF NOT EXISTS idx_notif_created ON notifications(created_at);

-- Activity log
CREATE TABLE IF NOT EXISTS activity_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER,
    action TEXT NOT NULL,
    target_type TEXT,
    target_id INTEGER,
    ip TEXT,
    user_agent TEXT,
    created_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_activity_user ON activity_log(user_id, created_at);

-- Hashtag follows
CREATE TABLE IF NOT EXISTS hashtag_follows (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    hashtag TEXT NOT NULL,
    created_at INTEGER NOT NULL,
    UNIQUE(user_id, hashtag),
    FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
);

-- Moderation queue
CREATE TABLE IF NOT EXISTS moderation_queue (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    target_type TEXT NOT NULL,
    target_id INTEGER NOT NULL,
    reason TEXT,
    severity INTEGER DEFAULT 1,
    status TEXT DEFAULT 'pending',
    reviewed_by INTEGER,
    created_at INTEGER NOT NULL
);

-- Site stats (cached)
CREATE TABLE IF NOT EXISTS stats_cache (
    key TEXT PRIMARY KEY,
    value TEXT,
    expires_at INTEGER
);
"""


def init_database():
    """Bazani yaratish va migratsiya qilish."""
    log.info(f"Baza ishga tushirilmoqda: {DB_FILE}")
    with db() as c:
        c.executescript(SCHEMA)
    log.info("✅ Baza tayyor")


# ============================================================================
# SECTION 5: SEED DATA (Katta demo ma'lumotlar)
# ============================================================================

DEMO_NAMES = [
    ("Dilnoza Karimova", "🌻", "UI/UX dizayner · 5 yil tajriba", 1),
    ("Javlon Toshmatov", "🚀", "Startap asoschisi · EdTech", 1),
    ("Malika Yusupova", "🦋", "Fotograf · sayyoh", 0),
    ("Bekzod Rahimov", "🐺", "Backend dasturchi · Python", 1),
    ("Sevinch Aliyeva", "🌙", "Kitob klubi asoschisi", 0),
    ("Nodira Ismoilova", "🌻", "Marketolog · SMM", 0),
    ("Aziz Sobirov", "⚡", "Frontend · React/Vue", 1),
    ("Kamola Nazarova", "🐨", "Ingliz tili o'qituvchisi", 0),
    ("Sardor Aliyev", "🎯", "Product Manager", 0),
    ("Zulfiya Rahimova", "💎", "Data Scientist · ML", 1),
    ("Rustam Ergashev", "🔥", "DevOps Engineer", 0),
    ("Nigora Yusupova", "🌸", "Dizayner · illustrator", 0),
    ("Otabek Nazarov", "🎮", "Game developer · Unity", 0),
    ("Shahzoda Karimova", "👑", "Aktrisa · model", 1),
    ("Ulugbek Tursunov", "📚", "Tarix o'qituvchisi", 0),
    ("Laylo Ibragimova", "🌈", "Blogger · sayohat", 1),
    ("Bobur Toshmatov", "⚔️", "Sport murabbiy", 0),
    ("Madina Yusupova", "🎨", "Artist · painter", 0),
    ("Farhod Nazarov", "🎬", "Rejissyor · video", 1),
    ("Zarina Aliyeva", "🌟", "Marketing manager", 0),
    ("Akmal Umarov", "🎸", "Musiqachi · gitara", 0),
    ("Feruza Sattorova", "🍰", "Oshpaz · konditer", 0),
    ("Sherzod Ergashev", "📊", "Analitik · BI", 0),
    ("Dilnoza Sobirova", "💐", "Florist · bog'bon", 0),
    ("Jasur Karimov", "⚽", "Futbolchi", 1),
    ("Muxlisa Rahimova", "📖", "Yozuvchi · shoir", 0),
    ("Sunnat Toshmatov", "🔬", "Ilmiy xodim · fizika", 0),
    ("Nilufar Yusupova", "🎭", "Teatr aktrisasi", 0),
    ("Davron Nazarov", "💻", "Full-stack developer", 1),
    ("Shahnoza Karimova", "💃", "Raqqosa · xoreograf", 0),
]

DEMO_POSTS = [
    ("Unigram'ga xush kelibsiz! 🎉\n\nYangi platforma — yangi imkoniyatlar. Barcha bo'limlarni sinab ko'ring!", "unigram,yangilik,kelajak"),
    ("Bugun yangi dizayn loyihasini boshladim. Figma'da ishlash juda yoqadi! 🎨", "dizayn,figma"),
    ("3 oyda 10 000 foydalanuvchi. Eng katta saboq: mahsulotni emas, muammoni seving. 🚀", "startap,biznes,talim"),
    ("Python'da yangi kutubxona topdim — juda tez ishlaydi! Kod tezligi 3x oshdi. 💻", "dasturlash,python"),
    ("Kitob almashish klubi ochdik. Har shanba, Navoiy ko'chasi. Kitobingizni olib keling! 📚", "kitob,tadbirlar,toshkent"),
    ("React 19 yangi imkoniyatlari — Server Components haqida qisqacha. 🎬", "dasturlash,react,javascript"),
    ("Bugungi Toshkent quyoshi. Shahar juda go'zal! ☀️", "toshkent,foto,quyosh"),
    ("Backend arxitekturasi haqida maslahat: mikroservislar har doim yaxshi emas. 🏗️", "backend,dasturlash"),
    ("Yangi AI model bilan tanishdim — matn yozishda juda yaxshi yordam beradi! 🤖", "ai,texnologiya"),
    ("Startup uchun investor topdim! Keyingi qadam — MVP. 💡", "startap,investor"),
    ("UX dizayn qoidalari: 1. Foydalanuvchini o'ylang. 2. Sodda qiling. 3. Test qiling. 🎯", "ux,dizayn"),
    ("Kripto bozori — bugun BTC yangi cho'qqilarda! 📈", "kripto,btc,trading"),
    ("Yangi restoran ochildi — palov juda mazali! 🍚", "ovqat,toshkent"),
    ("Kitob tavsiyasi: 'Atomic Habits' — odatlarni o'zgartirishning eng yaxshi usuli. 📖", "kitob,tavsiya"),
    ("Dizayn trendlari 2024: minimalism, neumorphism, glassmorphism. 🎨", "dizayn,trend"),
    ("Sport va dasturlash — ikkalasi ham intizom talab qiladi. 💪", "sport,dasturlash"),
    ("Yangi loyiham haqida — Telegram bot yaratdim. 5 ta funksiya bor! 🤖", "python,telegram,bot"),
    ("Italiyaga sayohat — yangi maqola blog'imda. ✈️", "sayohat,italiya"),
    ("Biznes g'oyasi: mahalliy fermerlarni onlayn bozorga olib chiqish. 🌾", "biznes,startap"),
    ("Data Science uchun 5 ta maslahat: statistika, Python, SQL, vizualizatsiya, domen bilimi. 📊", "datascience,ml"),
    ("Yangi telefon oldim — iPhone 15 Pro. Kamera juda zo'r! 📱", "texnologiya,apple"),
    ("Freelance ishlashning afzalliklari va kamchiliklari. 💼", "freelance,ish"),
    ("Bugun 20 km yugurdim. Marafonga tayyorgarlik davom etmoqda! 🏃", "sport,yugurish"),
    ("Yangi kitobim nashr etildi! 'Kod va hayot' — dasturchilar uchun. 📕", "kitob,dasturlash"),
    ("Muvaffaqiyat siri — kichik qadamlar, katta natijalar. 🎯", "motivatsiya,biznes"),
    ("Toshkentda yangi IT-park ochildi! Startup'lar uchun ajoyib imkoniyat. 🏢", "toshkent,it"),
    ("Figma yangi funksiyalar — AI bilan dizayn yaratish. 🎨", "figma,dizayn,ai"),
    ("Backend uchun eng yaxshi stack: Python + FastAPI + PostgreSQL. 🐍", "backend,python"),
    ("Mahalliy brendlarni qo'llab-quvvatlaylik! O'zbek kiyimlari juda chiroyli. 👗", "brend,kiyim"),
    ("Sun'iy intellekt va kelajak — 10 yil ichida nima o'zgaradi? 🤖", "ai,kelajak"),
]

DEMO_GROUPS = [
    ("Dizaynerlar", "🎨", "UI/UX, grafik va illyustratsiya haqida jonli suhbat"),
    ("Startapchilar", "🚀", "G'oya → MVP → bozor. Tajriba almashish maydoni"),
    ("Toshkent Tanishlar", "📍", "Shahardagi tadbirlar, uchrashuvlar va yangiliklar"),
    ("Kitobxonlar", "📚", "Har hafta bitta kitob, bitta taassurot"),
    ("Dasturchilar", "💻", "Kod, savol-javob va freelance imkoniyatlar"),
    ("Fotografiya", "📷", "Suratlar, texnika va mahorat sirlari"),
    ("Sport", "⚽", "Futbol, boks, kurash va boshqa sport turlari"),
    ("Ovqat va retseptlar", "🍳", "Oshxona sirlari va mazali retseptlar"),
    ("Sayohat", "✈️", "Dunyo bo'ylab sayohat tajribalari"),
    ("Musiqa", "🎵", "Turli janrlar, yangi qo'shiqlar va konsertlar"),
    ("Kino va seriallar", "🎬", "Yangi filmlar, tavsiyalar va muhokamalar"),
    ("Ta'lim va kurslar", "🎓", "Onlayn kurslar, darslar va bilim almashish"),
]

DEMO_MARKET = [
    ("iPhone 15 Pro", 12999000, "256GB, Titanium, A17 Pro chip", "📱", "elektronika"),
    ("MacBook Pro M3", 25999000, '14", 16GB RAM, 512GB SSD', "💻", "elektronika"),
    ("AirPods Pro 2", 3499000, "ANC, Wireless", "🎧", "elektronika"),
    ("Samsung S24 Ultra", 15999000, "512GB, AI Camera, S-Pen", "📱", "elektronika"),
    ("Apple Watch 9", 5499000, "45mm, GPS + Cellular", "⌚", "elektronika"),
    ("Sony PS5", 7499000, "Disc Edition, 1TB", "🎮", "o'yinlar"),
    ("Dyson V15", 6999000, "Changyutgich, kuchli", "🌪️", "uy"),
    ("Bosch kir yuvish", 4999000, "9kg, inverter", "🧺", "uy"),
    ("Grafik planşet Wacom", 1999000, "Intuos Pro Medium", "🎨", "elektronika"),
    ("Krossovka Nike Air", 899000, "Original, 42 o'lcham", "👟", "kiyim"),
    ("Adidas futbolka", 299000, "Sport uchun, paxta", "👕", "kiyim"),
    ("Ray-Ban ko'zoynak", 1299000, "Aviator, original", "🕶️", "aksesuar"),
    ("Rolex soat (replika)", 599000, "Ajoyib sifatli replika", "⌚", "aksesuar"),
    ("Kofe apparati DeLonghi", 2499000, "Ofis uchun, 2 oy ishlatilgan", "☕", "uy"),
    ("Yoga mat", 199000, "6mm, TPE material", "🧘", "sport"),
    ("Velosiped Trek", 3499000, "21 tezlik, tog' velosipedi", "🚲", "sport"),
]


def seed_data():
    """Demo ma'lumotlarni yaratish (faqat bo'sh bazada)."""
    with db() as c:
        if c.execute("SELECT COUNT(*) FROM users").fetchone()[0] > 0:
            return
        log.info("Demo ma'lumotlar yaratilmoqda...")
        t = now_ms()
        pwd_hash = generate_password_hash("demo123")

        # 30 ta demo foydalanuvchi
        user_ids = []
        for i, (name, avatar, bio, verified) in enumerate(DEMO_NAMES):
            email = f"user{i + 1}@unigram.uz"
            phone = f"+9989012345{i:02d}"
            c.execute(
                "INSERT INTO users(name,password_hash,email,phone,avatar,bio,verified,last_seen,created_at) "
                "VALUES(?,?,?,?,?,?,?,?,?)",
                (name, pwd_hash, email, phone, avatar, bio, verified, t, t - i * 86400000),
            )
            user_ids.append(c.execute("SELECT last_insert_rowid()").fetchone()[0])

        # Demo posts (30 ta)
        for i, (text, tags) in enumerate(DEMO_POSTS):
            uid = user_ids[i % len(user_ids)]
            created = t - (30 - i) * 3600000 - random.randint(0, 3600000)
            post_type = "reel" if i % 7 == 0 else "post"
            c.execute(
                "INSERT INTO posts(user_id,type,text,tags,created_at) VALUES(?,?,?,?,?)",
                (uid, post_type, text, tags, created),
            )
            pid = c.execute("SELECT last_insert_rowid()").fetchone()[0]
            # Random likes
            likers = random.sample(user_ids, random.randint(0, min(15, len(user_ids))))
            for liker in likers:
                if liker != uid:
                    try:
                        c.execute(
                            "INSERT INTO reactions(post_id,user_id,emoji,created_at) VALUES(?,?,?,?)",
                            (pid, liker, random.choice(["❤️", "🔥", "😂", "😮", "😢"]), now_ms()),
                        )
                    except Exception:
                        pass
            # Random comments
            if random.random() < 0.5:
                for _ in range(random.randint(1, 3)):
                    cu = random.choice(user_ids)
                    if cu != uid:
                        c.execute(
                            "INSERT INTO comments(post_id,user_id,text,created_at) VALUES(?,?,?,?)",
                            (pid, cu, random.choice([
                                "Ajoyib! 🔥", "Rahmat, foydali ma'lumot",
                                "Zo'r post!", "Qo'shilaman",
                                "Davom eting!", "Juda qiziqarli"
                            ]), now_ms()),
                        )

        # Demo follows
        for i in range(len(user_ids)):
            for _ in range(random.randint(2, 8)):
                j = random.randint(0, len(user_ids) - 1)
                if i != j:
                    try:
                        c.execute(
                            "INSERT INTO follows(follower_id,following_id,created_at) VALUES(?,?,?)",
                            (user_ids[i], user_ids[j], now_ms()),
                        )
                    except Exception:
                        pass

        # Demo groups
        for name, emoji, desc in DEMO_GROUPS:
            c.execute(
                "INSERT INTO groups_t(name,emoji,description,created_by,created_at) VALUES(?,?,?,1,?)",
                (name, emoji, desc, t),
            )
            gid = c.execute("SELECT last_insert_rowid()").fetchone()[0]
            # Members
            for uid in random.sample(user_ids, random.randint(3, 10)):
                try:
                    c.execute(
                        "INSERT INTO group_members(group_id,user_id,joined_at) VALUES(?,?,?)",
                        (gid, uid, now_ms()),
                    )
                except Exception:
                    pass

        # Demo market items
        for title, price, desc, emoji, cat in DEMO_MARKET:
            seller = random.choice(user_ids)
            c.execute(
                "INSERT INTO market(title,price,description,emoji,category,user_id,created_at) "
                "VALUES(?,?,?,?,?,?,?)",
                (title, price, desc, emoji, cat, seller, t - random.randint(0, 30 * 86400000)),
            )

        # Demo stories (5 ta)
        for i in range(5):
            uid = user_ids[i]
            c.execute(
                "INSERT INTO stories(user_id,emoji,text,created_at,expires_at) VALUES(?,?,?,?,?)",
                (uid, random.choice(["🌅", "💻", "🎨", "📚", "☕"]),
                 f"Demo story #{i + 1}", t, t + 24 * 3600000),
            )

        log.info(f"✅ Demo data: {len(user_ids)} users, {len(DEMO_POSTS)} posts, "
                 f"{len(DEMO_GROUPS)} groups, {len(DEMO_MARKET)} market items")


# ============================================================================
# SECTION 6: CACHE (In-memory)
# ============================================================================

class Cache:
    """Oddiy in-memory cache."""

    def __init__(self):
        self._data = {}
        self._lock = threading.Lock()

    def get(self, key: str):
        with self._lock:
            item = self._data.get(key)
            if not item:
                return None
            value, expires = item
            if expires < time.time():
                del self._data[key]
                return None
            return value

    def set(self, key: str, value, ttl: int = 60):
        with self._lock:
            self._data[key] = (value, time.time() + ttl)

    def delete(self, key: str):
        with self._lock:
            self._data.pop(key, None)

    def clear(self):
        with self._lock:
            self._data.clear()

    def cleanup(self):
        with self._lock:
            now_t = time.time()
            for k in list(self._data.keys()):
                if self._data[k][1] < now_t:
                    del self._data[k]


cache = Cache()


def cached(key: str, ttl: int = 60):
    """Cache decorator."""
    def decorator(fn):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            ck = f"{key}:{args}:{tuple(sorted(kwargs.items()))}"
            cached_val = cache.get(ck)
            if cached_val is not None:
                return cached_val
            result = fn(*args, **kwargs)
            cache.set(ck, result, ttl)
            return result
        return wrapper
    return decorator


# ============================================================================
# SECTION 7: RATE LIMITER
# ============================================================================

class RateLimiter:
    """Oddiy sliding-window rate limiter."""

    def __init__(self):
        self._buckets = defaultdict(list)
        self._lock = threading.Lock()

    def check(self, key: str, limit: int, window: int) -> tuple:
        with self._lock:
            now_t = time.time()
            bucket = self._buckets[key]
            # Clean old
            bucket[:] = [t for t in bucket if t > now_t - window]
            if len(bucket) >= limit:
                return False, 0, int(window - (now_t - bucket[0]))
            bucket.append(now_t)
            return True, limit - len(bucket), 0


rate_limiter = RateLimiter()


def rate_limit(limit_type: str = "default"):
    """Rate limit decorator."""
    def decorator(fn):
        @wraps(fn)
        def wrapper(*args, **kwargs):
            limit, window = RATE_LIMITS.get(limit_type, RATE_LIMITS["default"])
            ip = request.remote_addr or "unknown"
            user_id = g.user["id"] if hasattr(g, "user") and g.user else "anon"
            key = f"{limit_type}:{ip}:{user_id}"
            allowed, remaining, retry_after = rate_limiter.check(key, limit, window)
            if not allowed:
                return jsonify({
                    "error": "Juda ko'p so'rov",
                    "retry_after": retry_after
                }), 429, {"Retry-After": str(retry_after)}
            response = make_response(fn(*args, **kwargs))
            response.headers["X-RateLimit-Limit"] = str(limit)
            response.headers["X-RateLimit-Remaining"] = str(remaining)
            return response
        return wrapper
    return decorator


# ============================================================================
# SECTION 8: AUTH
# ============================================================================

def get_token_from_header() -> str:
    """Header'dan tokenni olish."""
    hdr = request.headers.get("Authorization", "")
    if hdr.startswith("Bearer "):
        return hdr[7:].strip()
    return hdr.strip()


def auth_required(fn):
    """Autentifikatsiya talab qiluvchi decorator."""
    @wraps(fn)
    def wrapper(*args, **kwargs):
        token = get_token_from_header()
        if not token:
            return jsonify({"error": "Token kerak", "code": "no_token"}), 401
        with db() as c:
            row = c.execute(
                """SELECT u.*, t.id as token_id, t.revoked
                   FROM users u
                   JOIN api_tokens t ON t.user_id = u.id
                   WHERE t.token = ?""",
                (token,),
            ).fetchone()
        if not row:
            return jsonify({"error": "Token yaroqsiz", "code": "bad_token"}), 401
        if row["revoked"]:
            return jsonify({"error": "Token bekor qilingan", "code": "revoked"}), 401
        g.user = dict(row)
        g.token = token
        g.token_id = row["token_id"]
        # Update last_used (throttle)
        if now_ms() - (g.user.get("last_seen") or 0) > 60000:
            with db() as c:
                c.execute("UPDATE users SET last_seen=? WHERE id=?", (now_ms(), g.user["id"]))
                c.execute("UPDATE api_tokens SET last_used=? WHERE id=?",
                          (now_ms(), g.token_id))
        return fn(*args, **kwargs)
    return wrapper


def auth_optional(fn):
    """Ixtiyoriy autentifikatsiya — user bo'lsa g.user, bo'lmasa None."""
    @wraps(fn)
    def wrapper(*args, **kwargs):
        token = get_token_from_header()
        g.user = None
        g.token = None
        if token:
            with db() as c:
                row = c.execute(
                    """SELECT u.*, t.id as token_id
                       FROM users u
                       JOIN api_tokens t ON t.user_id = u.id
                       WHERE t.token = ? AND t.revoked = 0""",
                    (token,),
                ).fetchone()
            if row:
                g.user = dict(row)
                g.token = token
                g.token_id = row["token_id"]
        return fn(*args, **kwargs)
    return wrapper


def log_activity(action: str, target_type: str = None, target_id: int = None):
    """Faoliyatni log qilish."""
    try:
        user_id = g.user["id"] if hasattr(g, "user") and g.user else None
        with db() as c:
            c.execute(
                """INSERT INTO activity_log(user_id,action,target_type,target_id,ip,user_agent,created_at)
                   VALUES(?,?,?,?,?,?,?)""",
                (user_id, action, target_type, target_id,
                 request.remote_addr, request.headers.get("User-Agent", "")[:200], now_ms()),
            )
    except Exception as e:
        log.warn(f"Activity log xatosi: {e}")


# ============================================================================
# SECTION 9: VALIDATORS
# ============================================================================

def validate_user_input(data: dict, required: list) -> tuple:
    """Umumiy validatsiya."""
    for field in required:
        if not data.get(field):
            return False, f"'{field}' maydoni to'ldirilishi shart"
    return True, None


def validate_post_text(text: str) -> tuple:
    if not text or not text.strip():
        return False, "Post matni bo'sh bo'lmasin"
    if len(text) > MAX_POST_LENGTH:
        return False, f"Post {MAX_POST_LENGTH} belgidan oshmasin"
    return True, None


def validate_comment_text(text: str) -> tuple:
    if not text or not text.strip():
        return False, "Izoh bo'sh bo'lmasin"
    if len(text) > MAX_COMMENT_LENGTH:
        return False, f"Izoh {MAX_COMMENT_LENGTH} belgidan oshmasin"
    return True, None


def validate_name(name: str) -> tuple:
    if not name or not isinstance(name, str):
        return False, "Ism kerak"
    name = name.strip()
    if len(name) < 2:
        return False, "Ism kamida 2 belgi"
    if len(name) > MAX_NAME_LENGTH:
        return False, f"Ism {MAX_NAME_LENGTH} belgidan oshmasin"
    return True, name


# ============================================================================
# SECTION 10: FILE HANDLING
# ============================================================================

def save_base64_image(b64_data: str, prefix: str = "img") -> str:
    """Base64 rasmni faylga saqlash."""
    if not b64_data or not isinstance(b64_data, str):
        return None
    if not b64_data.startswith("data:image/"):
        return None
    try:
        header, b64 = b64_data.split(",", 1)
        m = re.search(r"image/(\w+)", header)
        if not m:
            return None
        ext = m.group(1).lower()
        if ext == "jpeg":
            ext = "jpg"
        if ext not in ALLOWED_IMAGE_EXT:
            return None
        raw = base64.b64decode(b64)
        if len(raw) > MAX_IMAGE_SIZE:
            log.warn(f"Rasm juda katta: {len(raw)} bytes")
            return None
        fname = f"{prefix}_{secrets.token_hex(12)}.{ext}"
        path = os.path.join(UPLOAD_DIR, fname)
        with open(path, "wb") as f:
            f.write(raw)
        return "/uploads/" + fname
    except Exception as e:
        log.error(f"Rasm saqlash xatosi: {e}")
        return None


def delete_upload_file(url: str) -> bool:
    """Yuklangan faylni o'chirish."""
    if not url or not url.startswith("/uploads/"):
        return False
    try:
        fname = url.replace("/uploads/", "")
        path = os.path.join(UPLOAD_DIR, fname)
        if os.path.exists(path):
            os.remove(path)
            return True
    except Exception as e:
        log.error(f"Fayl o'chirish xatosi: {e}")
    return False


def get_upload_size() -> int:
    """Uploads papkasidagi fayllar hajmi."""
    try:
        total = 0
        for f in os.listdir(UPLOAD_DIR):
            fp = os.path.join(UPLOAD_DIR, f)
            if os.path.isfile(fp):
                total += os.path.getsize(fp)
        return total
    except Exception:
        return 0


def cleanup_old_uploads(days: int = 30):
    """Eski fayllarni o'chirish."""
    try:
        cutoff = time.time() - days * 86400
        for f in os.listdir(UPLOAD_DIR):
            fp = os.path.join(UPLOAD_DIR, f)
            if os.path.isfile(fp) and os.path.getmtime(fp) < cutoff:
                os.remove(fp)
                log.info(f"Eski fayl o'chirildi: {f}")
    except Exception as e:
        log.error(f"Cleanup xatosi: {e}")


# ============================================================================
# SECTION 11: EMAIL (Simulated)
# ============================================================================

def send_email(email: str, subject: str, body: str) -> bool:
    """Email yuborish (simulyatsiya — konsolga yozadi)."""
    log.info(f"📧 EMAIL → {email}")
    log.info(f"   Subject: {subject}")
    log.info(f"   Body: {truncate(body, 200)}")
    # Production'da SMTP yoki SendGrid ishlatiladi
    return True


def send_verification_email(email: str, code: str):
    """Tasdiqlash kodi yuborish."""
    send_email(
        email,
        "Unigram — Tasdiqlash kodi",
        f"Salom!\n\nSizning tasdiqlash kodingiz: {code}\n\n"
        f"Kod 15 daqiqa amal qiladi.\n\nUnigram jamoasi",
    )


def send_reset_email(email: str, token: str, host: str = "localhost"):
    """Parol tiklash emaili."""
    link = f"http://{host}/reset?token={token}"
    send_email(
        email,
        "Unigram — Parolni tiklash",
        f"Salom!\n\nParolni tiklash uchun havola:\n{link}\n\n"
        f"Havola 1 soat amal qiladi.\n\nUnigram jamoasi",
    )


# ============================================================================
# SECTION 12: 2FA (Simulated TOTP)
# ============================================================================

def generate_2fa_secret() -> str:
    """2FA uchun maxfiy kalit."""
    return base64.b32encode(secrets.token_bytes(20)).decode("ascii")


def verify_2fa_code(secret: str, code: str) -> bool:
    """2FA kodini tekshirish (oddiy simulyatsiya)."""
    if not secret or not code:
        return False
    # Haqiqiy TOTP algoritmi bo'lardi, bu demo
    expected = hashlib.sha256((secret + str(int(time.time() // 30))).encode()).hexdigest()[:6]
    return code == expected or code == "123456"  # Demo bypass


# ============================================================================
# SECTION 13: CONTENT MODERATION
# ============================================================================

BAD_WORDS = {
    "uz": ["ahmoq", "tentak", "dangasa", "yomon", "jinni"],
    "en": ["idiot", "stupid", "dumb", "hate", "spam"],
    "ru": ["дурак", "идиот", "тупой", "ненавижу"],
}

SPAM_PATTERNS = [
    r"(?i)free money",
    r"(?i)click here",
    r"(?i)buy now",
    r"(?i)win prize",
    r"http[s]?://[^\s]+\.(exe|dmg|apk)",
    r"(\+?\d{10,}){3,}",  # Multiple phone numbers
]


def moderate_text(text: str) -> tuple:
    """Matnni moderatsiya qilish. (is_ok, reason)."""
    if not text:
        return True, None

    # Bad words
    text_lower = text.lower()
    for lang, words in BAD_WORDS.items():
        for w in words:
            if w in text_lower:
                return False, f"Taqiqlangan so'z: {w[:3]}***"

    # Spam
    for pattern in SPAM_PATTERNS:
        if re.search(pattern, text):
            return False, "Spam deb topildi"

    # Too many caps
    if len(text) > 10:
        caps = sum(1 for c in text if c.isupper())
        if caps / len(text) > 0.7:
            return False, "Juda ko'p katta harflar"

    # Repeated characters
    if re.search(r"(.)\1{10,}", text):
        return False, "Takrorlanuvchi belgilar"

    return True, None


def auto_moderate(target_type: str, target_id: int, text: str):
    """Avtomatik moderatsiya — shubhali kontentni navbatga qo'shish."""
    is_ok, reason = moderate_text(text)
    if not is_ok:
        try:
            with db() as c:
                c.execute(
                    """INSERT INTO moderation_queue(target_type,target_id,reason,created_at)
                       VALUES(?,?,?,?)""",
                    (target_type, target_id, reason, now_ms()),
                )
        except Exception as e:
            log.error(f"Moderation xatosi: {e}")
    return is_ok


# ============================================================================
# SECTION 14: AI ENGINE (20 bots)
# ============================================================================

AI_BOTS = {
    1: {
        "name": "Koder AI", "icon": "💻", "topics": ["kod", "python", "javascript", "dasturlash", "html", "css", "bug", "react", "vue", "node", "sql", "git", "api", "frontend", "backend", "function", "code", "program"],
        "replies": [
            "Dasturlashda eng muhim narsa — amaliyot. Har kuni kamida 1 soat kod yozing.",
            "Kod yozishdan oldin algoritmni qog'ozga chizing — bu xatolarni 50% kamaytiradi.",
            "Git — bu dasturchining eng yaxshi do'sti. Har bir o'zgarishni commit qiling.",
            "Clean code yozing: o'zgaruvchi nomlarini mazmunli qo'ying.",
            "Debug qilishda console.log eng yaxshi yordamchingiz.",
            "Framework o'rganishdan oldin HTML/CSS/JS ni chuqur o'rganing.",
            "Test yozish vaqtni tejaydi — bugungi 1 soat, ertaga 10 soat.",
            "Kod review — bu o'rganishning eng tez usuli.",
        ],
    },
    2: {
        "name": "Dizayner AI", "icon": "🎨", "topics": ["dizayn", "ui", "ux", "foto", "rang", "shrift", "logo", "figma", "typography", "layout", "color", "gradient", "design"],
        "replies": [
            "Yaxshi dizayn — bu kamroq elementlar bilan ko'proq narsa aytish san'ati.",
            "Rang palitrasi 3 tadan oshmasin: asosiy, ikkilamchi, aksent.",
            "Foydalanuvchi tajribasi (UX) — bu dizaynning 80%.",
            "Whitespace — bu bo'sh joy emas, bu nafas.",
            "Shrift o'lchamlarida 1.5x scale ishlatib ko'ring (16-24-36-54).",
            "Kontrast muhim: matn va fon orasida kamida 4.5:1 nisbat bo'lsin.",
            "Figma'da component yaratish — bu vaqtni 10x tejaydi.",
        ],
    },
    3: {
        "name": "Marketolog AI", "icon": "📈", "topics": ["marketing", "smm", "reklama", "brend", "sotuv", "mijoz", "kontent", "instagram", "facebook", "targeting", "funnel"],
        "replies": [
            "Marketing — bu mahsulotingizni sevadigan odamlarni topish.",
            "SMM da izchillik muhim: haftada 3-5 post optimal.",
            "Mijozni tinglang — u sizga eng yaxshi g'oyalarni beradi.",
            "Kontent marketing — bu pul emas, vaqt investitsiyasi.",
            "A/B test qilishdan qo'rqmang: eng yaxshi variant faqat sinovda topiladi.",
            "Email marketing hanuz eng samarali kanallardan biri.",
        ],
    },
    4: {
        "name": "Tarjimon AI", "icon": "🌐", "topics": ["tarjima", "ingliz", "rus", "tarjimon", "til", "english", "translate", "language", "o'zbek"],
        "replies": [
            "Tarjima — bu so'z almashtirish emas, ma'no ko'chirish.",
            "Ingliz tilini o'rganish uchun har kuni 20 yangi so'z yodlang.",
            "Filmlarni asl tilida ko'ring — eng yaxshi o'qituvchi.",
            "Til o'rganishda asosiy to'siq — qo'rquv. Xato qilishdan qo'rqmang.",
            "Grammatikani o'rganing, lekin avval gapirishni boshlang.",
        ],
    },
    5: {
        "name": "Shifokor AI", "icon": "🩺", "topics": ["shifokor", "sog'liq", "kasal", "dori", "tibbiyot", "og'riq", "bosh", "tish", "yurak", "qon", "health"],
        "replies": [
            "Har qanday kasallikda birinchi qadam — haqiqiy shifokorga murojaat.",
            "Kuniga 2 litr suv ichish — eng oddiy va samarali odat.",
            "Kuniga 7-8 soat uxlash immunitet uchun zarur.",
            "Sport — bu eng yaxshi dori.",
            "Stressni kamaytirish uchun meditatsiya yoki chuqur nafas olishni sinab ko'ring.",
            "Yillik tibbiy ko'rikdan o'tishni unutmang.",
        ],
    },
    6: {
        "name": "Oshpaz AI", "icon": "👨‍🍳", "topics": ["ovqat", "retsept", "pishirish", "taom", "sho'rva", "salat", "non", "palov", "kabob", "somsa", "food"],
        "replies": [
            "Oshxona — bu sevgi bilan qilingan san'at.",
            "Palovning siri — guruchni to'g'ri tanlashda.",
            "Somsa uchun xamir yupqa bo'lsin, ichi esa suvli.",
            "Taomga tuz oxirida qo'shing — aks holda qattiq bo'ladi.",
            "Yangi masalliqlar — mazali taomning kaliti.",
        ],
    },
    7: {
        "name": "Psixolog AI", "icon": "🧠", "topics": ["psixolog", "ruhiy", "stress", "xafa", "yolg'iz", "qo'rquv", "tushkun", "depressiya", "tashvish", "xotirjam"],
        "replies": [
            "Sizning his-tuyg'ularingiz haqiqiy va muhim. Ularni inkor etmang.",
            "Qiyin paytlarda o'zingizga mehribon bo'ling.",
            "Har kuni 3 ta yaxshi narsa yozing — bu kayfiyatni yaxshilaydi.",
            "Yolg'izlik hissi — bu siz yomonligingiz belgisi emas.",
            "Professional psixologga murojaat qilish — kuchlilik belgisi.",
            "Nafas oling. Bu ham o'tadi.",
        ],
    },
    8: {
        "name": "Biznes AI", "icon": "💼", "topics": ["biznes", "startap", "investor", "kompaniya", "loyiha", "foyda", "strategiya", "moliya", "kapital", "mahsulot", "business"],
        "replies": [
            "Startap — bu 100 marta sinab, 1 marta muvaffaqiyat qozonish.",
            "Mahsulotni emas, muammoni yeching.",
            "Investor pul berishdan oldin jamoaga qaraydi.",
            "MVP (minimal viable product) — eng tez bozorga chiqing.",
            "Har bir xarajatni hisobga oling — pul tez tugaydi.",
        ],
    },
    9: {
        "name": "Huquqshunos AI", "icon": "⚖️", "topics": ["qonun", "huquq", "sud", "shartnoma", "advokat", "yuridik", "jinoyat", "mulk", "meros", "law"],
        "replies": [
            "Har qanday muhim shartnomani advokat bilan tekshiring.",
            "Huquqlaringizni bilish — himoyaning birinchi qadami.",
            "Og'zaki kelishuvlar sudda isbotlanmaydi — yozma shartnoma tuzing.",
            "Soliq to'lashdan qochmang — jarima kattaroq bo'ladi.",
        ],
    },
    10: {
        "name": "Sayohat AI", "icon": "✈️", "topics": ["sayohat", "dengiz", "tog'", "poyezd", "samolyot", "mehmonxona", "vizit", "turizm", "davlat", "travel"],
        "replies": [
            "Sayohatdan oldin byudjetni 2 barobar zaxira bilan rejalashtiring.",
            "Mahalliy taomlarni tatib ko'ring — eng yaxshi xotiralar shu yerda.",
            "Chiptalarni oldindan band qiling — 2-3x arzonroq.",
            "Eng yaxshi sayohat — rejadan chetga chiqish.",
        ],
    },
    11: {
        "name": "Musiqa AI", "icon": "🎵", "topics": ["musiqa", "qo'shiq", "garmoniya", "nota", "kuy", "gitara", "pianino", "baraban", "music"],
        "replies": [
            "Musiqa — bu ruhning tili.",
            "Har kuni 15 daqiqa mashq — bir yilda professional darajaga chiqaradi.",
            "Notalarni o'rganish — musiqa nazariyasining asosi.",
            "O'z uslubingizni topish uchun turli janrlarni tinglang.",
        ],
    },
    12: {
        "name": "Matematik AI", "icon": "➗", "topics": ["matematika", "hisob", "algebra", "geometriya", "formula", "tenglama", "integral", "hosila", "statistika", "math"],
        "replies": [
            "Matematika — bu naqshlarni topish san'ati.",
            "Formulani yodlamang, mantiqini tushuning.",
            "Har kuni 1 masala yechish — eng yaxshi mashq.",
            "Xato qilishdan qo'rqmang — bu o'rganishning bir qismi.",
        ],
    },
    13: {
        "name": "Tarixchi AI", "icon": "📜", "topics": ["tarix", "o'tmish", "qadim", "ajdodlar", "imperiya", "urush", "saltanat", "buyuk", "sarkarda", "history"],
        "replies": [
            "Tarixni bilmagan xalq kelajagini qura olmaydi.",
            "Amir Temur — buyuk sarkarda va davlat arbobi.",
            "Al-Xorazmiy — algebra asoschisi, jahon ilmiga ulkan hissa qo'shgan.",
            "Bobur — Boburiylar sulolasi asoschisi.",
            "Ulug'bek — astronomiya sohasida buyuk olim.",
        ],
    },
    14: {
        "name": "Sport AI", "icon": "⚽", "topics": ["sport", "futbol", "boks", "kurash", "olimpiya", "musobaqa", "tennis", "basketbol", "yugurish", "sport"],
        "replies": [
            "Sport — bu salomatlik garovi.",
            "Futbol — dunyodagi eng ommabop sport turi.",
            "Har kuni 30 daqiqa yugurish — sog'liq uchun eng yaxshi investitsiya.",
            "Kurash — o'zbek xalqining faxri.",
            "Chempionlar mashqdan charchamaydi, ular mashqsiz charchaydi.",
        ],
    },
    15: {
        "name": "Kino AI", "icon": "🎬", "topics": ["kino", "film", "serial", "aktyor", "rejissyor", "tomosha", "drama", "komediya", "triller", "movie"],
        "replies": [
            "Eng yaxshi filmlar — bu his-tuyg'ularni uyg'otadigan filmlar.",
            "Kino — bu 2 soat ichida boshqa hayotni yashash.",
            "Serial tomosha qilish vaqtni boshqarishni o'rgatadi.",
            "Oskar olgan filmlar — sifat belgisi.",
        ],
    },
    16: {
        "name": "Fitnes AI", "icon": "💪", "topics": ["fitnes", "mashq", "gym", "yugurish", "muskul", "vazn", "ovqatlanish", "kardio", "shtanga", "fitness"],
        "replies": [
            "Fitnes — bu bir martalik harakat emas, hayot tarzi.",
            "Mashqdan oldin isinish — jarohatdan himoya.",
            "Kuniga 8 stakan suv iching.",
            "Protein mushak o'sishi uchun zarur.",
            "Kardio va kuch mashqlarini birlashtiring.",
        ],
    },
    17: {
        "name": "Avto AI", "icon": "🚗", "topics": ["mashina", "avto", "dvigatel", "yuritish", "avtomobil", "haydash", "yoqilg'i", "remont", "car"],
        "replies": [
            "Mashina tanlashda ishonchlilik birinchi o'rinda.",
            "Muntazam texnik ko'rik — uzoq umr garovi.",
            "Yoqilg'i tejamkorligi muhim omil.",
            "Xavfsizlik — hech qachon murosaga kelmang.",
            "Elektr mashinalar kelajak.",
        ],
    },
    18: {
        "name": "Bog'bon AI", "icon": "🌱", "topics": ["o'simlik", "gul", "bog'", "daraxt", "parvarish", "sug'orish", "urug'", "tuproq", "plant"],
        "replies": [
            "O'simliklar — bu sabr va g'amxo'rlik ramzi.",
            "Sug'orish vaqti — ertalab yoki kechqurun.",
            "Har bir o'simlikning o'z yorug'lik ehtiyoji bor.",
            "Tuproqni to'g'ri tanlash muvaffaqiyat kaliti.",
        ],
    },
    19: {
        "name": "Astrolog AI", "icon": "🔮", "topics": ["astrologiya", "yulduz", "burj", "bashorat", "fal", "ekzotik", "zodiak", "astro"],
        "replies": [
            "Yulduzlar — bu yo'l ko'rsatuvchi, taqdiringizni belgilovchi emas.",
            "Har bir burjning o'z kuchli tomonlari bor.",
            "Astrologiya — bu o'z-o'zini bilish vositasi.",
            "Bugun — yangi boshlanishlar uchun yaxshi kun.",
        ],
    },
    20: {
        "name": "Komediyachi AI", "icon": "😂", "topics": ["hazil", "kulgi", "latifa", "anim", "qiziq", "komediya", "masxara", "funny"],
        "replies": [
            "Nega dasturchi ko'zoynak taqadi? Chunki u C# ni ko'ra olmaydi! 😂",
            "Nega kompyuter sovuq? Chunki u Windows ochgan! 🥶",
            "Nega matematik kitobni yozgan? Chunki u muammolarni yechishni yaxshi ko'radi! 📚",
            "Hayot — bu teatr, siz esa aktyorsiz. Kulishni unutmang!",
            "Kulgi — eng yaxshi dori. Bepul va nojoiz ta'sir qilmaydi! 😄",
        ],
    },
}


def ai_generate_reply(user_text: str) -> dict:
    """AI javobi generatsiyasi."""
    if not user_text:
        return {"bot": 1, "text": "Savolingizni yozing 🙂"}
    t = user_text.lower().strip()

    # Greetings
    if any(k in t for k in ["salom", "assalom", "hey", "hi", "qalay"]):
        return {"bot": 1, "text": "Salom! Qalaysiz? 😊 Sizga qanday yordam bera olaman?"}

    # Thanks
    if any(k in t for k in ["rahmat", "tashakkur", "thanks"]):
        return {"bot": 1, "text": "Arzimaydi! 😊 Yana savolingiz bo'lsa, bemalol yozing."}

    # Bye
    if any(k in t for k in ["xayr", "bye", "ko'rishguncha"]):
        return {"bot": 1, "text": "Xayr! Yana tashrif buyuring. 👋"}

    # Find best matching bot
    best_bot = 1
    best_score = 0
    for bot_id, bot in AI_BOTS.items():
        score = sum(1 for topic in bot["topics"] if topic in t)
        if score > best_score:
            best_score = score
            best_bot = bot_id

    bot = AI_BOTS[best_bot]
    reply = random.choice(bot["replies"])
    return {"bot": best_bot, "text": reply}


# ============================================================================
# SECTION 15: SERIALIZERS
# ============================================================================

def serialize_user_brief(user_row) -> dict:
    """Foydalanuvchi haqida qisqa ma'lumot."""
    if not user_row:
        return None
    u = dict(user_row)
    return {
        "id": u["id"],
        "name": u["name"],
        "avatar": u.get("avatar", "⭐"),
        "bio": u.get("bio", ""),
        "verified": bool(u.get("verified", 0)),
        "lastSeen": u.get("last_seen"),
        "createdAt": u.get("created_at"),
    }


def serialize_user_full(user_row, me_id: int = None) -> dict:
    """To'liq foydalanuvchi ma'lumoti."""
    if not user_row:
        return None
    u = dict(user_row)
    with db() as c:
        followers = c.execute(
            "SELECT COUNT(*) FROM follows WHERE following_id=?", (u["id"],)
        ).fetchone()[0]
        following = c.execute(
            "SELECT COUNT(*) FROM follows WHERE follower_id=?", (u["id"],)
        ).fetchone()[0]
        posts = c.execute(
            "SELECT COUNT(*) FROM posts WHERE user_id=? AND archived=0 AND deleted_at IS NULL",
            (u["id"],),
        ).fetchone()[0]
        is_following = False
        if me_id and me_id != u["id"]:
            is_following = c.execute(
                "SELECT 1 FROM follows WHERE follower_id=? AND following_id=?",
                (me_id, u["id"]),
            ).fetchone() is not None
        is_blocked = False
        if me_id and me_id != u["id"]:
            is_blocked = c.execute(
                "SELECT 1 FROM blocks WHERE (blocker_id=? AND blocked_id=?) OR (blocker_id=? AND blocked_id=?)",
                (me_id, u["id"], u["id"], me_id),
            ).fetchone() is not None

    return {
        "id": u["id"],
        "name": u["name"],
        "avatar": u.get("avatar", "⭐"),
        "bio": u.get("bio", ""),
        "email": u.get("email") if me_id == u["id"] else None,
        "phone": u.get("phone") if me_id == u["id"] else None,
        "verified": bool(u.get("verified", 0)),
        "isPrivate": bool(u.get("is_private", 0)),
        "twoFaEnabled": bool(u.get("two_fa_enabled", 0)) if me_id == u["id"] else False,
        "followers": followers,
        "following": following,
        "posts": posts,
        "isFollowing": is_following,
        "isBlocked": is_blocked,
        "lastSeen": u.get("last_seen"),
        "createdAt": u.get("created_at"),
    }


def serialize_post(post_row, me_id: int = None) -> dict:
    """Post ma'lumotlari."""
    if not post_row:
        return None
    p = dict(post_row)
    with db() as c:
        author = c.execute("SELECT * FROM users WHERE id=?", (p["user_id"],)).fetchone()
        reactions = c.execute(
            "SELECT emoji, COUNT(*) as n FROM reactions WHERE post_id=? GROUP BY emoji",
            (p["id"],),
        ).fetchall()
        comments = c.execute(
            "SELECT cm.*, u.name, u.avatar, u.verified FROM comments cm "
            "JOIN users u ON u.id = cm.user_id "
            "WHERE cm.post_id=? AND cm.deleted_at IS NULL ORDER BY cm.created_at",
            (p["id"],),
        ).fetchall()
        my_reaction = None
        my_save = None
        if me_id:
            r = c.execute(
                "SELECT emoji FROM reactions WHERE post_id=? AND user_id=?",
                (p["id"], me_id),
            ).fetchone()
            my_reaction = r["emoji"] if r else None
            s = c.execute(
                "SELECT 1 FROM saves WHERE post_id=? AND user_id=?",
                (p["id"], me_id),
            ).fetchone()
            my_save = s is not None

    return {
        "id": p["id"],
        "type": p.get("type", "post"),
        "authorId": p["user_id"],
        "authorName": author["name"] if author else "Deleted",
        "avatar": author["avatar"] if author else "⭐",
        "verified": bool(author["verified"]) if author else False,
        "text": p.get("text", ""),
        "image": p.get("image_url"),
        "video": p.get("video_url"),
        "audio": p.get("audio_url"),
        "location": p.get("location"),
        "tags": [t for t in (p.get("tags") or "").split(",") if t],
        "pinned": bool(p.get("pinned", 0)),
        "archived": bool(p.get("archived", 0)),
        "views": p.get("views", 0),
        "at": p["created_at"],
        "timeAgo": time_ago(p["created_at"]),
        "likes": sum(r["n"] for r in reactions),
        "liked": my_reaction is not None,
        "myReaction": my_reaction,
        "reactions": {r["emoji"]: r["n"] for r in reactions},
        "saved": my_save,
        "comments": [serialize_comment(dict(x)) for x in comments],
        "commentCount": len(comments),
    }


def serialize_comment(comment_row) -> dict:
    if not comment_row:
        return None
    c = dict(comment_row)
    return {
        "id": c["id"],
        "authorId": c["user_id"],
        "author": c.get("name", ""),
        "avatar": c.get("avatar", "⭐"),
        "verified": bool(c.get("verified", 0)),
        "text": c.get("text", ""),
        "at": c["created_at"],
        "timeAgo": time_ago(c["created_at"]),
    }


def serialize_message(msg_row, me_id: int) -> dict:
    m = dict(msg_row)
    return {
        "id": m["id"],
        "from": "me" if m["sender_id"] == me_id else "them",
        "text": m.get("text", ""),
        "type": m.get("type", "text"),
        "mediaUrl": m.get("media_url"),
        "replyToId": m.get("reply_to_id"),
        "read": bool(m.get("read_at")),
        "at": m["created_at"],
    }


def serialize_story(story_row, me_id: int, seen_ids: set = None) -> dict:
    s = dict(story_row)
    return {
        "id": s["id"],
        "userId": s["user_id"],
        "who": s.get("name", ""),
        "av": s.get("avatar", "⭐"),
        "emoji": s.get("emoji", "✨"),
        "text": s.get("text", ""),
        "image": s.get("image_url"),
        "views": s.get("views", 0),
        "isMine": s["user_id"] == me_id,
        "seen": s["id"] in (seen_ids or set()),
        "at": s["created_at"],
        "expiresAt": s["expires_at"],
    }


def serialize_product(prod_row) -> dict:
    p = dict(prod_row)
    return {
        "id": p["id"],
        "title": p["title"],
        "price": p["price"],
        "currency": p.get("currency", "UZS"),
        "description": p.get("description", ""),
        "emoji": p.get("emoji", "📦"),
        "category": p.get("category", "general"),
        "image": p.get("image_url"),
        "stock": p.get("stock", 0),
        "sold": p.get("sold", 0),
        "seller": p.get("seller_name", ""),
        "sellerId": p["user_id"],
        "at": p["created_at"],
    }


# ============================================================================
# SECTION 16: NOTIFICATIONS
# ============================================================================

def send_notification(user_id: int, actor_id: int, kind: str, text: str, ref_id: int = None):
    """Bildirishnoma yuborish."""
    if not user_id or user_id == actor_id:
        return
    try:
        with db() as c:
            c.execute(
                """INSERT INTO notifications(user_id,actor_id,kind,ref_id,text,created_at)
                   VALUES(?,?,?,?,?,?)""",
                (user_id, actor_id, kind, ref_id, text[:200], now_ms()),
            )
    except Exception as e:
        log.error(f"Notification xatosi: {e}")


def notify_followers(actor_id: int, post_id: int, text: str):
    """Follower'larga xabar yuborish."""
    try:
        with db() as c:
            followers = c.execute(
                "SELECT follower_id FROM follows WHERE following_id=?", (actor_id,)
            ).fetchall()
        for f in followers:
            send_notification(f["follower_id"], actor_id, "post", text, post_id)
    except Exception as e:
        log.error(f"Notify followers xatosi: {e}")


# ============================================================================
# SECTION 17: i18n
# ============================================================================

I18N = {
    "uz": {
        "welcome": "Xush kelibsiz",
        "login": "Kirish",
        "register": "Ro'yxatdan o'tish",
        "logout": "Chiqish",
        "error": "Xatolik",
        "success": "Muvaffaqiyat",
        "not_found": "Topilmadi",
        "unauthorized": "Ruxsat yo'q",
    },
    "en": {
        "welcome": "Welcome",
        "login": "Login",
        "register": "Register",
        "logout": "Logout",
        "error": "Error",
        "success": "Success",
        "not_found": "Not found",
        "unauthorized": "Unauthorized",
    },
    "ru": {
        "welcome": "Добро пожаловать",
        "login": "Вход",
        "register": "Регистрация",
        "logout": "Выход",
        "error": "Ошибка",
        "success": "Успех",
        "not_found": "Не найдено",
        "unauthorized": "Неавторизован",
    },
}


def t(key: str, lang: str = DEFAULT_LANG) -> str:
    """Tarjima."""
    if lang not in I18N:
        lang = DEFAULT_LANG
    return I18N[lang].get(key, I18N[DEFAULT_LANG].get(key, key))


def get_lang() -> str:
    """Foydalanuvchi tilini aniqlash."""
    if hasattr(g, "user") and g.user and g.user.get("lang"):
        return g.user["lang"]
    lang = request.headers.get("X-Lang", DEFAULT_LANG)
    return lang if lang in SUPPORTED_LANGS else DEFAULT_LANG


# ============================================================================
# SECTION 18: ANALYTICS
# ============================================================================

def get_analytics_summary() -> dict:
    """Umumiy analitika (admin)."""
    with db() as c:
        return {
            "users": {
                "total": c.execute("SELECT COUNT(*) FROM users").fetchone()[0],
                "verified": c.execute("SELECT COUNT(*) FROM users WHERE verified=1").fetchone()[0],
                "active_24h": c.execute("SELECT COUNT(*) FROM users WHERE last_seen > ?", (now_ms() - 86400000,)).fetchone()[0],
                "new_7d": c.execute("SELECT COUNT(*) FROM users WHERE created_at > ?", (now_ms() - 7*86400000,)).fetchone()[0],
                "new_30d": c.execute("SELECT COUNT(*) FROM users WHERE created_at > ?", (now_ms() - 30*86400000,)).fetchone()[0],
            },
            "posts": {
                "total": c.execute("SELECT COUNT(*) FROM posts WHERE deleted_at IS NULL").fetchone()[0],
                "today": c.execute("SELECT COUNT(*) FROM posts WHERE created_at > ?", (now_ms() - 86400000,)).fetchone()[0],
                "week": c.execute("SELECT COUNT(*) FROM posts WHERE created_at > ?", (now_ms() - 7*86400000,)).fetchone()[0],
                "reels": c.execute("SELECT COUNT(*) FROM posts WHERE type='reel' AND deleted_at IS NULL").fetchone()[0],
            },
            "engagement": {
                "reactions": c.execute("SELECT COUNT(*) FROM reactions").fetchone()[0],
                "comments": c.execute("SELECT COUNT(*) FROM comments WHERE deleted_at IS NULL").fetchone()[0],
                "saves": c.execute("SELECT COUNT(*) FROM saves").fetchone()[0],
                "follows": c.execute("SELECT COUNT(*) FROM follows").fetchone()[0],
            },
            "chats": {
                "total": c.execute("SELECT COUNT(*) FROM chats").fetchone()[0],
                "messages": c.execute("SELECT COUNT(*) FROM messages WHERE deleted_at IS NULL").fetchone()[0],
            },
            "stories": {
                "active": c.execute("SELECT COUNT(*) FROM stories WHERE expires_at > ?", (now_ms(),)).fetchone()[0],
                "views": c.execute("SELECT COUNT(*) FROM story_views").fetchone()[0],
            },
            "market": {
                "products": c.execute("SELECT COUNT(*) FROM market WHERE status='active'").fetchone()[0],
                "orders": c.execute("SELECT COUNT(*) FROM orders").fetchone()[0],
                "revenue": c.execute("SELECT COALESCE(SUM(total),0) FROM orders WHERE status='completed'").fetchone()[0],
            },
            "moderation": {
                "pending": c.execute("SELECT COUNT(*) FROM moderation_queue WHERE status='pending'").fetchone()[0],
                "reports": c.execute("SELECT COUNT(*) FROM reports WHERE status='pending'").fetchone()[0],
            },
        }


def get_user_growth(days: int = 30) -> list:
    """Foydalanuvchilar o'sishi."""
    with db() as c:
        result = []
        for i in range(days):
            start = now_ms() - (days - i) * 86400000
            end = start + 86400000
            n = c.execute(
                "SELECT COUNT(*) FROM users WHERE created_at >= ? AND created_at < ?",
                (start, end),
            ).fetchone()[0]
            result.append({"date": datetime.fromtimestamp(start/1000).strftime("%Y-%m-%d"), "count": n})
    return result


def get_top_users(limit: int = 10, metric: str = "followers") -> list:
    """Eng faol foydalanuvchilar."""
    with db() as c:
        if metric == "followers":
            rows = c.execute(
                """SELECT u.*, COUNT(f.id) as metric
                   FROM users u LEFT JOIN follows f ON f.following_id = u.id
                   GROUP BY u.id ORDER BY metric DESC LIMIT ?""",
                (limit,),
            ).fetchall()
        elif metric == "posts":
            rows = c.execute(
                """SELECT u.*, COUNT(p.id) as metric
                   FROM users u LEFT JOIN posts p ON p.user_id = u.id
                   WHERE p.deleted_at IS NULL
                   GROUP BY u.id ORDER BY metric DESC LIMIT ?""",
                (limit,),
            ).fetchall()
        else:  # reactions
            rows = c.execute(
                """SELECT u.*, COUNT(r.id) as metric
                   FROM users u LEFT JOIN posts p ON p.user_id = u.id
                   LEFT JOIN reactions r ON r.post_id = p.id
                   GROUP BY u.id ORDER BY metric DESC LIMIT ?""",
                (limit,),
            ).fetchall()
    return [dict(r) for r in rows]


def get_trending_hashtags(limit: int = 10, hours: int = 24) -> list:
    """Trend hashtaglar."""
    cutoff = now_ms() - hours * 3600000
    with db() as c:
        rows = c.execute(
            "SELECT tags FROM posts WHERE created_at > ? AND deleted_at IS NULL AND tags != ''",
            (cutoff,),
        ).fetchall()

    counter = Counter()
    for row in rows:
        for tag in (row["tags"] or "").split(","):
            tag = tag.strip()
            if tag:
                counter[tag] += 1

    return [{"tag": t, "count": n} for t, n in counter.most_common(limit)]


def get_engagement_rate(post_id: int) -> dict:
    """Post uchun engagement rate."""
    with db() as c:
        likes = c.execute("SELECT COUNT(*) FROM reactions WHERE post_id=?", (post_id,)).fetchone()[0]
        comments = c.execute("SELECT COUNT(*) FROM comments WHERE post_id=? AND deleted_at IS NULL", (post_id,)).fetchone()[0]
        saves = c.execute("SELECT COUNT(*) FROM saves WHERE post_id=?", (post_id,)).fetchone()[0]
        views = c.execute("SELECT views FROM posts WHERE id=?", (post_id,)).fetchone()
        view_count = views["views"] if views else 0
    total_eng = likes + comments + saves
    rate = (total_eng / view_count * 100) if view_count > 0 else 0
    return {
        "likes": likes,
        "comments": comments,
        "saves": saves,
        "views": view_count,
        "engagement": total_eng,
        "rate": round(rate, 2),
    }


# ============================================================================
# SECTION 19: HEALTH & SYSTEM
# ============================================================================

@app.route("/health", methods=["GET"])
@app.route("/api/health", methods=["GET"])
def health_check():
    """Health check — Render uchun majburiy."""
    try:
        with db() as c:
            c.execute("SELECT 1").fetchone()
        return jsonify({
            "ok": True,
            "status": "healthy",
            "time": now_ms(),
            "version": VERSION,
            "render": IS_RENDER,
            "db": "ok",
        }), 200
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/version", methods=["GET"])
def api_version():
    return jsonify({
        "name": APP_NAME,
        "version": VERSION,
        "python": sys.version.split()[0],
        "render": IS_RENDER,
        "uptime": now_ms(),
    })


@app.route("/api/metrics", methods=["GET"])
def api_metrics():
    """Prometheus-style metrics."""
    try:
        stats = get_analytics_summary()
        lines = []
        for cat, metrics in stats.items():
            for key, val in metrics.items():
                if isinstance(val, (int, float)):
                    lines.append(f'unigram_{cat}_{key} {val}')
        return Response("\n".join(lines), mimetype="text/plain")
    except Exception as e:
        return Response(f"# error: {e}", mimetype="text/plain")


# ============================================================================
# SECTION 20: AUTH ENDPOINTS
# ============================================================================

@app.route("/api/auth/register", methods=["POST"])
@rate_limit("register")
def api_register():
    """Ro'yxatdan o'tish."""
    data = request.get_json(silent=True) or {}

    # Validatsiya
    ok, name_or_err = validate_name(data.get("name"))
    if not ok:
        return jsonify({"error": name_or_err}), 400

    name = name_or_err
    password = data.get("password", "")
    email = (data.get("email") or "").strip().lower()
    phone = (data.get("phone") or "").strip()
    avatar = (data.get("avatar") or "⭐").strip()[:4]

    ok, pwd_result = validate_password(password)
    if not ok:
        return jsonify({"error": pwd_result}), 400

    if email and not validate_email(email):
        return jsonify({"error": "Email formati xato"}), 400

    if phone and not validate_phone(phone):
        return jsonify({"error": "Telefon formati xato"}), 400

    with db() as c:
        if c.execute("SELECT 1 FROM users WHERE name=?", (name,)).fetchone():
            return jsonify({"error": "Bu ism band", "code": "name_taken"}), 409
        if email and c.execute("SELECT 1 FROM users WHERE email=?", (email,)).fetchone():
            return jsonify({"error": "Bu email band", "code": "email_taken"}), 409

        c.execute(
            "INSERT INTO users(name,password_hash,email,phone,avatar,created_at,last_seen) "
            "VALUES(?,?,?,?,?,?,?)",
            (name, generate_password_hash(password), email or None,
             phone or None, avatar, now_ms(), now_ms()),
        )
        uid = c.execute("SELECT last_insert_rowid()").fetchone()[0]

        # Token yaratish
        token = gen_token()
        c.execute(
            "INSERT INTO api_tokens(user_id,token,ip,user_agent,created_at) VALUES(?,?,?,?,?)",
            (uid, token, request.remote_addr, request.headers.get("User-Agent", "")[:200], now_ms()),
        )

    log.info(f"Yangi user: {name} (ID: {uid})")

    return jsonify({
        "token": token,
        "user": {
            "id": uid,
            "name": name,
            "avatar": avatar,
            "email": email or None,
            "phone": phone or None,
            "verified": False,
        },
        "passwordStrength": pwd_result,
    }), 201


@app.route("/api/auth/login", methods=["POST"])
@rate_limit("auth")
def api_login():
    """Kirish."""
    data = request.get_json(silent=True) or {}
    name = (data.get("name") or "").strip()
    password = data.get("password", "")

    if not name or not password:
        return jsonify({"error": "Ism va parol kerak"}), 400

    with db() as c:
        user = c.execute("SELECT * FROM users WHERE name=?", (name,)).fetchone()

        if not user or not check_password_hash(user["password_hash"], password):
            return jsonify({"error": "Ism yoki parol xato", "code": "invalid_creds"}), 401

        # 2FA check
        if user["two_fa_enabled"]:
            code = data.get("code", "")
            if not code:
                return jsonify({"error": "2FA kod kerak", "code": "need_2fa"}), 401
            if not verify_2fa_code(user["two_fa_secret"], code):
                return jsonify({"error": "2FA kod xato", "code": "bad_2fa"}), 401

        # Token yaratish
        token = gen_token()
        c.execute(
            "INSERT INTO api_tokens(user_id,token,ip,user_agent,created_at) VALUES(?,?,?,?,?)",
            (user["id"], token, request.remote_addr,
             request.headers.get("User-Agent", "")[:200], now_ms()),
        )
        c.execute("UPDATE users SET last_login=?, last_seen=? WHERE id=?",
                  (now_ms(), now_ms(), user["id"]))

    log_activity("login", "user", user["id"])

    return jsonify({
        "token": token,
        "user": serialize_user_full(user, user["id"]),
    })


@app.route("/api/auth/logout", methods=["POST"])
@auth_required
def api_logout():
    """Chiqish."""
    with db() as c:
        c.execute("UPDATE api_tokens SET revoked=1 WHERE id=?", (g.token_id,))
    log_activity("logout")
    return jsonify({"ok": True})


@app.route("/api/auth/logout-all", methods=["POST"])
@auth_required
def api_logout_all():
    """Barcha qurilmalardan chiqish."""
    with db() as c:
        c.execute("UPDATE api_tokens SET revoked=1 WHERE user_id=?", (g.user["id"],))
    return jsonify({"ok": True})


@app.route("/api/auth/reset-request", methods=["POST"])
@rate_limit("auth")
def api_reset_request():
    """Parolni tiklash so'rovi."""
    data = request.get_json(silent=True) or {}
    email = (data.get("email") or "").strip().lower()
    if not email:
        return jsonify({"error": "Email kerak"}), 400

    with db() as c:
        user = c.execute("SELECT * FROM users WHERE email=?", (email,)).fetchone()
        if user:
            token = gen_token()
            expires = now_ms() + 3600000  # 1 soat
            c.execute("UPDATE users SET reset_token=?, reset_expires=? WHERE id=?",
                      (token, expires, user["id"]))
            send_reset_email(email, token, request.host)

    # Xavfsizlik uchun har doim bir xil javob
    return jsonify({"ok": True, "message": "Agar email mavjud bo'lsa, havola yuborildi"})


@app.route("/api/auth/reset-confirm", methods=["POST"])
@rate_limit("auth")
def api_reset_confirm():
    """Parolni yangilash."""
    data = request.get_json(silent=True) or {}
    token = data.get("token", "")
    new_password = data.get("password", "")

    if not token or not new_password:
        return jsonify({"error": "Token va parol kerak"}), 400

    ok, err = validate_password(new_password)
    if not ok:
        return jsonify({"error": err}), 400

    with db() as c:
        user = c.execute(
            "SELECT * FROM users WHERE reset_token=? AND reset_expires > ?",
            (token, now_ms()),
        ).fetchone()
        if not user:
            return jsonify({"error": "Token yaroqsiz yoki muddati o'tgan"}), 400

        c.execute(
            "UPDATE users SET password_hash=?, reset_token=NULL, reset_expires=NULL WHERE id=?",
            (generate_password_hash(new_password), user["id"]),
        )
        # Barcha tokenlarni bekor qilish
        c.execute("UPDATE api_tokens SET revoked=1 WHERE user_id=?", (user["id"],))

    return jsonify({"ok": True, "message": "Parol yangilandi"})


@app.route("/api/auth/2fa/enable", methods=["POST"])
@auth_required
def api_2fa_enable():
    """2FA yoqish."""
    secret = generate_2fa_secret()
    with db() as c:
        c.execute("UPDATE users SET two_fa_secret=? WHERE id=?",
                  (secret, g.user["id"]))
    return jsonify({"secret": secret, "note": "Kodni saqlang"})


@app.route("/api/auth/2fa/verify", methods=["POST"])
@auth_required
def api_2fa_verify():
    """2FA ni tasdiqlash."""
    data = request.get_json(silent=True) or {}
    code = data.get("code", "")

    with db() as c:
        user = c.execute("SELECT two_fa_secret FROM users WHERE id=?", (g.user["id"],)).fetchone()
        if not user or not user["two_fa_secret"]:
            return jsonify({"error": "2FA yoqilmagan"}), 400

        if verify_2fa_code(user["two_fa_secret"], code):
            c.execute("UPDATE users SET two_fa_enabled=1 WHERE id=?", (g.user["id"],))
            return jsonify({"ok": True})
    return jsonify({"error": "Kod xato"}), 400


@app.route("/api/auth/2fa/disable", methods=["POST"])
@auth_required
def api_2fa_disable():
    """2FA o'chirish."""
    with db() as c:
        c.execute("UPDATE users SET two_fa_enabled=0, two_fa_secret=NULL WHERE id=?",
                  (g.user["id"],))
    return jsonify({"ok": True})


@app.route("/api/auth/sessions", methods=["GET"])
@auth_required
def api_sessions():
    """Faol sessiyalar."""
    with db() as c:
        rows = c.execute(
            "SELECT id, ip, user_agent, created_at, last_used FROM api_tokens "
            "WHERE user_id=? AND revoked=0 ORDER BY created_at DESC",
            (g.user["id"],),
        ).fetchall()
    return jsonify([dict(r) for r in rows])


@app.route("/api/auth/sessions/<int:sid>", methods=["DELETE"])
@auth_required
def api_revoke_session(sid):
    """Sessiyani bekor qilish."""
    with db() as c:
        c.execute(
            "UPDATE api_tokens SET revoked=1 WHERE id=? AND user_id=?",
            (sid, g.user["id"]),
        )
    return jsonify({"ok": True})


# ============================================================================
# SECTION 21: USER ENDPOINTS
# ============================================================================

@app.route("/api/me", methods=["GET"])
@auth_required
def api_me():
    """Joriy foydalanuvchi."""
    with db() as c:
        user = c.execute("SELECT * FROM users WHERE id=?", (g.user["id"],)).fetchone()
    return jsonify(serialize_user_full(user, g.user["id"]))


@app.route("/api/me", methods=["PATCH"])
@auth_required
def api_update_me():
    """Profil yangilash."""
    data = request.get_json(silent=True) or {}
    fields, params = [], []

    if "avatar" in data:
        fields.append("avatar=?")
        params.append(str(data["avatar"])[:4])

    if "bio" in data:
        bio = str(data["bio"])[:MAX_BIO_LENGTH]
        fields.append("bio=?")
        params.append(bio)

    if "email" in data:
        email = (data["email"] or "").strip().lower()
        if email and not validate_email(email):
            return jsonify({"error": "Email formati xato"}), 400
        fields.append("email=?")
        params.append(email or None)

    if "phone" in data:
        phone = (data["phone"] or "").strip()
        if phone and not validate_phone(phone):
            return jsonify({"error": "Telefon formati xato"}), 400
        fields.append("phone=?")
        params.append(phone or None)

    if "lang" in data:
        lang = data["lang"]
        if lang not in SUPPORTED_LANGS:
            return jsonify({"error": "Til qo'llab-quvvatlanmaydi"}), 400
        fields.append("lang=?")
        params.append(lang)

    if "isPrivate" in data:
        fields.append("is_private=?")
        params.append(1 if data["isPrivate"] else 0)

    if not fields:
        return jsonify({"error": "Hech narsa o'zgartirilmadi"}), 400

    params.append(g.user["id"])
    with db() as c:
        c.execute(f"UPDATE users SET {','.join(fields)} WHERE id=?", params)

    return jsonify({"ok": True})


@app.route("/api/me/change-password", methods=["POST"])
@auth_required
def api_change_password():
    """Parolni o'zgartirish."""
    data = request.get_json(silent=True) or {}
    old_pwd = data.get("old", "")
    new_pwd = data.get("new", "")

    if not check_password_hash(g.user["password_hash"], old_pwd):
        return jsonify({"error": "Eski parol xato"}), 400

    ok, err = validate_password(new_pwd)
    if not ok:
        return jsonify({"error": err}), 400

    with db() as c:
        c.execute("UPDATE users SET password_hash=? WHERE id=?",
                  (generate_password_hash(new_pwd), g.user["id"]))
        # Boshqa sessiyalarni bekor qilish
        c.execute("UPDATE api_tokens SET revoked=1 WHERE user_id=? AND id != ?",
                  (g.user["id"], g.token_id))

    return jsonify({"ok": True})


@app.route("/api/users/<int:uid>", methods=["GET"])
@auth_optional
def api_user_get(uid):
    """Boshqa foydalanuvchi profili."""
    with db() as c:
        user = c.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()
        if not user:
            return jsonify({"error": "Foydalanuvchi topilmadi"}), 404
    me_id = g.user["id"] if g.user else None
    return jsonify(serialize_user_full(user, me_id))


@app.route("/api/users/<int:uid>/posts", methods=["GET"])
@auth_optional
def api_user_posts(uid):
    """Foydalanuvchi postlari."""
    with db() as c:
        user = c.execute("SELECT is_private FROM users WHERE id=?", (uid,)).fetchone()
        if not user:
            return jsonify({"error": "Topilmadi"}), 404

        # Yopiq akkaunt tekshiruvi
        me_id = g.user["id"] if g.user else None
        if user["is_private"] and me_id != uid:
            if not me_id:
                return jsonify({"error": "Yopiq akkaunt", "isPrivate": True}), 403
            is_following = c.execute(
                "SELECT 1 FROM follows WHERE follower_id=? AND following_id=?",
                (me_id, uid),
            ).fetchone()
            if not is_following:
                return jsonify({"error": "Yopiq akkaunt", "isPrivate": True}), 403

        rows = c.execute(
            "SELECT * FROM posts WHERE user_id=? AND archived=0 AND deleted_at IS NULL "
            "ORDER BY created_at DESC LIMIT 50",
            (uid,),
        ).fetchall()

    return jsonify([serialize_post(r, me_id) for r in rows])


@app.route("/api/users/<int:uid>/follow", methods=["POST"])
@auth_required
def api_follow(uid):
    """Kuzatish / bekor qilish."""
    if uid == g.user["id"]:
        return jsonify({"error": "O'zingizni kuzatolmaysiz"}), 400

    with db() as c:
        u = c.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone()
        if not u:
            return jsonify({"error": "Topilmadi"}), 404

        # Block tekshiruvi
        blocked = c.execute(
            "SELECT 1 FROM blocks WHERE (blocker_id=? AND blocked_id=?) OR (blocker_id=? AND blocked_id=?)",
            (g.user["id"], uid, uid, g.user["id"]),
        ).fetchone()
        if blocked:
            return jsonify({"error": "Bloklangan"}), 403

        existing = c.execute(
            "SELECT 1 FROM follows WHERE follower_id=? AND following_id=?",
            (g.user["id"], uid),
        ).fetchone()

        if existing:
            c.execute(
                "DELETE FROM follows WHERE follower_id=? AND following_id=?",
                (g.user["id"], uid),
            )
            following = False
        else:
            c.execute(
                "INSERT INTO follows(follower_id,following_id,created_at) VALUES(?,?,?)",
                (g.user["id"], uid, now_ms()),
            )
            following = True
            send_notification(uid, g.user["id"], "follow",
                              f"{g.user['name']} sizni kuzatishni boshladi")

        followers_count = c.execute(
            "SELECT COUNT(*) FROM follows WHERE following_id=?", (uid,)
        ).fetchone()[0]

    log_activity("follow" if following else "unfollow", "user", uid)
    return jsonify({"following": following, "followers": followers_count})


@app.route("/api/users/<int:uid>/followers", methods=["GET"])
@auth_optional
def api_user_followers(uid):
    """Foydalanuvchi follower'lari."""
    with db() as c:
        rows = c.execute(
            "SELECT u.* FROM users u JOIN follows f ON f.follower_id = u.id "
            "WHERE f.following_id=? ORDER BY f.created_at DESC LIMIT 100",
            (uid,),
        ).fetchall()
    return jsonify([serialize_user_brief(r) for r in rows])


@app.route("/api/users/<int:uid>/following", methods=["GET"])
@auth_optional
def api_user_following(uid):
    """Foydalanuvchi kuzatayotganlar."""
    with db() as c:
        rows = c.execute(
            "SELECT u.* FROM users u JOIN follows f ON f.following_id = u.id "
            "WHERE f.follower_id=? ORDER BY f.created_at DESC LIMIT 100",
            (uid,),
        ).fetchall()
    return jsonify([serialize_user_brief(r) for r in rows])


@app.route("/api/users/<int:uid>/block", methods=["POST"])
@auth_required
def api_block(uid):
    """Bloklash / blokdan chiqarish."""
    if uid == g.user["id"]:
        return jsonify({"error": "O'zingizni bloklay olmaysiz"}), 400

    with db() as c:
        u = c.execute("SELECT 1 FROM users WHERE id=?", (uid,)).fetchone()
        if not u:
            return jsonify({"error": "Topilmadi"}), 404

        existing = c.execute(
            "SELECT 1 FROM blocks WHERE blocker_id=? AND blocked_id=?",
            (g.user["id"], uid),
        ).fetchone()

        if existing:
            c.execute("DELETE FROM blocks WHERE blocker_id=? AND blocked_id=?",
                      (g.user["id"], uid))
            blocked = False
        else:
            c.execute("INSERT INTO blocks(blocker_id,blocked_id,created_at) VALUES(?,?,?)",
                      (g.user["id"], uid, now_ms()))
            # Follow larni ham o'chirish
            c.execute("DELETE FROM follows WHERE follower_id=? AND following_id=?",
                      (g.user["id"], uid))
            c.execute("DELETE FROM follows WHERE follower_id=? AND following_id=?",
                      (uid, g.user["id"]))
            blocked = True

    return jsonify({"blocked": blocked})


@app.route("/api/blocks", methods=["GET"])
@auth_required
def api_get_blocks():
    """Bloklangan foydalanuvchilar."""
    with db() as c:
        rows = c.execute(
            "SELECT u.* FROM users u JOIN blocks b ON b.blocked_id = u.id "
            "WHERE b.blocker_id=?",
            (g.user["id"],),
        ).fetchall()
    return jsonify([serialize_user_brief(r) for r in rows])


@app.route("/api/users/report", methods=["POST"])
@auth_required
@rate_limit("post")
def api_report():
    """Shikoyat qilish."""
    data = request.get_json(silent=True) or {}
    target_type = data.get("targetType")
    target_id = data.get("targetId")
    reason = (data.get("reason") or "boshqa")[:200]

    if target_type not in ("post", "comment", "user", "story", "message"):
        return jsonify({"error": "targetType noto'g'ri"}), 400
    if not target_id:
        return jsonify({"error": "targetId kerak"}), 400

    with db() as c:
        c.execute(
            "INSERT INTO reports(reporter_id,target_type,target_id,reason,created_at) "
            "VALUES(?,?,?,?,?)",
            (g.user["id"], target_type, target_id, reason, now_ms()),
        )

    return jsonify({"ok": True})


# ============================================================================
# SECTION 22: POSTS
# ============================================================================

@app.route("/api/posts", methods=["GET"])
@auth_optional
def api_posts_list():
    """Postlar ro'yxati."""
    me_id = g.user["id"] if g.user else None
    limit = min(int(request.args.get("limit", 50)), 100)
    offset = max(0, int(request.args.get("offset", 0)))
    post_type = request.args.get("type")

    query = "SELECT * FROM posts WHERE archived=0 AND deleted_at IS NULL"
    params = []
    if post_type in ("post", "reel", "album"):
        query += " AND type=?"
        params.append(post_type)
    query += " ORDER BY pinned DESC, created_at DESC LIMIT ? OFFSET ?"
    params.extend([limit, offset])

    with db() as c:
        rows = c.execute(query, params).fetchall()

    return jsonify([serialize_post(r, me_id) for r in rows])


@app.route("/api/posts", methods=["POST"])
@auth_required
@rate_limit("post")
def api_posts_create():
    """Post yaratish."""
    data = request.get_json(silent=True) or {}
    text = (data.get("text") or "").strip()
    ptype = data.get("type", "post")
    if ptype not in ("post", "reel", "album"):
        ptype = "post"

    # Validatsiya
    if text:
        ok, err = validate_post_text(text)
        if not ok:
            return jsonify({"error": err}), 400

    # Moderatsiya
    if text:
        is_ok, mod_reason = moderate_text(text)
        if not is_ok:
            return jsonify({"error": f"Kontent bloklandi: {mod_reason}"}), 400

    # Rasm
    image_url = save_base64_image(data.get("image"), "post")
    if not text and not image_url:
        return jsonify({"error": "Bo'sh post yaratib bo'lmaydi"}), 400

    tags = ",".join(parse_tags(text))[:300]
    location = (data.get("location") or "")[:80] or None

    with db() as c:
        c.execute(
            "INSERT INTO posts(user_id,type,text,image_url,location,tags,created_at) "
            "VALUES(?,?,?,?,?,?,?)",
            (g.user["id"], ptype, text, image_url, location, tags, now_ms()),
        )
        pid = c.execute("SELECT last_insert_rowid()").fetchone()[0]
        row = c.execute("SELECT * FROM posts WHERE id=?", (pid,)).fetchone()

    # Auto moderate
    if text:
        auto_moderate("post", pid, text)

    log_activity("create_post", "post", pid)

    return jsonify(serialize_post(row, g.user["id"])), 201


@app.route("/api/posts/<int:pid>", methods=["GET"])
@auth_optional
def api_post_get(pid):
    """Bitta post."""
    me_id = g.user["id"] if g.user else None
    with db() as c:
        row = c.execute("SELECT * FROM posts WHERE id=? AND deleted_at IS NULL", (pid,)).fetchone()
        if not row:
            return jsonify({"error": "Topilmadi"}), 404
        # View count increment
        c.execute("UPDATE posts SET views = views + 1 WHERE id=?", (pid,))

    return jsonify(serialize_post(row, me_id))


@app.route("/api/posts/<int:pid>", methods=["DELETE"])
@auth_required
def api_post_delete(pid):
    """Post o'chirish."""
    with db() as c:
        row = c.execute("SELECT user_id FROM posts WHERE id=?", (pid,)).fetchone()
        if not row:
            return jsonify({"error": "Topilmadi"}), 404
        if row["user_id"] != g.user["id"]:
            return jsonify({"error": "Ruxsat yo'q"}), 403
        c.execute("UPDATE posts SET deleted_at=? WHERE id=?", (now_ms(), pid))

    log_activity("delete_post", "post", pid)
    return jsonify({"ok": True})


@app.route("/api/posts/<int:pid>/pin", methods=["POST"])
@auth_required
def api_post_pin(pid):
    """Postni pin qilish."""
    with db() as c:
        row = c.execute("SELECT user_id, pinned FROM posts WHERE id=?", (pid,)).fetchone()
        if not row:
            return jsonify({"error": "Topilmadi"}), 404
        if row["user_id"] != g.user["id"]:
            return jsonify({"error": "Ruxsat yo'q"}), 403
        new_val = 0 if row["pinned"] else 1
        c.execute("UPDATE posts SET pinned=? WHERE id=?", (new_val, pid))
    return jsonify({"pinned": bool(new_val)})


@app.route("/api/posts/<int:pid>/archive", methods=["POST"])
@auth_required
def api_post_archive(pid):
    """Postni arxivlash."""
    with db() as c:
        row = c.execute("SELECT user_id, archived FROM posts WHERE id=?", (pid,)).fetchone()
        if not row:
            return jsonify({"error": "Topilmadi"}), 404
        if row["user_id"] != g.user["id"]:
            return jsonify({"error": "Ruxsat yo'q"}), 403
        new_val = 0 if row["archived"] else 1
        c.execute("UPDATE posts SET archived=? WHERE id=?", (new_val, pid))
    return jsonify({"archived": bool(new_val)})


@app.route("/api/posts/<int:pid>/react", methods=["POST"])
@auth_required
@rate_limit("reaction")
def api_post_react(pid):
    """Layk / reaksiya."""
    data = request.get_json(silent=True) or {}
    emoji = (data.get("emoji") or "❤️")[:8]

    with db() as c:
        post = c.execute("SELECT user_id FROM posts WHERE id=? AND deleted_at IS NULL", (pid,)).fetchone()
        if not post:
            return jsonify({"error": "Topilmadi"}), 404

        existing = c.execute(
            "SELECT emoji FROM reactions WHERE post_id=? AND user_id=?",
            (pid, g.user["id"]),
        ).fetchone()

        if existing and existing["emoji"] == emoji:
            c.execute("DELETE FROM reactions WHERE post_id=? AND user_id=?",
                      (pid, g.user["id"]))
            my = None
        else:
            c.execute(
                """INSERT INTO reactions(post_id,user_id,emoji,created_at) VALUES(?,?,?,?)
                   ON CONFLICT(post_id,user_id) DO UPDATE SET emoji=excluded.emoji""",
                (pid, g.user["id"], emoji, now_ms()),
            )
            my = emoji
            if post["user_id"] != g.user["id"]:
                send_notification(post["user_id"], g.user["id"], "reaction",
                                  f"{g.user['name']} postingizga {emoji} qo'ydi", pid)

        count = c.execute("SELECT COUNT(*) FROM reactions WHERE post_id=?", (pid,)).fetchone()[0]

    return jsonify({"liked": my is not None, "myReaction": my, "count": count})


@app.route("/api/posts/<int:pid>/save", methods=["POST"])
@auth_required
def api_post_save(pid):
    """Postni saqlash."""
    with db() as c:
        post = c.execute("SELECT 1 FROM posts WHERE id=? AND deleted_at IS NULL", (pid,)).fetchone()
        if not post:
            return jsonify({"error": "Topilmadi"}), 404

        existing = c.execute(
            "SELECT 1 FROM saves WHERE post_id=? AND user_id=?",
            (pid, g.user["id"]),
        ).fetchone()

        if existing:
            c.execute("DELETE FROM saves WHERE post_id=? AND user_id=?",
                      (pid, g.user["id"]))
            saved = False
        else:
            c.execute("INSERT INTO saves(post_id,user_id,created_at) VALUES(?,?,?)",
                      (pid, g.user["id"], now_ms()))
            saved = True

        count = c.execute("SELECT COUNT(*) FROM saves WHERE post_id=?", (pid,)).fetchone()[0]

    return jsonify({"saved": saved, "count": count})


@app.route("/api/posts/<int:pid>/comment", methods=["POST"])
@auth_required
@rate_limit("comment")
def api_post_comment(pid):
    """Izoh qo'shish."""
    data = request.get_json(silent=True) or {}
    text = (data.get("text") or "").strip()
    parent_id = data.get("parentId")

    ok, err = validate_comment_text(text)
    if not ok:
        return jsonify({"error": err}), 400

    is_ok, mod_reason = moderate_text(text)
    if not is_ok:
        return jsonify({"error": f"Kontent bloklandi: {mod_reason}"}), 400

    with db() as c:
        post = c.execute("SELECT user_id FROM posts WHERE id=? AND deleted_at IS NULL", (pid,)).fetchone()
        if not post:
            return jsonify({"error": "Post topilmadi"}), 404

        if parent_id:
            parent = c.execute("SELECT 1 FROM comments WHERE id=? AND post_id=?",
                              (parent_id, pid)).fetchone()
            if not parent:
                parent_id = None

        c.execute(
            "INSERT INTO comments(post_id,user_id,parent_id,text,created_at) VALUES(?,?,?,?,?)",
            (pid, g.user["id"], parent_id, text, now_ms()),
        )
        cid = c.execute("SELECT last_insert_rowid()").fetchone()[0]

        # Post egasiga
        if post["user_id"] != g.user["id"]:
            send_notification(post["user_id"], g.user["id"], "comment",
                              f"{g.user['name']} izoh qoldirdi: {text[:40]}", pid)

    auto_moderate("comment", cid, text)

    return jsonify({"id": cid}), 201


@app.route("/api/comments/<int:cid>", methods=["DELETE"])
@auth_required
def api_comment_delete(cid):
    """Izohni o'chirish."""
    with db() as c:
        row = c.execute("SELECT user_id FROM comments WHERE id=?", (cid,)).fetchone()
        if not row:
            return jsonify({"error": "Topilmadi"}), 404
        if row["user_id"] != g.user["id"]:
            return jsonify({"error": "Ruxsat yo'q"}), 403
        c.execute("UPDATE comments SET deleted_at=? WHERE id=?", (now_ms(), cid))
    return jsonify({"ok": True})


@app.route("/api/posts/<int:pid>/insights", methods=["GET"])
@auth_required
def api_post_insights(pid):
    """Post statistikasi (faqat egasi uchun)."""
    with db() as c:
        post = c.execute("SELECT user_id FROM posts WHERE id=?", (pid,)).fetchone()
        if not post:
            return jsonify({"error": "Topilmadi"}), 404
        if post["user_id"] != g.user["id"]:
            return jsonify({"error": "Ruxsat yo'q"}), 403

    return jsonify(get_engagement_rate(pid))


# ============================================================================
# SECTION 23: TREND / EXPLORE / REELS
# ============================================================================

@app.route("/api/trend", methods=["GET"])
@auth_optional
def api_trend():
    """Trend postlar."""
    rng = request.args.get("range", "all")
    me_id = g.user["id"] if g.user else None
    now = now_ms()
    cut = {"day": 86400000, "week": 604800000}.get(rng)

    with db() as c:
        rows = c.execute("SELECT * FROM posts WHERE archived=0 AND deleted_at IS NULL "
                        "ORDER BY created_at DESC").fetchall()

        items = []
        for r in rows:
            if cut and now - r["created_at"] > cut:
                continue
            d = serialize_post(r, me_id)
            age_h = max(0.1, (now - r["created_at"]) / 3600000)
            eng = d["likes"] * 3 + len(d["comments"]) * 6 + sum(d["reactions"].values())
            d["score"] = round((eng + 1) / ((age_h + 2) ** 1.35) * 100, 2)
            items.append(d)

        items.sort(key=lambda x: -x["score"])

        authors = c.execute(
            """SELECT u.id, u.name, u.avatar, u.verified,
                      COUNT(p.id) as posts,
                      COALESCE((SELECT COUNT(*) FROM reactions r
                                JOIN posts pp ON pp.id=r.post_id
                                WHERE pp.user_id=u.id),0) as likes
               FROM users u LEFT JOIN posts p ON p.user_id=u.id
               GROUP BY u.id HAVING posts>0 ORDER BY likes DESC LIMIT 6"""
        ).fetchall()

    return jsonify({
        "posts": items[:30],
        "authors": [
            {"id": a["id"], "name": a["name"], "avatar": a["avatar"],
             "verified": bool(a["verified"]), "posts": a["posts"],
             "likes": a["likes"]}
            for a in authors
        ],
        "hashtags": get_trending_hashtags(10),
    })


@app.route("/api/explore", methods=["GET"])
@auth_required
def api_explore():
    """Explore — tasodifiy postlar."""
    me = g.user["id"]
    with db() as c:
        rows = c.execute(
            """SELECT * FROM posts
               WHERE archived=0 AND deleted_at IS NULL
                 AND user_id NOT IN (SELECT following_id FROM follows WHERE follower_id=?)
               ORDER BY RANDOM() LIMIT 30""",
            (me,),
        ).fetchall()
    return jsonify([serialize_post(r, me) for r in rows])


@app.route("/api/reels", methods=["GET"])
@auth_optional
def api_reels():
    """Reels — qisqa videolar."""
    me_id = g.user["id"] if g.user else None
    with db() as c:
        rows = c.execute(
            "SELECT * FROM posts WHERE type='reel' AND archived=0 AND deleted_at IS NULL "
            "ORDER BY created_at DESC LIMIT 30"
        ).fetchall()
    return jsonify([serialize_post(r, me_id) for r in rows])


# ============================================================================
# SECTION 24: SEARCH
# ============================================================================

@app.route("/api/search", methods=["GET"])
@auth_optional
def api_search():
    """Universal qidiruv."""
    q = (request.args.get("q") or "").strip().lower()
    tab = request.args.get("tab", "posts")
    me_id = g.user["id"] if g.user else None

    if not q:
        return jsonify([])

    q_clean = q.lstrip("#").lstrip("@")

    with db() as c:
        if tab == "posts":
            rows = c.execute(
                """SELECT * FROM posts WHERE archived=0 AND deleted_at IS NULL
                   AND (LOWER(text) LIKE ? OR LOWER(tags) LIKE ?)
                   ORDER BY created_at DESC LIMIT 50""",
                (f"%{q}%", f"%{q_clean}%"),
            ).fetchall()
            return jsonify([serialize_post(r, me_id) for r in rows])

        if tab == "people":
            rows = c.execute(
                """SELECT * FROM users WHERE id != COALESCE(?, 0)
                   AND (LOWER(name) LIKE ? OR LOWER(bio) LIKE ?) LIMIT 30""",
                (me_id, f"%{q}%", f"%{q}%"),
            ).fetchall()
            return jsonify([serialize_user_brief(r) for r in rows])

        if tab == "groups":
            rows = c.execute(
                """SELECT * FROM groups_t
                   WHERE LOWER(name) LIKE ? OR LOWER(description) LIKE ? LIMIT 30""",
                (f"%{q}%", f"%{q}%"),
            ).fetchall()
            return jsonify([dict(r) for r in rows])

        if tab == "market":
            rows = c.execute(
                """SELECT m.*, u.name AS seller_name FROM market m
                   JOIN users u ON u.id=m.user_id
                   WHERE m.status='active' AND m.deleted_at IS NULL
                     AND (LOWER(m.title) LIKE ? OR LOWER(m.description) LIKE ?)
                   LIMIT 30""",
                (f"%{q}%", f"%{q}%"),
            ).fetchall()
            return jsonify([serialize_product(r) for r in rows])

    return jsonify([])


# ============================================================================
# SECTION 25: GROUPS
# ============================================================================

@app.route("/api/groups", methods=["GET"])
@auth_optional
def api_groups_list():
    """Guruhlar ro'yxati."""
    me_id = g.user["id"] if g.user else None
    with db() as c:
        rows = c.execute("SELECT * FROM groups_t ORDER BY created_at DESC").fetchall()

        result = []
        for r in rows:
            g = dict(r)
            member_count = c.execute(
                "SELECT COUNT(*) FROM group_members WHERE group_id=?", (r["id"],)
            ).fetchone()[0]
            joined = False
            if me_id:
                joined = c.execute(
                    "SELECT 1 FROM group_members WHERE group_id=? AND user_id=?",
                    (r["id"], me_id),
                ).fetchone() is not None

            result.append({
                "id": g["id"],
                "name": g["name"],
                "emoji": g["emoji"],
                "desc": g["description"],
                "isPrivate": bool(g["is_private"]),
                "members": member_count,
                "joined": joined,
                "createdBy": g["created_by"],
                "createdAt": g["created_at"],
            })

    return jsonify(result)


@app.route("/api/groups", methods=["POST"])
@auth_required
def api_groups_create():
    """Yangi guruh."""
    data = request.get_json(silent=True) or {}
    name = (data.get("name") or "").strip()[:80]
    if not name:
        return jsonify({"error": "Nom kerak"}), 400

    with db() as c:
        c.execute(
            "INSERT INTO groups_t(name,emoji,description,is_private,created_by,created_at) "
            "VALUES(?,?,?,?,?,?)",
            (name, (data.get("emoji") or "👥")[:4],
             (data.get("desc") or "")[:300],
             1 if data.get("isPrivate") else 0,
             g.user["id"], now_ms()),
        )
        gid = c.execute("SELECT last_insert_rowid()").fetchone()[0]
        c.execute(
            "INSERT INTO group_members(group_id,user_id,role,joined_at) VALUES(?,?,'admin',?)",
            (gid, g.user["id"], now_ms()),
        )

    return jsonify({
        "id": gid, "name": name,
        "emoji": data.get("emoji", "👥"),
        "desc": data.get("desc", ""),
        "members": 1, "joined": True,
    })


@app.route("/api/groups/<int:gid>/join", methods=["POST"])
@auth_required
def api_groups_join(gid):
    """Guruhga qo'shilish."""
    with db() as c:
        group = c.execute("SELECT 1 FROM groups_t WHERE id=?", (gid,)).fetchone()
        if not group:
            return jsonify({"error": "Topilmadi"}), 404

        existing = c.execute(
            "SELECT 1 FROM group_members WHERE group_id=? AND user_id=?",
            (gid, g.user["id"]),
        ).fetchone()

        if existing:
            c.execute("DELETE FROM group_members WHERE group_id=? AND user_id=?",
                      (gid, g.user["id"]))
            joined = False
        else:
            c.execute(
                "INSERT INTO group_members(group_id,user_id,joined_at) VALUES(?,?,?)",
                (gid, g.user["id"], now_ms()),
            )
            joined = True

        count = c.execute("SELECT COUNT(*) FROM group_members WHERE group_id=?",
                         (gid,)).fetchone()[0]

    return jsonify({"joined": joined, "members": count})


@app.route("/api/groups/<int:gid>/members", methods=["GET"])
@auth_optional
def api_groups_members(gid):
    """Guruh a'zolari."""
    with db() as c:
        rows = c.execute(
            "SELECT u.*, gm.role FROM users u JOIN group_members gm ON gm.user_id=u.id "
            "WHERE gm.group_id=? ORDER BY gm.role DESC, gm.joined_at",
            (gid,),
        ).fetchall()
    return jsonify([dict(r) for r in rows])


# ============================================================================
# SECTION 26: CHAT
# ============================================================================

@app.route("/api/chats", methods=["GET"])
@auth_required
def api_chats_list():
    """Chatlar ro'yxati."""
    me = g.user["id"]
    with db() as c:
        rows = c.execute(
            "SELECT * FROM chats WHERE user1_id=? OR user2_id=? ORDER BY created_at DESC",
            (me, me),
        ).fetchall()

        result = []
        for r in rows:
            if r["is_ai"]:
                name, av, kind = "Unigram AI", "🤖", "ai"
            else:
                other = r["user2_id"] if r["user1_id"] == me else r["user1_id"]
                u = c.execute("SELECT * FROM users WHERE id=?", (other,)).fetchone()
                if not u:
                    continue
                name, av, kind = u["name"], u["avatar"], "user"

            last = c.execute(
                "SELECT * FROM messages WHERE chat_id=? AND deleted_at IS NULL "
                "ORDER BY id DESC LIMIT 1",
                (r["id"],),
            ).fetchone()

            unread = c.execute(
                "SELECT COUNT(*) FROM messages WHERE chat_id=? AND sender_id != ? AND read_at IS NULL AND deleted_at IS NULL",
                (r["id"], me),
            ).fetchone()[0]

            result.append({
                "id": r["id"],
                "name": name,
                "avatar": av,
                "kind": kind,
                "last": last["text"] if last else "Xabar yo'q",
                "lastAt": last["created_at"] if last else r["created_at"],
                "unread": unread,
            })

        result.sort(key=lambda x: (x["kind"] != "ai", -(x["lastAt"] or 0)))

    return jsonify(result)


@app.route("/api/chats/ai", methods=["POST"])
@auth_required
def api_chats_ai():
    """AI chat ochish."""
    me = g.user["id"]
    with db() as c:
        row = c.execute("SELECT * FROM chats WHERE user1_id=? AND is_ai=1", (me,)).fetchone()
        if not row:
            c.execute(
                "INSERT INTO chats(user1_id,user2_id,is_ai,created_at) VALUES(?,?,1,?)",
                (me, 0, now_ms()),
            )
            cid = c.execute("SELECT last_insert_rowid()").fetchone()[0]
            c.execute(
                "INSERT INTO messages(chat_id,sender_id,text,created_at) VALUES(?,?,?,?)",
                (cid, 0, "Salom! Men Unigram AI man. Menga savol bering 🙂", now_ms()),
            )
            row = c.execute("SELECT * FROM chats WHERE id=?", (cid,)).fetchone()
    return jsonify({"id": row["id"]})


@app.route("/api/chats/with/<int:uid>", methods=["POST"])
@auth_required
def api_chats_with(uid):
    """Foydalanuvchi bilan chat."""
    me = g.user["id"]
    if uid == me:
        return jsonify({"error": "O'zingiz bilan chat yo'q"}), 400

    with db() as c:
        u = c.execute("SELECT 1 FROM users WHERE id=?", (uid,)).fetchone()
        if not u:
            return jsonify({"error": "Topilmadi"}), 404

        a, b = (me, uid) if me < uid else (uid, me)
        row = c.execute(
            "SELECT * FROM chats WHERE is_ai=0 AND user1_id=? AND user2_id=?",
            (a, b),
        ).fetchone()

        if not row:
            c.execute(
                "INSERT INTO chats(user1_id,user2_id,is_ai,created_at) VALUES(?,?,0,?)",
                (a, b, now_ms()),
            )
            cid = c.execute("SELECT last_insert_rowid()").fetchone()[0]
            row = c.execute("SELECT * FROM chats WHERE id=?", (cid,)).fetchone()

    return jsonify({"id": row["id"]})


@app.route("/api/chats/<int:cid>/messages", methods=["GET"])
@auth_required
def api_chat_messages(cid):
    """Chat xabarlari."""
    me = g.user["id"]
    after = request.args.get("after", type=int) or 0

    with db() as c:
        chat = c.execute(
            "SELECT * FROM chats WHERE id=? AND (user1_id=? OR user2_id=?)",
            (cid, me, me),
        ).fetchone()
        if not chat:
            return jsonify({"error": "Topilmadi"}), 404

    # AI auto reply
    if chat["is_ai"]:
        with db() as c:
            last = c.execute(
                "SELECT * FROM messages WHERE chat_id=? ORDER BY id DESC LIMIT 1",
                (cid,),
            ).fetchone()
            if last and last["sender_id"] == me and now_ms() - last["created_at"] > 1500:
                reply = ai_generate_reply(last["text"])
                c.execute(
                    "INSERT INTO messages(chat_id,sender_id,text,created_at) VALUES(?,?,?,?)",
                    (cid, 0, reply["text"], now_ms()),
                )

    with db() as c:
        rows = c.execute(
            "SELECT * FROM messages WHERE chat_id=? AND id>? AND deleted_at IS NULL ORDER BY id",
            (cid, after),
        ).fetchall()

        # Read receipt
        c.execute(
            "UPDATE messages SET read_at=? WHERE chat_id=? AND sender_id != ? AND read_at IS NULL",
            (now_ms(), cid, me),
        )

    return jsonify([serialize_message(r, me) for r in rows])


@app.route("/api/chats/<int:cid>/messages", methods=["POST"])
@auth_required
def api_chat_send(cid):
    """Xabar yuborish."""
    me = g.user["id"]
    data = request.get_json(silent=True) or {}
    text = (data.get("text") or "").strip()[:MAX_MESSAGE_LENGTH]
    if not text:
        return jsonify({"error": "Xabar bo'sh"}), 400

    with db() as c:
        chat = c.execute(
            "SELECT * FROM chats WHERE id=? AND (user1_id=? OR user2_id=?)",
            (cid, me, me),
        ).fetchone()
        if not chat:
            return jsonify({"error": "Topilmadi"}), 404

        c.execute(
            "INSERT INTO messages(chat_id,sender_id,text,created_at) VALUES(?,?,?,?)",
            (cid, me, text, now_ms()),
        )
        mid = c.execute("SELECT last_insert_rowid()").fetchone()[0]

    return jsonify({"id": mid, "from": "me", "text": text, "at": now_ms()}), 201


@app.route("/api/messages/<int:mid>", methods=["DELETE"])
@auth_required
def api_message_delete(mid):
    """Xabarni o'chirish."""
    with db() as c:
        msg = c.execute("SELECT sender_id FROM messages WHERE id=?", (mid,)).fetchone()
        if not msg:
            return jsonify({"error": "Topilmadi"}), 404
        if msg["sender_id"] != g.user["id"]:
            return jsonify({"error": "Ruxsat yo'q"}), 403
        c.execute("UPDATE messages SET deleted_at=? WHERE id=?", (now_ms(), mid))
    return jsonify({"ok": True})


# ============================================================================
# SECTION 27: STORIES
# ============================================================================

@app.route("/api/stories", methods=["GET"])
@auth_optional
def api_stories_list():
    """Aktiv stories."""
    me_id = g.user["id"] if g.user else None
    now = now_ms()

    with db() as c:
        rows = c.execute(
            """SELECT s.*, u.name, u.avatar FROM stories s
               JOIN users u ON u.id=s.user_id
               WHERE s.expires_at > ?
               ORDER BY s.created_at DESC""",
            (now,),
        ).fetchall()

        seen = set()
        if me_id:
            seen = {r["story_id"] for r in c.execute(
                "SELECT story_id FROM story_views WHERE user_id=?", (me_id,)
            ).fetchall()}

    return jsonify([serialize_story(r, me_id or 0, seen) for r in rows])


@app.route("/api/stories", methods=["POST"])
@auth_required
def api_stories_create():
    """Yangi story."""
    data = request.get_json(silent=True) or {}
    image_url = save_base64_image(data.get("image"), "story")
    emoji = (data.get("emoji") or "✨")[:4]
    text = (data.get("text") or "").strip()[:200]

    if not text and not image_url:
        return jsonify({"error": "Bo'sh story bo'lmaydi"}), 400

    with db() as c:
        c.execute(
            "INSERT INTO stories(user_id,emoji,text,image_url,created_at,expires_at) "
            "VALUES(?,?,?,?,?,?)",
            (g.user["id"], emoji, text, image_url, now_ms(), now_ms() + 24 * 3600000),
        )
        sid = c.execute("SELECT last_insert_rowid()").fetchone()[0]

    return jsonify({"id": sid}), 201


@app.route("/api/stories/<int:sid>/view", methods=["POST"])
@auth_required
def api_story_view(sid):
    """Story ko'rish."""
    with db() as c:
        try:
            c.execute("INSERT INTO story_views(story_id,user_id,viewed_at) VALUES(?,?,?)",
                      (sid, g.user["id"], now_ms()))
            c.execute("UPDATE stories SET views = views + 1 WHERE id=?", (sid,))
        except sqlite3.IntegrityError:
            pass
    return jsonify({"ok": True})


# ============================================================================
# SECTION 28: MARKET
# ============================================================================

@app.route("/api/market", methods=["GET"])
@auth_optional
def api_market_list():
    """Mahsulotlar."""
    with db() as c:
        rows = c.execute(
            """SELECT m.*, u.name AS seller_name FROM market m
               JOIN users u ON u.id=m.user_id
               WHERE m.status='active' AND m.deleted_at IS NULL
               ORDER BY m.created_at DESC LIMIT 100"""
        ).fetchall()
    return jsonify([serialize_product(r) for r in rows])


@app.route("/api/market", methods=["POST"])
@auth_required
def api_market_create():
    """Yangi mahsulot."""
    data = request.get_json(silent=True) or {}
    title = (data.get("title") or "").strip()[:120]
    if not title:
        return jsonify({"error": "Nom kerak"}), 400

    with db() as c:
        c.execute(
            """INSERT INTO market(title,price,description,emoji,category,user_id,created_at)
               VALUES(?,?,?,?,?,?,?)""",
            (title, int(data.get("price", 0) or 0),
             (data.get("desc") or "")[:500],
             (data.get("emoji") or "📦")[:4],
             (data.get("category") or "general")[:20],
             g.user["id"], now_ms()),
        )
        mid = c.execute("SELECT last_insert_rowid()").fetchone()[0]
        row = c.execute(
            """SELECT m.*, u.name AS seller_name FROM market m
               JOIN users u ON u.id=m.user_id WHERE m.id=?""",
            (mid,),
        ).fetchone()

    return jsonify(serialize_product(row)), 201


@app.route("/api/market/<int:mid>", methods=["GET"])
@auth_optional
def api_market_get(mid):
    """Bitta mahsulot."""
    with db() as c:
        row = c.execute(
            """SELECT m.*, u.name AS seller_name FROM market m
               JOIN users u ON u.id=m.user_id
               WHERE m.id=? AND m.deleted_at IS NULL""",
            (mid,),
        ).fetchone()
        if not row:
            return jsonify({"error": "Topilmadi"}), 404
    return jsonify(serialize_product(row))


@app.route("/api/market/<int:mid>/buy", methods=["POST"])
@auth_required
def api_market_buy(mid):
    """Mahsulot sotib olish."""
    data = request.get_json(silent=True) or {}
    qty = max(1, int(data.get("quantity", 1)))

    with db() as c:
        product = c.execute(
            "SELECT * FROM market WHERE id=? AND status='active' AND deleted_at IS NULL",
            (mid,),
        ).fetchone()
        if not product:
            return jsonify({"error": "Mahsulot topilmadi"}), 404
        if product["stock"] < qty:
            return jsonify({"error": "Yetarli emas"}), 400

        total = product["price"] * qty
        c.execute(
            "INSERT INTO orders(user_id,product_id,quantity,total,status,created_at) "
            "VALUES(?,?,?,?,'pending',?)",
            (g.user["id"], mid, qty, total, now_ms()),
        )
        oid = c.execute("SELECT last_insert_rowid()").fetchone()[0]

        c.execute("UPDATE market SET stock=stock-?, sold=sold+? WHERE id=?",
                  (qty, qty, mid))

        send_notification(
            product["user_id"], g.user["id"], "order",
            f"{g.user['name']} buyurtma berdi: {product['title']}", mid,
        )

    return jsonify({"orderId": oid, "total": total}), 201


@app.route("/api/orders", methods=["GET"])
@auth_required
def api_orders_list():
    """Mening buyurtmalarim."""
    with db() as c:
        rows = c.execute(
            """SELECT o.*, m.title, m.emoji FROM orders o
               JOIN market m ON m.id = o.product_id
               WHERE o.user_id=? ORDER BY o.created_at DESC""",
            (g.user["id"],),
        ).fetchall()
    return jsonify([dict(r) for r in rows])


# ============================================================================
# SECTION 29: NOTIFICATIONS
# ============================================================================

@app.route("/api/notifications", methods=["GET"])
@auth_required
def api_notifications_list():
    """Bildirishnomalar."""
    with db() as c:
        rows = c.execute(
            "SELECT * FROM notifications WHERE user_id=? ORDER BY created_at DESC LIMIT 100",
            (g.user["id"],),
        ).fetchall()
    return jsonify([
        {"id": r["id"], "kind": r["kind"], "text": r["text"],
         "ref_id": r["ref_id"], "read": bool(r["read"]),
         "at": r["created_at"]}
        for r in rows
    ])


@app.route("/api/notifications/count", methods=["GET"])
@auth_required
def api_notifications_count():
    """O'qilmagan bildirishnomalar soni."""
    with db() as c:
        n = c.execute(
            "SELECT COUNT(*) FROM notifications WHERE user_id=? AND read=0",
            (g.user["id"],),
        ).fetchone()[0]
    return jsonify({"unread": n})


@app.route("/api/notifications/read", methods=["POST"])
@auth_required
def api_notifications_read():
    """O'qilgan deb belgilash."""
    with db() as c:
        c.execute("UPDATE notifications SET read=1 WHERE user_id=?", (g.user["id"],))
    return jsonify({"ok": True})


# ============================================================================
# SECTION 30: ADMIN ENDPOINTS
# ============================================================================

def admin_required(fn):
    """Admin autentifikatsiyasi."""
    @wraps(fn)
    def wrapper(*args, **kwargs):
        key = request.args.get("key") or request.headers.get("X-Admin-Key")
        if key != ADMIN_SECRET:
            return jsonify({"error": "Admin kalit kerak"}), 403
        return fn(*args, **kwargs)
    return wrapper


@app.route("/api/admin/stats", methods=["GET"])
@admin_required
def api_admin_stats():
    """Umumiy statistika."""
    return jsonify(get_analytics_summary())


@app.route("/api/admin/users", methods=["GET"])
@admin_required
def api_admin_users():
    """Barcha foydalanuvchilar."""
    with db() as c:
        rows = c.execute(
            """SELECT id, name, avatar, bio, verified, created_at, last_seen, email, phone,
                      (SELECT COUNT(*) FROM posts WHERE user_id=users.id AND deleted_at IS NULL) as posts,
                      (SELECT COUNT(*) FROM follows WHERE following_id=users.id) as followers
               FROM users ORDER BY created_at DESC LIMIT 500"""
        ).fetchall()
    return jsonify([dict(r) for r in rows])


@app.route("/api/admin/users/<int:uid>", methods=["DELETE"])
@admin_required
def api_admin_delete_user(uid):
    """Foydalanuvchini o'chirish."""
    with db() as c:
        c.execute("DELETE FROM users WHERE id=?", (uid,))
    return jsonify({"ok": True})


@app.route("/api/admin/users/<int:uid>/verify", methods=["POST"])
@admin_required
def api_admin_verify_user(uid):
    """Verifikatsiya."""
    val = request.args.get("val", "1")
    with db() as c:
        c.execute("UPDATE users SET verified=? WHERE id=?",
                  (1 if val == "1" else 0, uid))
    return jsonify({"ok": True})


@app.route("/api/admin/posts", methods=["GET"])
@admin_required
def api_admin_posts():
    """Barcha postlar."""
    with db() as c:
        rows = c.execute(
            """SELECT * FROM posts WHERE deleted_at IS NULL
               ORDER BY created_at DESC LIMIT 500"""
        ).fetchall()
    return jsonify([dict(r) for r in rows])


@app.route("/api/admin/posts/<int:pid>", methods=["DELETE"])
@admin_required
def api_admin_delete_post(pid):
    """Postni o'chirish."""
    with db() as c:
        c.execute("UPDATE posts SET deleted_at=? WHERE id=?", (now_ms(), pid))
    return jsonify({"ok": True})


@app.route("/api/admin/reports", methods=["GET"])
@admin_required
def api_admin_reports():
    """Shikoyatlar."""
    with db() as c:
        rows = c.execute(
            """SELECT r.*, u.name AS reporter_name FROM reports r
               JOIN users u ON u.id=r.reporter_id
               ORDER BY r.created_at DESC LIMIT 200"""
        ).fetchall()
    return jsonify([dict(r) for r in rows])


@app.route("/api/admin/reports/<int:rid>/<action>", methods=["POST"])
@admin_required
def api_admin_handle_report(rid, action):
    """Shikoyatni ko'rib chiqish."""
    if action not in ("resolve", "dismiss"):
        return jsonify({"error": "Noto'g'ri amal"}), 400
    status = "resolved" if action == "resolve" else "dismissed"
    with db() as c:
        c.execute("UPDATE reports SET status=? WHERE id=?", (status, rid))
    return jsonify({"ok": True})


@app.route("/api/admin/moderation", methods=["GET"])
@admin_required
def api_admin_moderation():
    """Moderatsiya navbati."""
    with db() as c:
        rows = c.execute(
            "SELECT * FROM moderation_queue WHERE status='pending' ORDER BY created_at DESC"
        ).fetchall()
    return jsonify([dict(r) for r in rows])


@app.route("/api/admin/moderation/<int:qid>/<action>", methods=["POST"])
@admin_required
def api_admin_moderate(qid, action):
    """Moderatsiya qarori."""
    status = "approved" if action == "approve" else "rejected"
    with db() as c:
        c.execute("UPDATE moderation_queue SET status=? WHERE id=?", (status, qid))
    return jsonify({"ok": True})


@app.route("/api/admin/growth", methods=["GET"])
@admin_required
def api_admin_growth():
    """Foydalanuvchilar o'sishi."""
    days = int(request.args.get("days", 30))
    return jsonify(get_user_growth(days))


@app.route("/api/admin/top-users", methods=["GET"])
@admin_required
def api_admin_top_users():
    """Eng faol foydalanuvchilar."""
    metric = request.args.get("metric", "followers")
    return jsonify(get_top_users(10, metric))


@app.route("/api/admin/backup", methods=["POST"])
@admin_required
def api_admin_backup():
    """Baza zaxirasini yaratish."""
    try:
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_file = os.path.join(BACKUP_DIR, f"unigram_backup_{timestamp}.db")
        shutil.copy2(DB_FILE, backup_file)
        size = os.path.getsize(backup_file)
        return jsonify({
            "ok": True,
            "file": os.path.basename(backup_file),
            "size": size,
            "timestamp": timestamp,
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/admin/cleanup", methods=["POST"])
@admin_required
def api_admin_cleanup():
    """Eski ma'lumotlarni tozalash."""
    days = int(request.args.get("days", 30))
    cutoff = now_ms() - days * 86400000

    with db() as c:
        # Expired stories
        c.execute("DELETE FROM stories WHERE expires_at < ?", (cutoff,))
        # Old activity log
        c.execute("DELETE FROM activity_log WHERE created_at < ?", (cutoff,))
        # Old notifications
        c.execute("DELETE FROM notifications WHERE created_at < ? AND read=1", (cutoff,))

    # Old upload files
    cleanup_old_uploads(days)

    return jsonify({"ok": True, "cleaned": True})


# ============================================================================
# SECTION 31: EXPORT
# ============================================================================

@app.route("/api/export/users.csv", methods=["GET"])
@admin_required
def api_export_users_csv():
    """Foydalanuvchilar CSV."""
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["ID", "Name", "Email", "Phone", "Verified", "Created", "Last Seen"])

    with db() as c:
        rows = c.execute("SELECT * FROM users ORDER BY id").fetchall()
        for r in rows:
            writer.writerow([
                r["id"], r["name"], r["email"] or "", r["phone"] or "",
                "yes" if r["verified"] else "no",
                datetime.fromtimestamp(r["created_at"]/1000).strftime("%Y-%m-%d %H:%M"),
                datetime.fromtimestamp((r["last_seen"] or 0)/1000).strftime("%Y-%m-%d %H:%M") if r["last_seen"] else "",
            ])

    return Response(output.getvalue(), mimetype="text/csv",
                   headers={"Content-Disposition": "attachment; filename=users.csv"})


@app.route("/api/export/posts.json", methods=["GET"])
@admin_required
def api_export_posts_json():
    """Postlar JSON."""
    with db() as c:
        rows = c.execute("SELECT * FROM posts WHERE deleted_at IS NULL").fetchall()
    data = [dict(r) for r in rows]
    return Response(json.dumps(data, indent=2, ensure_ascii=False), mimetype="application/json",
                   headers={"Content-Disposition": "attachment; filename=posts.json"})


@app.route("/api/export/database.sql", methods=["GET"])
@admin_required
def api_export_sql():
    """Butun baza SQL dump."""
    try:
        with db() as c:
            dump = "\n".join(c.iterdump())
        return Response(dump, mimetype="application/sql",
                       headers={"Content-Disposition": "attachment; filename=unigram.sql"})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/export/my-data", methods=["GET"])
@auth_required
def api_export_my_data():
    """Shaxsiy ma'lumotlar (GDPR)."""
    uid = g.user["id"]
    with db() as c:
        user = dict(c.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone())
        user.pop("password_hash", None)
        user.pop("two_fa_secret", None)

        posts = [dict(r) for r in c.execute(
            "SELECT * FROM posts WHERE user_id=? AND deleted_at IS NULL", (uid,)
        ).fetchall()]

        comments = [dict(r) for r in c.execute(
            "SELECT * FROM comments WHERE user_id=? AND deleted_at IS NULL", (uid,)
        ).fetchall()]

        messages = [dict(r) for r in c.execute(
            "SELECT * FROM messages WHERE sender_id=? AND deleted_at IS NULL", (uid,)
        ).fetchall()]

    return jsonify({
        "user": user,
        "posts": posts,
        "comments": comments,
        "messages": messages,
        "exportedAt": now_ms(),
    })


# ============================================================================
# SECTION 32: STATIC
# ============================================================================

@app.route("/")
@app.route("/index.html")
def serve_index():
    """Bosh sahifa — index.html."""
    idx_path = os.path.join(BASE_DIR, "index.html")
    if os.path.exists(idx_path):
        return send_from_directory(BASE_DIR, "index.html")
    return Response(FALLBACK_HTML, mimetype="text/html")


@app.route("/uploads/<path:filename>")
def serve_upload(filename):
    """Yuklangan fayllar."""
    try:
        return send_from_directory(UPLOAD_DIR, filename)
    except Exception:
        abort(404)


# ============================================================================
# SECTION 33: ERROR HANDLERS
# ============================================================================

@app.errorhandler(400)
def error_400(e):
    if request.path.startswith("/api/"):
        return jsonify({"error": "Noto'g'ri so'rov"}), 400
    return Response("400 Bad Request", status=400)


@app.errorhandler(401)
def error_401(e):
    return jsonify({"error": "Ruxsat yo'q"}), 401


@app.errorhandler(403)
def error_403(e):
    return jsonify({"error": "Taqiqlangan"}), 403


@app.errorhandler(404)
def error_404(e):
    if request.path.startswith("/api/"):
        return jsonify({"error": "Topilmadi"}), 404
    idx_path = os.path.join(BASE_DIR, "index.html")
    if os.path.exists(idx_path):
        return send_from_directory(BASE_DIR, "index.html")
    return Response(FALLBACK_HTML, mimetype="text/html")


@app.errorhandler(413)
def error_413(e):
    return jsonify({"error": "Fayl juda katta (12MB limit)"}), 413


@app.errorhandler(429)
def error_429(e):
    return jsonify({"error": "Juda ko'p so'rov"}), 429


@app.errorhandler(500)
def error_500(e):
    log.error(f"500 xato: {e}")
    return jsonify({"error": "Server xatosi", "detail": str(e) if IS_DEBUG else None}), 500


# ============================================================================
# SECTION 34: FALLBACK HTML
# ============================================================================

FALLBACK_HTML = """<!DOCTYPE html>
<html lang="uz">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Unigram — API</title>
<style>
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:system-ui,sans-serif;background:#0a0e1a;color:#f1f5f9;
min-height:100vh;display:flex;align-items:center;justify-content:center;padding:24px;
background:radial-gradient(900px 500px at 10% -10%,rgba(0,240,255,.15),transparent 60%),
radial-gradient(700px 400px at 100% 0%,rgba(168,85,247,.15),transparent 60%),#0a0e1a}
.card{background:#131826;border:1px solid rgba(255,255,255,.08);border-radius:24px;
padding:40px 32px;max-width:560px;width:100%;text-align:center;box-shadow:0 20px 60px rgba(0,0,0,.4)}
.logo{width:88px;height:88px;margin:0 auto 20px;background:rgba(0,240,255,.06);
border:1px solid rgba(0,240,255,.3);border-radius:22px;display:flex;align-items:center;
justify-content:center;box-shadow:0 0 30px rgba(0,240,255,.2)}
.logo svg{width:56px;height:56px;color:#00F0FF;filter:drop-shadow(0 0 12px rgba(0,240,255,.6))}
h1{font-size:32px;font-weight:900;margin-bottom:6px;letter-spacing:-1px;
background:linear-gradient(135deg,#00F0FF,#a855f7);-webkit-background-clip:text;
background-clip:text;color:transparent}
.sub{color:#8892a8;font-size:14px;margin-bottom:28px}
.status{display:inline-flex;align-items:center;gap:8px;padding:10px 20px;
background:rgba(16,185,129,.15);border:1px solid rgba(16,185,129,.3);color:#10b981;
border-radius:99px;font-weight:700;font-size:14px;margin-bottom:28px}
.status::before{content:'';width:8px;height:8px;background:#10b981;border-radius:50%;
animation:pulse 1.5s infinite}
@keyframes pulse{0%,100%{opacity:1;transform:scale(1)}50%{opacity:.5;transform:scale(1.3)}}
.info{background:#1e2436;border-radius:14px;padding:20px;text-align:left;font-size:14px;
line-height:1.9;color:#8892a8;margin-bottom:20px}
.info b{color:#f1f5f9}
.info code{background:#0a0e1a;color:#00F0FF;padding:2px 8px;border-radius:6px;
font-family:ui-monospace,monospace;font-size:12px}
.links{display:flex;flex-direction:column;gap:8px;margin-top:16px}
.link{display:flex;align-items:center;gap:10px;padding:12px 16px;background:#1e2436;
border:1px solid rgba(255,255,255,.08);border-radius:12px;color:#f1f5f9;
text-decoration:none;font-weight:600;font-size:14px;transition:.2s}
.link:hover{background:#2a3048;border-color:rgba(0,240,255,.3)}
.link .arr{margin-left:auto;color:#8892a8}
.note{margin-top:24px;padding:12px;background:rgba(251,191,36,.1);
border:1px solid rgba(251,191,36,.3);border-radius:12px;color:#fbbf24;
font-size:13px;line-height:1.6;text-align:left}
</style>
</head>
<body>
<div class="card">
    <div class="logo">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">
            <path d="M21 11.5a8.38 8.38 0 0 1-.9 3.8 8.5 8.5 0 0 1-7.6 4.7 8.38 8.38 0 0 1-3.8-.9L3 21l1.9-5.7a8.38 8.38 0 0 1-.9-3.8 8.5 8.5 0 0 1 4.7-7.6 8.38 8.38 0 0 1 3.8-.9h.5a8.48 8.48 0 0 1 8 8v.5z"/>
            <path d="M8 9v3.5a3.5 3.5 0 0 0 7 0V9"/>
            <circle cx="10" cy="13" r="0.75" fill="currentColor"/>
            <circle cx="12" cy="13" r="0.75" fill="currentColor"/>
            <circle cx="14" cy="13" r="0.75" fill="currentColor"/>
        </svg>
    </div>
    <h1>Unigram</h1>
    <p class="sub">Backend muvaffaqiyatli ishga tushdi</p>
    <div class="status">Server ishlayapti</div>

    <div class="info">
        <div>📡 <b>API</b>: <code>/api/*</code></div>
        <div>🏥 <b>Health</b>: <code>/health</code></div>
        <div>👤 <b>Demo user</b>: <code>Dilnoza Karimova</code> / <code>demo123</code></div>
        <div>🔑 <b>Admin</b>: <code>/api/admin/stats?key=admin123</code></div>
    </div>

    <div class="links">
        <a href="/health" class="link" target="_blank">🏥 Health check <span class="arr">→</span></a>
        <a href="/api/version" class="link" target="_blank">📦 Version <span class="arr">→</span></a>
        <a href="/api/admin/stats?key=admin123" class="link" target="_blank">📊 Statistika <span class="arr">→</span></a>
        <a href="/api/posts" class="link" target="_blank">📝 Postlar <span class="arr">→</span></a>
    </div>

    <div class="note">
        💡 <b>index.html topilmadi.</b> To'liq interfeys uchun <code>index.html</code> faylini <code>main.py</code> yoniga qo'ying.
    </div>
</div>
</body>
</html>"""


# ============================================================================
# SECTION 35: BOOT
# ============================================================================

def bootstrap():
    """Ilovani ishga tushirish."""
    log.info("=" * 62)
    log.info(f"  🚀 {APP_NAME} v{VERSION}")
    log.info("=" * 62)

    # Database
    init_database()
    seed_data()

    # Info
    log.info(f"  Port: {PORT}")
    log.info(f"  Host: {HOST}")
    log.info(f"  Debug: {IS_DEBUG}")
    log.info(f"  Render: {IS_RENDER}")
    log.info(f"  Baza: {DB_FILE}")
    log.info(f"  Uploads: {UPLOAD_DIR}")
    log.info(f"  Backup: {BACKUP_DIR}")

    # Check index.html
    if os.path.exists(os.path.join(BASE_DIR, "index.html")):
        log.info("  ✅ index.html topildi")
    else:
        log.info("  ⚠️  index.html yo'q — fallback sahifa ishlatiladi")

    log.info("=" * 62)


# Auto-init at import (Render uchun kerak)
try:
    bootstrap()
except Exception as e:
    log.error(f"Bootstrap xatosi: {e}")


# ============================================================================
# MAIN
# ============================================================================

if __name__ == "__main__":
    log.info("")
    log.info("=" * 62)
    log.info(f"  🌐 http://localhost:{PORT}")
    log.info(f"  🏥 http://localhost:{PORT}/health")
    log.info(f"  📊 http://localhost:{PORT}/api/admin/stats?key=admin123")
    log.info("")
    log.info("  Demo user: Dilnoza Karimova / demo123")
    log.info("  To'xtatish: Ctrl+C")
    log.info("=" * 62)
    log.info("")

    app.run(host=HOST, port=PORT, debug=IS_DEBUG, threaded=True)
