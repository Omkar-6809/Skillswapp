"""
SkillSwap - Student Skill Exchange Platform
=============================================
A Flask + PostgreSQL web app that lets students offer skills they know and
find other students who can teach them skills they want to learn.

Run with:  python app.py
Then visit http://127.0.0.1:5000
"""

import os
import re
import json
import time

try:
    import psycopg
    from psycopg.rows import dict_row
except ImportError:
    psycopg = None
    dict_row = None
from functools import wraps

from flask import (
    Flask, render_template, request, redirect,
    url_for, session, flash, g, jsonify
)
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename

# -----------------------------------------------------------------------
# App configuration
# -----------------------------------------------------------------------
app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "skillswap-local-dev-key-2026")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
# Vercel's deployment filesystem is read-only. Use /tmp for uploads there.
# Note: /tmp is ephemeral on serverless platforms; persistent image storage
# should later be moved to object storage if profile uploads are important.
if os.environ.get("VERCEL"):
    DEFAULT_UPLOAD_FOLDER = "/tmp/skillswap_uploads"
else:
    DEFAULT_UPLOAD_FOLDER = os.path.join(BASE_DIR, "static", "images", "uploads")
UPLOAD_FOLDER = os.environ.get("UPLOAD_FOLDER", DEFAULT_UPLOAD_FOLDER)
ALLOWED_EXTENSIONS = {"png", "jpg", "jpeg", "gif", "webp"}

app.config["UPLOAD_FOLDER"]        = UPLOAD_FOLDER
app.config["MAX_CONTENT_LENGTH"]   = 5 * 1024 * 1024

os.makedirs(UPLOAD_FOLDER, exist_ok=True)

# -----------------------------------------------------------------------
# Chat content moderation
# -----------------------------------------------------------------------
_BAD_WORDS = re.compile(
    r'\b(' + '|'.join([
        r'sex', r'porn', r'nude', r'naked', r'nudes', r'boob', r'boobs',
        r'dick', r'cock', r'pussy', r'vagina', r'penis', r'ass(?:hole)?',
        r'f+u+c+k+', r'sh[i1]t', r'b[i1]tch', r'sl[u\*]t', r'wh[o0]re',
        r'horny', r'sexy', r'sexual', r'masturbat', r'orgasm', r'erotic',
        r'onlyfans', r'nsfw', r'rape', r'molest',
    ]) + r')\b',
    re.IGNORECASE
)

def is_clean(text: str) -> bool:
    """Return True if the message contains no banned words."""
    return not _BAD_WORDS.search(text)


# Departments / years
DEPARTMENTS = [
    "Artificial Intelligence", "Computer Science", "Information Technology",
    "Electronics", "Mechanical", "Civil", "Electrical",
    "Business Administration", "Design", "Mathematics", "Other"
]
YEARS = ["1st Year", "2nd Year", "3rd Year", "4th Year", "Postgraduate"]


# -----------------------------------------------------------------------
# Database helpers — PostgreSQL only
# -----------------------------------------------------------------------
# This deployment intentionally uses PostgreSQL only.  Set DATABASE_URL
# in Vercel (for example, to a Neon pooled PostgreSQL connection string).
DATABASE_URL = os.environ.get("DATABASE_URL", "").strip()


class DBConnection:
    """Small compatibility wrapper for the app's existing SQL calls.

    The application uses '?' parameter placeholders in its SQL.  PostgreSQL/psycopg uses '%s', so this wrapper translates
    those placeholders while keeping the rest of the application unchanged.
    """

    def __init__(self, conn):
        self.conn = conn
        self.postgres = True

    def execute(self, sql, params=()):
        sql = sql.replace("?", "%s")
        cur = self.conn.cursor()
        cur.execute(sql, params)

        # The application expects cursor.lastrowid after INSERTs.
        # PostgreSQL has no native lastrowid, so obtain the ID generated
        # by the sequence on this same connection.
        lastrowid = None
        if sql.lstrip().upper().startswith("INSERT"):
            try:
                id_cur = self.conn.cursor()
                id_cur.execute("SELECT LASTVAL() AS lastval")
                row = id_cur.fetchone()
                if row:
                    lastrowid = row["lastval"] if isinstance(row, dict) else row[0]
                id_cur.close()
            except Exception:
                # Keep the INSERT result usable even if an ID cannot be read.
                pass

        return PGCursor(cur, lastrowid)

    def commit(self):
        self.conn.commit()

    def close(self):
        self.conn.close()


class PGCursor:
    def __init__(self, cursor, lastrowid=None):
        self.cursor = cursor
        self.lastrowid = lastrowid

    def __iter__(self):
        # The app iterates directly over db.execute(...) in several places.
        return iter(self.cursor)

    def fetchone(self):
        return self.cursor.fetchone()

    def fetchall(self):
        return self.cursor.fetchall()

    def close(self):
        self.cursor.close()


import threading

# Vercel serverless instances can remain warm. Reuse a PostgreSQL connection
# within a warm instance and reconnect if it becomes invalid.
_pg_conn = None
_pg_conn_lock = threading.Lock()


def _get_pg_connection():
    """Return a live PostgreSQL connection for this warm serverless instance."""
    global _pg_conn

    if not DATABASE_URL:
        raise RuntimeError(
            "DATABASE_URL is not set. Add your Neon PostgreSQL connection "
            "string as a Vercel environment variable."
        )
    if psycopg is None:
        raise RuntimeError(
            "psycopg is required. Install the dependencies from requirements.txt."
        )

    with _pg_conn_lock:
        if _pg_conn is not None:
            try:
                _pg_conn.execute("SELECT 1")
                _pg_conn.commit()
                return _pg_conn
            except Exception:
                try:
                    _pg_conn.close()
                except Exception:
                    pass
                _pg_conn = None

        _pg_conn = psycopg.connect(
            DATABASE_URL,
            row_factory=dict_row,
            connect_timeout=10,
        )
        _pg_conn.autocommit = False
        return _pg_conn


def get_db():
    """Return the PostgreSQL connection wrapper for the current Flask request."""
    db = getattr(g, "_database", None)
    if db is None:
        db = g._database = DBConnection(_get_pg_connection())
    return db


@app.teardown_appcontext
def close_db(exception=None):
    """Rollback failed/incomplete transactions on the shared connection."""
    db = getattr(g, "_database", None)
    if db is None:
        return

    try:
        db.conn.rollback()
    except Exception:
        global _pg_conn
        try:
            db.conn.close()
        except Exception:
            pass
        _pg_conn = None


def init_db():
    """Create the complete PostgreSQL schema when the database is fresh."""
    if not DATABASE_URL:
        raise RuntimeError(
            "DATABASE_URL is not set. Configure it in Vercel Environment Variables."
        )
    if psycopg is None:
        raise RuntimeError("Install psycopg[binary] when using DATABASE_URL.")

    conn = psycopg.connect(DATABASE_URL, row_factory=dict_row, connect_timeout=10)
    cur = conn.cursor()
    try:
        cur.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id BIGSERIAL PRIMARY KEY, name TEXT NOT NULL, email TEXT UNIQUE NOT NULL,
                password TEXT NOT NULL, department TEXT, year TEXT, bio TEXT,
                profile_picture TEXT, role TEXT DEFAULT 'student', created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                designation TEXT, qualifications TEXT,
                CONSTRAINT users_role_check CHECK (role IN ('student','faculty'))
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS skills (
                id BIGSERIAL PRIMARY KEY, user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                skill_name TEXT NOT NULL, skill_type TEXT NOT NULL,
                CONSTRAINT skills_type_check CHECK (skill_type IN ('offered','wanted'))
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS requests (
                id BIGSERIAL PRIMARY KEY, sender_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                receiver_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                skill_offered TEXT, skill_wanted TEXT, status TEXT NOT NULL DEFAULT 'pending',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                CONSTRAINT requests_status_check CHECK (status IN ('pending','accepted','rejected','completed'))
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS ratings (
                id BIGSERIAL PRIMARY KEY, giver_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                receiver_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                request_id BIGINT REFERENCES requests(id) ON DELETE SET NULL,
                stars INTEGER NOT NULL CHECK (stars BETWEEN 1 AND 5), feedback TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS teams (
                id BIGSERIAL PRIMARY KEY, name TEXT NOT NULL, description TEXT,
                team_type TEXT NOT NULL DEFAULT 'other', creator_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                max_members INTEGER DEFAULT 4, event_date TEXT, status TEXT DEFAULT 'open',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                CONSTRAINT teams_type_check CHECK (team_type IN ('hackathon','coding_competition','project','other')),
                CONSTRAINT teams_status_check CHECK (status IN ('open','closed'))
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS team_members (
                id BIGSERIAL PRIMARY KEY, team_id BIGINT NOT NULL REFERENCES teams(id) ON DELETE CASCADE,
                user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                role TEXT DEFAULT 'member', status TEXT DEFAULT 'pending', joined_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(team_id,user_id),
                CONSTRAINT team_members_role_check CHECK (role IN ('leader','member')),
                CONSTRAINT team_members_status_check CHECK (status IN ('pending','accepted','rejected'))
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS conversations (
                id BIGSERIAL PRIMARY KEY, user1_id BIGINT REFERENCES users(id) ON DELETE CASCADE,
                user2_id BIGINT REFERENCES users(id) ON DELETE CASCADE, team_id BIGINT REFERENCES teams(id) ON DELETE CASCADE,
                is_group INTEGER NOT NULL DEFAULT 0, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(user1_id,user2_id), UNIQUE(team_id)
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS messages (
                id BIGSERIAL PRIMARY KEY, conversation_id BIGINT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
                sender_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE, content TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP, deleted INTEGER DEFAULT 0, edited INTEGER DEFAULT 0,
                reactions TEXT DEFAULT '{}', reply_to BIGINT REFERENCES messages(id) ON DELETE SET NULL,
                hidden_for TEXT DEFAULT '[]'
            )
        """)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS message_reports (
                id BIGSERIAL PRIMARY KEY, message_id BIGINT NOT NULL REFERENCES messages(id) ON DELETE CASCADE,
                reporter_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE, reason TEXT, details TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        cur.close()
        conn.close()

# -----------------------------------------------------------------------
# Small data-access helpers used across multiple routes
# -----------------------------------------------------------------------
def get_user(user_id):
    """Fetch a single user row by id."""
    return get_db().execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()


def get_skills(user_id, skill_type=None):
    """Fetch a user's skills, optionally filtered by 'offered' / 'wanted'."""
    db = get_db()
    if skill_type:
        return db.execute(
            "SELECT * FROM skills WHERE user_id = ? AND skill_type = ? ORDER BY skill_name",
            (user_id, skill_type)
        ).fetchall()
    return db.execute(
        "SELECT * FROM skills WHERE user_id = ? ORDER BY skill_type, skill_name", (user_id,)
    ).fetchall()


def get_rating_stats(user_id):
    """Return (average_rating, total_ratings) for a user, rounded to 1 decimal."""
    row = get_db().execute(
        "SELECT AVG(stars) AS avg_stars, COUNT(*) AS total FROM ratings WHERE receiver_id = ?",
        (user_id,)
    ).fetchone()
    avg = round(row["avg_stars"], 1) if row["avg_stars"] else 0
    return avg, row["total"]


def get_profiles_by_role(role, exclude_user_id=None, limit=6):
    """Fetch basic profile info (+ offered skills) for users with the given role."""
    db = get_db()
    rows = db.execute(
        "SELECT * FROM users WHERE role = ? AND id != ? ORDER BY name LIMIT ?",
        (role, exclude_user_id or -1, limit)
    ).fetchall()

    profiles = []
    for u in rows:
        offered = db.execute(
            "SELECT * FROM skills WHERE user_id = ? AND skill_type = 'offered' ORDER BY skill_name",
            (u["id"],)
        ).fetchall()
        avg_rating, total_ratings = get_rating_stats(u["id"])
        profiles.append({
            "user": u,
            "offered": offered,
            "avg_rating": avg_rating,
            "total_ratings": total_ratings,
        })
    return profiles


def compute_matches(user_id):
    """
    The SkillSwap matching algorithm.

    A 'perfect match' happens when:
        - the other student offers something the current user wants, AND
        - the other student wants something the current user offers
    (i.e. both sides benefit -- a true two-way skill swap)

    A 'partial match' happens when only one of those directions is true
    (the other student can teach the current user something, but the
    current user doesn't currently offer anything they're looking for).

    Returns: (perfect_matches, partial_matches) -- both lists of dicts:
        {'user': <row>, 'they_teach_me': set(), 'i_teach_them': set(),
         'avg_rating': float, 'total_ratings': int}
    """
    db = get_db()

    my_offered = {
        r["skill_name"].strip().lower()
        for r in db.execute(
            "SELECT skill_name FROM skills WHERE user_id=? AND skill_type='offered'", (user_id,)
        )
    }
    my_wanted = {
        r["skill_name"].strip().lower()
        for r in db.execute(
            "SELECT skill_name FROM skills WHERE user_id=? AND skill_type='wanted'", (user_id,)
        )
    }

    other_ids = [
        r["user_id"] for r in db.execute(
            "SELECT DISTINCT user_id FROM skills WHERE user_id != ?", (user_id,)
        )
    ]

    perfect, partial = [], []

    for other_id in other_ids:
        other_offered_rows = db.execute(
            "SELECT skill_name FROM skills WHERE user_id=? AND skill_type='offered'", (other_id,)
        ).fetchall()
        other_wanted_rows = db.execute(
            "SELECT skill_name FROM skills WHERE user_id=? AND skill_type='wanted'", (other_id,)
        ).fetchall()

        other_offered = {r["skill_name"].strip().lower() for r in other_offered_rows}
        other_wanted = {r["skill_name"].strip().lower() for r in other_wanted_rows}

        they_teach_me, _ = fuzzy_intersect(my_wanted, other_offered)
        i_teach_them, _  = fuzzy_intersect(my_offered, other_wanted)

        if not they_teach_me:
            continue  # not relevant to me at all

        other_user = get_user(other_id)
        avg_rating, total_ratings = get_rating_stats(other_id)
        entry = {
            "user": other_user,
            "they_teach_me": sorted(they_teach_me),
            "i_teach_them": sorted(i_teach_them),
            "avg_rating": avg_rating,
            "total_ratings": total_ratings,
        }

        if they_teach_me and i_teach_them:
            perfect.append(entry)
        else:
            partial.append(entry)

    return perfect, partial


def get_connection_status(uid, other_id):
    """Return 'friends', 'pending', or 'none' for the swap-request
    relationship between two users (in either direction).
    'friends' once a request between them has been accepted/completed --
    at that point a new request should not be sendable again."""
    db = get_db()
    row = db.execute(
        """SELECT status FROM requests
           WHERE (sender_id=? AND receiver_id=?) OR (sender_id=? AND receiver_id=?)
           ORDER BY CASE status
               WHEN 'accepted' THEN 0
               WHEN 'completed' THEN 0
               WHEN 'pending' THEN 1
               ELSE 2
           END, created_at DESC
           LIMIT 1""",
        (uid, other_id, other_id, uid)
    ).fetchone()
    if not row:
        return "none"
    if row["status"] in ("accepted", "completed"):
        return "friends"
    if row["status"] == "pending":
        return "pending"
    return "none"


def annotate_connection_status(uid, entries):
    """Attach a 'connection_status' key to each match/result dict."""
    for e in entries:
        e["connection_status"] = get_connection_status(uid, e["user"]["id"])
    return entries


def allowed_file(filename):
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS


# -----------------------------------------------------------------------
# Fuzzy skill matching helpers
# -----------------------------------------------------------------------
_STRIP_WORDS = re.compile(
    r'\b(programming|language|lang|development|dev|coding|code|'
    r'basics|fundamental|fundamentals|introduction|intro|advanced|'
    r'beginner|intermediate|and|the|of|in|for|to|with|using|'
    r'basic|course|tutorial|module|subject|skills|skill)\b',
    re.IGNORECASE
)

# Groups of interchangeable skill names — anything in the same group matches
_SKILL_GROUPS = [
    {'javascript', 'js', 'ecmascript', 'es6', 'vanillajs'},
    {'typescript', 'ts'},
    {'reactjs', 'react'},
    {'vuejs', 'vue'},
    {'angularjs', 'angular'},
    {'nodejs', 'node'},
    {'nextjs', 'next'},
    {'html', 'html5'},
    {'css', 'css3'},
    {'python', 'py', 'python3', 'python2'},
    {'c', 'clanguage', 'cprogramming'},
    {'c++', 'cpp', 'cplusplus'},
    {'c#', 'csharp', 'dotnet'},
    {'java', 'javaprogramming'},
    {'machinelearning', 'ml'},
    {'deeplearning', 'dl', 'neuralnetworks'},
    {'artificialintelligence', 'artificialintel', 'aiprogramming', 'ai'},
    {'datascience', 'dataanalysis', 'dataanalytics'},
    {'datavisualization', 'dataviz'},
    {'mysql', 'mariadb'},
    {'postgresql', 'postgres'},
    {'mongodb', 'mongo'},
    {'photoshop', 'adobephotoshop'},
    {'illustrator', 'adobeillustrator'},
    {'figma', 'figmadesign'},
    {'androiddevelopment', 'android'},
    {'iosdevelopment', 'ios'},
    {'flutter', 'dart'},
    {'aws', 'amazonwebservices'},
    {'gcp', 'googlecloud'},
    {'azure', 'microsoftazure'},
    {'docker', 'containerization'},
    {'kubernetes', 'k8s'},
    {'git', 'github', 'gitlab'},
    {'php'},
    {'ruby', 'rb'},
    {'rust', 'rs'},
    {'golang', 'go'},
    {'rlanguage', 'rprogramming'},
    {'matlab'},
    {'excel', 'microsoftexcel', 'spreadsheets'},
    {'videoediting', 'premiere', 'adobepremiere'},
    {'blender', '3dmodeling'},
    {'publicspeaking', 'publicspeak'},
]

def normalize_skill(s: str) -> str:
    """Strip noise words and punctuation for fuzzy comparison."""
    s = s.strip().lower()
    s = _STRIP_WORDS.sub('', s)
    s = re.sub(r'[\s\-_/]+', '', s)
    s = re.sub(r'[^a-z0-9+#]', '', s)
    return s

# Build lookup: normalized AND plain-alphanum form → group index
_NORM_TO_GROUP: dict = {}
for _gidx, _grp in enumerate(_SKILL_GROUPS):
    for _item in _grp:
        _nk = normalize_skill(_item)
        _pk = re.sub(r'[^a-z0-9]', '', _item.lower())
        if _nk:
            _NORM_TO_GROUP.setdefault(_nk, _gidx)
        if _pk and _pk != _nk:
            _NORM_TO_GROUP.setdefault(_pk, _gidx)

def skills_similar(a: str, b: str) -> bool:
    """Return True if two skill names are similar enough to count as a match."""
    al, bl = a.strip().lower(), b.strip().lower()
    if al == bl:
        return True
    na, nb = normalize_skill(a), normalize_skill(b)
    if not na or not nb:
        return False
    if na == nb:
        return True
    # Substring: "python" ⊂ "python3", "react" ⊂ "reactjs"
    if len(na) >= 2 and len(nb) >= 2 and (na in nb or nb in na):
        return True
    # Alias group check — normalized form
    ga = _NORM_TO_GROUP.get(na)
    gb = _NORM_TO_GROUP.get(nb)
    if ga is not None and gb is not None and ga == gb:
        return True
    # Alias group check — plain alphanumeric form
    pa = re.sub(r'[^a-z0-9]', '', al)
    pb = re.sub(r'[^a-z0-9]', '', bl)
    ga2 = _NORM_TO_GROUP.get(pa)
    gb2 = _NORM_TO_GROUP.get(pb)
    if ga2 is not None and gb2 is not None and ga2 == gb2:
        return True
    return False

def fuzzy_intersect(set_a: set, set_b: set):
    """
    Return (matched_from_a, matched_from_b) — items from a and b that
    have a fuzzy match in the other set.
    """
    matched_a, matched_b = set(), set()
    for a in set_a:
        for b in set_b:
            if skills_similar(a, b):
                matched_a.add(a)
                matched_b.add(b)
    return matched_a, matched_b


# -----------------------------------------------------------------------
# Auth decorator
# -----------------------------------------------------------------------
def login_required(view_func):
    """Redirect to the login page if no user is logged in, or if the
    logged-in session refers to a user that no longer exists in the
    database (e.g. a stale cookie left over after the DB was reset)."""
    @wraps(view_func)
    def wrapped(*args, **kwargs):
        if "user_id" not in session:
            flash("Please log in to continue.", "warning")
            return redirect(url_for("login"))
        if get_user(session["user_id"]) is None:
            session.clear()
            flash("Your session has expired. Please log in again.", "warning")
            return redirect(url_for("login"))
        return view_func(*args, **kwargs)
    return wrapped


def api_login_required(view_func):
    """Same idea as login_required, but for endpoints called via
    fetch()/AJAX (chat send/poll/react/etc). These must never redirect to
    the HTML login page: if a fetch() call follows that redirect, it gets
    back an HTML page instead of JSON, response.json() throws, and the
    frontend has no clean way to show a useful error -- it just looks
    like a generic 'network error'. Always return JSON here instead."""
    @wraps(view_func)
    def wrapped(*args, **kwargs):
        if "user_id" not in session or get_user(session["user_id"]) is None:
            session.clear()
            return jsonify({"ok": False, "error": "auth_required",
                             "message": "Your session has expired. Please log in again."}), 401
        return view_func(*args, **kwargs)
    return wrapped


def json_safe(view_func):
    """Guarantee this endpoint always returns JSON, even if something
    unexpected throws. Without this, an unhandled exception produces
    Flask's default HTML error page, which breaks response.json() on the
    client and shows up to users as a confusing generic 'network error'
    even though nothing was actually wrong with the network."""
    @wraps(view_func)
    def wrapped(*args, **kwargs):
        try:
            return view_func(*args, **kwargs)
        except Exception:
            app.logger.exception("Unhandled error in %s", view_func.__name__)
            db = getattr(g, "_database", None)
            if db is not None:
                try:
                    db.conn.rollback()
                except Exception:
                    pass
            return jsonify({"ok": False, "error": "server_error",
                             "message": "Something went wrong on our end. Please try again."}), 500
    return wrapped


def _fmt_ts(value):
    """Normalize a timestamp into a consistent ISO 8601 string regardless
    of whether it came back as a Python datetime (Postgres) or a plain
    'YYYY-MM-DD HH:MM:SS' string, so the frontend can rely on
    one format either way."""
    if value is None:
        return None
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value).replace(" ", "T")


# -----------------------------------------------------------------------
# Context processor -- makes a few things available in every template
# -----------------------------------------------------------------------
MAX_SAVED_ACCOUNTS = 5


@app.context_processor
def inject_globals():
    pending_count = 0
    if "user_id" in session:
        row = get_db().execute(
            "SELECT COUNT(*) AS c FROM requests WHERE receiver_id=? AND status='pending'",
            (session["user_id"],)
        ).fetchone()
        pending_count = row["c"]

    # Other accounts signed in on this browser (for the account switcher),
    # excluding whichever one is currently active.
    saved_accounts = []
    stored = session.get("accounts") or {}
    for uid_str, info in stored.items():
        if str(session.get("user_id")) == uid_str:
            continue
        saved_accounts.append({
            "id": int(uid_str),
            "name": info.get("name", "Student"),
            "email": info.get("email", ""),
        })
    saved_accounts.sort(key=lambda a: a["name"].lower())

    return {
        "logged_in": "user_id" in session,
        "session_user_name": session.get("user_name"),
        "pending_count": pending_count,
        "DEPARTMENTS": DEPARTMENTS,
        "YEARS": YEARS,
        "saved_accounts": saved_accounts,
        "can_add_account": len(stored) < MAX_SAVED_ACCOUNTS,
    }


@app.template_filter("initials")
def initials_filter(name):
    """Turn 'Tejas Patel' into 'TP' for avatar placeholders."""
    if not name:
        return "?"
    parts = name.strip().split()
    if len(parts) == 1:
        return parts[0][:2].upper()
    return (parts[0][0] + parts[-1][0]).upper()


@app.template_filter("avatar_color")
def avatar_color_filter(user_id):
    """Pick a deterministic color from a small palette based on user id,
    so each student gets a consistent avatar background."""
    palette = ["#3b82f6", "#10b981", "#f97316", "#8b5cf6", "#ec4899", "#06b6d4"]
    try:
        return palette[int(user_id) % len(palette)]
    except (ValueError, TypeError):
        return palette[0]


# -----------------------------------------------------------------------
# Routes -- Public pages
# -----------------------------------------------------------------------
@app.route("/")
def index():
    if "user_id" in session:
        return redirect(url_for("dashboard"))
    return render_template("index.html")


@app.route("/register", methods=["GET", "POST"])
def register():
    if "user_id" in session:
        return redirect(url_for("dashboard"))

    if request.method == "POST":
        name = request.form.get("name", "").strip()
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        confirm_password = request.form.get("confirm_password", "")
        account_type = request.form.get("account_type", "student").strip().lower()
        if account_type not in ("student", "faculty"):
            account_type = "student"

        department = request.form.get("department", "").strip()
        year = request.form.get("year", "").strip()
        designation = request.form.get("designation", "").strip()
        qualifications = request.form.get("qualifications", "").strip()

        errors = []
        if not name or not email or not password:
            errors.append("Please fill in all required fields.")
        if password and len(password) < 6:
            errors.append("Password must be at least 6 characters long.")
        if password != confirm_password:
            errors.append("Passwords do not match.")
        if account_type == "faculty" and not designation:
            errors.append("Please enter your designation.")

        if not errors:
            existing = get_db().execute(
                "SELECT id FROM users WHERE email = ?", (email,)
            ).fetchone()
            if existing:
                errors.append("An account with this email already exists.")

        if errors:
            for e in errors:
                flash(e, "danger")
            return render_template("register.html", form_data=request.form)

        # Hash the password before storing it -- never store plain text passwords
        hashed_password = generate_password_hash(password)
        db = get_db()
        if account_type == "faculty":
            db.execute(
                """INSERT INTO users (name, email, password, role, designation, qualifications)
                   VALUES (?, ?, ?, 'faculty', ?, ?)""",
                (name, email, hashed_password, designation, qualifications)
            )
        else:
            db.execute(
                """INSERT INTO users (name, email, password, role, department, year)
                   VALUES (?, ?, ?, 'student', ?, ?)""",
                (name, email, hashed_password, department, year)
            )
        db.commit()

        flash("Account created successfully! Please log in.", "success")
        return redirect(url_for("login"))

    return render_template("register.html", form_data={})


def _remember_account(user):
    """Add/update this user in the browser's list of signed-in accounts
    (used by the account switcher) and make it the active session."""
    accounts = session.get("accounts") or {}
    accounts[str(user["id"])] = {"name": user["name"], "email": user["email"]}
    session["accounts"] = accounts
    session["user_id"] = user["id"]
    session["user_name"] = user["name"]
    session["login_role"] = user["role"]


@app.route("/login", methods=["GET", "POST"])
def login():
    # "Add another account" lets an already-logged-in user sign in with a
    # second account without losing the first one -- so we only bounce
    # already-logged-in visitors away when they're NOT trying to add one.
    adding_account = request.args.get("add") == "1"
    if "user_id" in session and not adding_account:
        return redirect(url_for("dashboard"))

    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        login_role = request.form.get("login_role", "student").strip().lower()
        if login_role not in ("student", "faculty"):
            login_role = "student"
        adding_account = request.form.get("add_account") == "1"

        existing_accounts = session.get("accounts") or {}
        user = get_db().execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone()

        if user and check_password_hash(user["password"], password):
            if (adding_account and str(user["id"]) not in existing_accounts
                    and len(existing_accounts) >= MAX_SAVED_ACCOUNTS):
                flash(f"You can only stay signed in to {MAX_SAVED_ACCOUNTS} accounts at once on this browser. "
                      f"Remove one from the account switcher first.", "warning")
                return redirect(url_for("profile"))

            db = get_db()
            db.execute("UPDATE users SET role = ? WHERE id = ?", (login_role, user["id"]))
            db.commit()
            user = get_user(user["id"])

            _remember_account(user)
            flash(f"Welcome back, {user['name']}! ({login_role.capitalize()})", "success")
            next_page = request.args.get("next")
            return redirect(next_page or url_for("dashboard"))

        flash("Invalid email or password.", "danger")
        return render_template("login.html", login_role=login_role, adding_account=adding_account)

    return render_template("login.html", adding_account=adding_account)


@app.route("/accounts/switch/<int:user_id>")
def switch_account(user_id):
    """Switch the active session to another account already signed in on
    this browser, with no password needed -- like a Google account switch."""
    accounts = session.get("accounts") or {}
    if str(user_id) not in accounts:
        flash("That account isn't signed in on this browser. Please log in.", "warning")
        return redirect(url_for("login"))

    user = get_user(user_id)
    if not user:
        accounts.pop(str(user_id), None)
        session["accounts"] = accounts
        flash("That account no longer exists and was removed.", "danger")
        return redirect(url_for("login"))

    session["user_id"] = user["id"]
    session["user_name"] = user["name"]
    session["login_role"] = user["role"]
    flash(f"Switched to {user['name']}.", "success")
    return redirect(url_for("dashboard"))


@app.route("/accounts/remove/<int:user_id>", methods=["POST"])
def remove_account(user_id):
    """Forget a saved account on this browser (does not delete the
    account itself -- just signs it out of the switcher)."""
    accounts = session.get("accounts") or {}
    was_active = session.get("user_id") == user_id
    accounts.pop(str(user_id), None)
    session["accounts"] = accounts

    if was_active:
        if accounts:
            next_id = int(next(iter(accounts)))
            user = get_user(next_id)
            if user:
                session["user_id"] = user["id"]
                session["user_name"] = user["name"]
                session["login_role"] = user["role"]
                flash(f"Removed that account. Switched to {user['name']}.", "info")
                return redirect(url_for("profile"))
        session.clear()
        flash("Account removed and you've been signed out.", "info")
        return redirect(url_for("login"))

    flash("Account removed from this browser.", "info")
    return redirect(request.referrer or url_for("profile"))


@app.route("/logout")
def logout():
    """Signs the current account out. If other accounts are still saved
    on this browser, switches to one of them instead of a full logout."""
    accounts = session.get("accounts") or {}
    uid = session.get("user_id")
    if uid is not None:
        accounts.pop(str(uid), None)
    session["accounts"] = accounts

    if accounts:
        next_id = int(next(iter(accounts)))
        user = get_user(next_id)
        if user:
            session["user_id"] = user["id"]
            session["user_name"] = user["name"]
            session["login_role"] = user["role"]
            flash(f"You have been logged out. Switched to {user['name']}.", "info")
            return redirect(url_for("dashboard"))

    session.clear()
    flash("You have been logged out.", "info")
    return redirect(url_for("index"))


@app.route("/logout-all")
def logout_all():
    """Fully signs out of every account saved on this browser."""
    session.clear()
    flash("Signed out of all accounts.", "info")
    return redirect(url_for("index"))


# -----------------------------------------------------------------------
# Routes -- Dashboard
# -----------------------------------------------------------------------
@app.route("/dashboard")
@login_required
def dashboard():
    uid = session["user_id"]
    user = get_user(uid)

    skills_offered = get_skills(uid, "offered")
    skills_wanted  = get_skills(uid, "wanted")

    perfect_matches, partial_matches = compute_matches(uid)
    annotate_connection_status(uid, perfect_matches)
    annotate_connection_status(uid, partial_matches)

    pending_received = get_db().execute(
        "SELECT COUNT(*) AS c FROM requests WHERE receiver_id=? AND status='pending'", (uid,)
    ).fetchone()["c"]

    avg_rating, total_ratings = get_rating_stats(uid)

    # Role-specific directory: students see faculty mentors, faculty see students
    user_role = user["role"] or "student"
    if user_role == "faculty":
        directory_profiles = get_profiles_by_role("student", exclude_user_id=uid)
    else:
        directory_profiles = get_profiles_by_role("faculty", exclude_user_id=uid)

    return render_template(
        "dashboard.html",
        user=user,
        user_role=user_role,
        skills_offered=skills_offered,
        skills_wanted=skills_wanted,
        my_offered=skills_offered,
        perfect_matches=perfect_matches[:3],
        partial_matches=partial_matches[:3],
        total_matches=len(perfect_matches) + len(partial_matches),
        pending_received=pending_received,
        avg_rating=avg_rating,
        total_ratings=total_ratings,
        directory_profiles=directory_profiles,
    )


# -----------------------------------------------------------------------
# Routes -- Profile
# -----------------------------------------------------------------------
@app.route("/profile", methods=["GET", "POST"])
@login_required
def profile():
    uid = session["user_id"]
    db = get_db()

    if request.method == "POST":
        current = get_user(uid)
        name = request.form.get("name", "").strip()
        bio = request.form.get("bio", "").strip()

        # Department/Year fields are only shown in the UI for student accounts.
        # Faculty accounts don't submit them, so keep whatever was already stored
        # rather than overwriting with a blank value.
        if "department" in request.form:
            department = request.form.get("department", "").strip()
        else:
            department = current["department"] if current else None

        if "year" in request.form:
            year = request.form.get("year", "").strip()
        else:
            year = current["year"] if current else None

        # Designation/qualifications fields are only shown in the UI for
        # faculty accounts. Students don't submit them, so keep whatever
        # was already stored rather than overwriting with a blank value.
        if "designation" in request.form:
            designation = request.form.get("designation", "").strip()
        else:
            designation = current["designation"] if current else None

        if "qualifications" in request.form:
            qualifications = request.form.get("qualifications", "").strip()
        else:
            qualifications = current["qualifications"] if current else None

        if not name:
            flash("Name cannot be empty.", "danger")
            return redirect(url_for("profile"))

        # Profile pictures are no longer supported — avatars are always
        # generated from the user's initials.
        db.execute(
            """UPDATE users SET name=?, department=?, year=?, designation=?, qualifications=?,
               bio=? WHERE id=?""",
            (name, department, year, designation, qualifications, bio, uid)
        )
        db.commit()
        session["user_name"] = name
        flash("Profile updated successfully!", "success")
        return redirect(url_for("profile"))

    user = get_user(uid)
    skills_offered = get_skills(uid, "offered")
    skills_wanted = get_skills(uid, "wanted")
    avg_rating, total_ratings = get_rating_stats(uid)
    recent_feedback = db.execute(
        """SELECT ratings.*, users.name AS giver_name FROM ratings
           JOIN users ON ratings.giver_id = users.id
           WHERE ratings.receiver_id = ? ORDER BY ratings.created_at DESC LIMIT 5""",
        (uid,)
    ).fetchall()

    return render_template(
        "profile.html",
        user=user,
        skills_offered=skills_offered,
        skills_wanted=skills_wanted,
        avg_rating=avg_rating,
        total_ratings=total_ratings,
        recent_feedback=recent_feedback,
    )


@app.route("/profile/delete", methods=["POST"])
@login_required
def delete_profile():
    uid = session["user_id"]
    db = get_db()
    user = get_user(uid)

    password = request.form.get("password", "")
    if not user or not check_password_hash(user["password"], password):
        flash("Incorrect password. Your profile was not deleted.", "danger")
        return redirect(url_for("profile"))

    # Remove the uploaded profile picture from disk, if any.
    if user["profile_picture"]:
        pic_path = os.path.join(app.config["UPLOAD_FOLDER"], user["profile_picture"])
        if os.path.exists(pic_path):
            try:
                os.remove(pic_path)
            except OSError:
                pass

    # Deleting the user row cascades (ON DELETE CASCADE) to their skills,
    # sent/received requests, ratings, team memberships, conversations,
    # and messages, since foreign keys are enabled on this connection.
    db.execute("DELETE FROM users WHERE id=?", (uid,))
    db.commit()

    session.clear()
    flash("Your profile has been permanently deleted.", "info")
    return redirect(url_for("index"))


# -----------------------------------------------------------------------
# Routes -- Skills management (add / delete)
# -----------------------------------------------------------------------
@app.route("/skills/add", methods=["POST"])
@login_required
def add_skill():
    skill_name = request.form.get("skill_name", "").strip()
    skill_type = request.form.get("skill_type", "")

    if not skill_name or skill_type not in ("offered", "wanted"):
        flash("Please provide a valid skill name.", "danger")
    else:
        db = get_db()
        # avoid exact duplicate entries for the same user
        dup = db.execute(
            "SELECT id FROM skills WHERE user_id=? AND skill_type=? AND LOWER(skill_name)=LOWER(?)",
            (session["user_id"], skill_type, skill_name)
        ).fetchone()
        if dup:
            flash("You've already added that skill.", "warning")
        else:
            db.execute(
                "INSERT INTO skills (user_id, skill_name, skill_type) VALUES (?, ?, ?)",
                (session["user_id"], skill_name, skill_type)
            )
            db.commit()
            flash("Skill added!", "success")

    return redirect(request.referrer or url_for("dashboard"))


@app.route("/skills/delete/<int:skill_id>", methods=["POST"])
@login_required
def delete_skill(skill_id):
    db = get_db()
    db.execute(
        "DELETE FROM skills WHERE id=? AND user_id=?", (skill_id, session["user_id"])
    )
    db.commit()
    flash("Skill removed.", "info")
    return redirect(request.referrer or url_for("dashboard"))


# -----------------------------------------------------------------------
# Routes -- Search
# -----------------------------------------------------------------------
@app.route("/search")
@login_required
def search():
    skill_q = request.args.get("skill", "").strip()
    dept_q = request.args.get("department", "").strip()
    year_q = request.args.get("year", "").strip()
    uid = session["user_id"]

    db = get_db()
    query = """
        SELECT DISTINCT u.* FROM users u
        LEFT JOIN skills s ON u.id = s.user_id
        WHERE u.id != ?
    """
    params = [uid]

    if skill_q:
        query += " AND s.skill_name LIKE ?"
        params.append(f"%{skill_q}%")
    if dept_q:
        query += " AND u.department = ?"
        params.append(dept_q)
    if year_q:
        query += " AND u.year = ?"
        params.append(year_q)

    query += " ORDER BY u.name"
    results_raw = db.execute(query, params).fetchall()

    # If a skill query was given, also find users who match fuzzily (e.g. "C language" ≈ "C programming")
    if skill_q:
        # get all other users not already in results
        existing_ids = {u["id"] for u in results_raw}
        extra_users = db.execute(
            "SELECT DISTINCT u.* FROM users u WHERE u.id != ?", (uid,)
        ).fetchall()
        for u in extra_users:
            if u["id"] in existing_ids:
                continue
            # check if any of their skills fuzzy-match the query
            user_skills = db.execute(
                "SELECT skill_name FROM skills WHERE user_id=?", (u["id"],)
            ).fetchall()
            if any(skills_similar(skill_q, s["skill_name"]) for s in user_skills):
                results_raw = list(results_raw) + [u]

    # attach each matching user's offered/wanted skills + rating for display
    results = []
    seen_ids = set()
    for u in results_raw:
        if u["id"] in seen_ids:
            continue
        seen_ids.add(u["id"])
        avg_rating, total_ratings = get_rating_stats(u["id"])
        results.append({
            "user": u,
            "offered": get_skills(u["id"], "offered"),
            "wanted": get_skills(u["id"], "wanted"),
            "avg_rating": avg_rating,
            "total_ratings": total_ratings,
        })

    searched = bool(skill_q or dept_q or year_q)
    annotate_connection_status(uid, results)

    return render_template(
        "search.html",
        results=results,
        skill_q=skill_q,
        dept_q=dept_q,
        year_q=year_q,
        searched=searched,
        my_offered=get_skills(uid, "offered"),
    )


# -----------------------------------------------------------------------
# Routes -- Matches
# -----------------------------------------------------------------------
@app.route("/matches")
@login_required
def matches():
    uid = session["user_id"]
    perfect_matches, partial_matches = compute_matches(uid)
    annotate_connection_status(uid, perfect_matches)
    annotate_connection_status(uid, partial_matches)
    return render_template(
        "matches.html",
        perfect_matches=perfect_matches,
        partial_matches=partial_matches,
        my_offered=get_skills(uid, "offered"),
    )


# -----------------------------------------------------------------------
# Routes -- Requests (send / accept / reject / complete)
# -----------------------------------------------------------------------
@app.route("/requests")
@login_required
def requests_page():
    uid = session["user_id"]
    db = get_db()

    received = db.execute(
        """SELECT r.*, u.name AS other_name, u.id AS other_id,
                  u.profile_picture AS other_picture
           FROM requests r JOIN users u ON r.sender_id = u.id
           WHERE r.receiver_id = ? ORDER BY r.created_at DESC""",
        (uid,)
    ).fetchall()

    sent = db.execute(
        """SELECT r.*, u.name AS other_name, u.id AS other_id,
                  u.profile_picture AS other_picture
           FROM requests r JOIN users u ON r.receiver_id = u.id
           WHERE r.sender_id = ? ORDER BY r.created_at DESC""",
        (uid,)
    ).fetchall()

    # figure out which completed requests this user has already rated
    rated_ids = {
        row["request_id"] for row in db.execute(
            "SELECT request_id FROM ratings WHERE giver_id=?", (uid,)
        ).fetchall()
    }

    return render_template(
        "requests.html",
        received=received,
        sent=sent,
        rated_ids=rated_ids,
    )


@app.route("/requests/send", methods=["POST"])
@login_required
def send_request():
    uid = session["user_id"]
    receiver_id = request.form.get("receiver_id", type=int)
    skill_offered = request.form.get("skill_offered", "").strip()
    skill_wanted = request.form.get("skill_wanted", "").strip()

    if not receiver_id or receiver_id == uid:
        flash("Invalid swap request.", "danger")
        return redirect(request.referrer or url_for("dashboard"))

    db = get_db()
    status = get_connection_status(uid, receiver_id)
    if status == "pending":
        flash("You already have a pending request with this student.", "warning")
    elif status == "friends":
        flash("You're already connected with this student.", "info")
    else:
        db.execute(
            """INSERT INTO requests (sender_id, receiver_id, skill_offered, skill_wanted, status)
               VALUES (?, ?, ?, ?, 'pending')""",
            (uid, receiver_id, skill_offered, skill_wanted)
        )
        db.commit()
        flash("Swap request sent!", "success")

    return redirect(request.referrer or url_for("dashboard"))


@app.route("/requests/respond/<int:req_id>", methods=["POST"])
@login_required
def respond_request(req_id):
    action = request.form.get("action")
    status = "accepted" if action == "accept" else "rejected"

    db = get_db()
    db.execute(
        "UPDATE requests SET status=? WHERE id=? AND receiver_id=?",
        (status, req_id, session["user_id"])
    )
    db.commit()
    flash(f"Request {status}.", "success" if status == "accepted" else "info")
    return redirect(url_for("requests_page"))


@app.route("/requests/complete/<int:req_id>", methods=["POST"])
@login_required
def complete_request(req_id):
    uid = session["user_id"]
    db = get_db()
    db.execute(
        """UPDATE requests SET status='completed'
           WHERE id=? AND (sender_id=? OR receiver_id=?) AND status='accepted'""",
        (req_id, uid, uid)
    )
    db.commit()
    flash("Swap marked as completed! You can now rate your partner.", "success")
    return redirect(url_for("requests_page"))


# -----------------------------------------------------------------------
# Routes -- Ratings
# -----------------------------------------------------------------------
@app.route("/ratings/add", methods=["POST"])
@login_required
def add_rating():
    uid = session["user_id"]
    receiver_id = request.form.get("receiver_id", type=int)
    request_id = request.form.get("request_id", type=int)
    stars = request.form.get("stars", type=int)
    feedback = request.form.get("feedback", "").strip()

    if not receiver_id or not stars or stars < 1 or stars > 5:
        flash("Please provide a valid star rating.", "danger")
        return redirect(url_for("requests_page"))

    db = get_db()
    already = db.execute(
        "SELECT id FROM ratings WHERE giver_id=? AND request_id=?", (uid, request_id)
    ).fetchone()

    if already:
        flash("You've already rated this swap.", "warning")
    else:
        db.execute(
            """INSERT INTO ratings (giver_id, receiver_id, request_id, stars, feedback)
               VALUES (?, ?, ?, ?, ?)""",
            (uid, receiver_id, request_id, stars, feedback)
        )
        db.commit()
        flash("Thanks for your feedback!", "success")

    return redirect(url_for("requests_page"))


# -----------------------------------------------------------------------
# Routes -- Chat
# -----------------------------------------------------------------------
def get_or_create_conversation(uid, other_id):
    """Return (conversation_id, created) — creates one if it doesn't exist.
    Always stores user1_id < user2_id so the UNIQUE constraint works."""
    db = get_db()
    u1, u2 = min(uid, other_id), max(uid, other_id)
    row = db.execute(
        "SELECT id FROM conversations WHERE user1_id=? AND user2_id=? AND is_group=0", (u1, u2)
    ).fetchone()
    if row:
        return row["id"], False
    cur = db.execute(
        "INSERT INTO conversations (user1_id, user2_id) VALUES (?, ?)", (u1, u2)
    )
    db.commit()
    return cur.lastrowid, True


def get_or_create_team_conversation(team_id):
    """Return (conversation_id, created) for a team's group chat."""
    db = get_db()
    row = db.execute(
        "SELECT id FROM conversations WHERE team_id=? AND is_group=1", (team_id,)
    ).fetchone()
    if row:
        return row["id"], False
    cur = db.execute(
        "INSERT INTO conversations (team_id, is_group) VALUES (?, 1)", (team_id,)
    )
    db.commit()
    return cur.lastrowid, True


def get_conversation_list(uid):
    """Sidebar list mixing 1-on-1 chats and team group chats the user
    belongs to, newest activity first."""
    db = get_db()

    dms = db.execute(
        """SELECT c.id, c.created_at,
                  CASE WHEN c.user1_id=? THEN c.user2_id ELSE c.user1_id END AS other_id,
                  u.name AS other_name, u.profile_picture AS other_pic, u.role AS other_role,
                  (SELECT content FROM messages m WHERE m.conversation_id=c.id
                   ORDER BY m.id DESC LIMIT 1) AS last_msg,
                  (SELECT created_at FROM messages m WHERE m.conversation_id=c.id
                   ORDER BY m.id DESC LIMIT 1) AS last_at
           FROM conversations c
           JOIN users u ON u.id = CASE WHEN c.user1_id=? THEN c.user2_id ELSE c.user1_id END
           WHERE c.is_group=0 AND (c.user1_id=? OR c.user2_id=?)""",
        (uid, uid, uid, uid)
    ).fetchall()

    groups = db.execute(
        """SELECT c.id, c.created_at, t.id AS team_id, t.name AS team_name, t.status AS team_status,
                  (SELECT COUNT(*) FROM team_members tm
                   WHERE tm.team_id=t.id AND tm.status='accepted') AS member_count,
                  (SELECT content FROM messages m WHERE m.conversation_id=c.id
                   ORDER BY m.id DESC LIMIT 1) AS last_msg,
                  (SELECT created_at FROM messages m WHERE m.conversation_id=c.id
                   ORDER BY m.id DESC LIMIT 1) AS last_at
           FROM conversations c
           JOIN teams t ON t.id = c.team_id
           JOIN team_members tm ON tm.team_id = t.id AND tm.user_id=? AND tm.status='accepted'
           WHERE c.is_group=1""",
        (uid,)
    ).fetchall()

    items = []
    for c in dms:
        items.append({
            "id": c["id"], "kind": "dm", "other_id": c["other_id"],
            "name": c["other_name"], "pic": c["other_pic"], "role": c["other_role"],
            "last_msg": c["last_msg"], "last_at": c["last_at"] or c["created_at"],
        })
    for c in groups:
        items.append({
            "id": c["id"], "kind": "group", "team_id": c["team_id"],
            "name": c["team_name"], "team_status": c["team_status"],
            "member_count": c["member_count"],
            "last_msg": c["last_msg"], "last_at": c["last_at"] or c["created_at"],
        })
    items.sort(key=lambda x: x["last_at"] or "", reverse=True)
    return items


REACTION_EMOJIS = ["👍", "❤️", "😂", "😮", "😢", "👏", "🔥", "🤔"]

REPORT_REASONS = {
    "spam": "Spam or scam",
    "harassment": "Harassment or bullying",
    "inappropriate": "Inappropriate content",
    "impersonation": "Impersonation",
    "other": "Other",
}


def _row_to_msg_dict(m, uid):
    """Turn a messages-table row (with sender_name and optional reply_*
    columns) into a JSON-friendly dict, parsing the JSON columns."""
    d = dict(m)
    d["reactions"] = json.loads(d.get("reactions") or "{}")
    try:
        hidden_for = json.loads(d.get("hidden_for") or "[]")
    except Exception:
        hidden_for = []
    d["hidden_for"] = hidden_for
    d["hidden_for_me"] = uid in hidden_for
    d["created_at"] = _fmt_ts(d.get("created_at"))
    return d


def _fetch_conversation_messages(conv_id, uid, after_id=0):
    """Fetch messages for a conversation (optionally only those newer than
    `after_id`), attaching sender info and a lightweight preview of the
    message being replied to, if any. Messages the current user has
    'deleted for me' are dropped from the list entirely."""
    db = get_db()
    rows = db.execute(
        """SELECT m.id, m.sender_id, m.content, m.created_at, m.reply_to,
                  m.deleted, m.edited, m.reactions, m.hidden_for,
                  u.name AS sender_name, u.profile_picture AS sender_pic, u.role AS sender_role,
                  ru.name AS reply_sender_name,
                  rm.content AS reply_content, rm.deleted AS reply_deleted
           FROM messages m
           JOIN users u ON u.id = m.sender_id
           LEFT JOIN messages rm ON rm.id = m.reply_to
           LEFT JOIN users ru ON ru.id = rm.sender_id
           WHERE m.conversation_id=? AND m.id>?
           ORDER BY m.id ASC""",
        (conv_id, after_id)
    ).fetchall()

    out = []
    for m in rows:
        d = _row_to_msg_dict(m, uid)
        if d["hidden_for_me"]:
            continue
        if d["reply_to"]:
            was_deleted = d.pop("reply_deleted")
            original_content = d.pop("reply_content")
            d["reply_preview"] = {
                "sender_name": d.pop("reply_sender_name"),
                "content": "[Message deleted]" if was_deleted else original_content,
            }
        else:
            d.pop("reply_sender_name", None)
            d.pop("reply_content", None)
            d.pop("reply_deleted", None)
        out.append(d)
    return out


@app.route("/chat")
@login_required
def chat():
    uid = session["user_id"]
    convs = get_conversation_list(uid)
    return render_template("chat.html", convs=convs, active_conv=None, messages=[],
                           other=None, group_team=None, member_count=0,
                           REACTION_EMOJIS=REACTION_EMOJIS, REPORT_REASONS=REPORT_REASONS)


@app.route("/chat/<int:other_id>")
@login_required
def chat_with(other_id):
    uid = session["user_id"]
    if other_id == uid:
        return redirect(url_for("chat"))

    other = get_db().execute("SELECT * FROM users WHERE id=?", (other_id,)).fetchone()
    if not other:
        flash("User not found.", "danger")
        return redirect(url_for("chat"))

    conv_id, _ = get_or_create_conversation(uid, other_id)
    msgs = _fetch_conversation_messages(conv_id, uid)
    convs = get_conversation_list(uid)

    return render_template("chat.html", convs=convs, active_conv=conv_id,
                           messages=msgs, other=other, group_team=None, member_count=0,
                           REACTION_EMOJIS=REACTION_EMOJIS, REPORT_REASONS=REPORT_REASONS)


@app.route("/teams/<int:team_id>/chat")
@login_required
def team_chat(team_id):
    uid = session["user_id"]
    db  = get_db()

    team = db.execute("SELECT * FROM teams WHERE id=?", (team_id,)).fetchone()
    if not team:
        flash("Team not found.", "danger")
        return redirect(url_for("teams"))

    membership = db.execute(
        "SELECT * FROM team_members WHERE team_id=? AND user_id=? AND status='accepted'",
        (team_id, uid)
    ).fetchone()
    if not membership:
        flash("You need to be an accepted member of this team to use its group chat.", "warning")
        return redirect(url_for("team_detail", team_id=team_id))

    if team["status"] != "open":
        flash("Group chat is only available for open teams.", "warning")
        return redirect(url_for("team_detail", team_id=team_id))

    conv_id, _ = get_or_create_team_conversation(team_id)
    msgs = _fetch_conversation_messages(conv_id, uid)

    convs = get_conversation_list(uid)
    member_count = get_team_member_count(team_id)

    return render_template("chat.html", convs=convs, active_conv=conv_id, messages=msgs,
                           other=None, group_team=team, member_count=member_count,
                           REACTION_EMOJIS=REACTION_EMOJIS, REPORT_REASONS=REPORT_REASONS)


def _user_can_access_conv(db, conv, uid):
    if conv["is_group"]:
        member = db.execute(
            "SELECT * FROM team_members WHERE team_id=? AND user_id=? AND status='accepted'",
            (conv["team_id"], uid)
        ).fetchone()
        return member is not None
    return conv["user1_id"] == uid or conv["user2_id"] == uid


@app.route("/chat/<int:conv_id>/send", methods=["POST"])
@api_login_required
@json_safe
def send_message(conv_id):
    uid     = session["user_id"]
    content = request.form.get("content", "").strip()
    reply_to = request.form.get("reply_to", type=int)

    if not content:
        return jsonify({"ok": False, "error": "Empty message"}), 400

    if not is_clean(content):
        return jsonify({"ok": False, "error": "Message contains inappropriate content and was not sent."}), 400

    db = get_db()
    # Make sure this user is allowed to post in this conversation
    conv = db.execute("SELECT * FROM conversations WHERE id=?", (conv_id,)).fetchone()
    if not conv:
        return jsonify({"ok": False, "error": "Forbidden"}), 403

    if conv["is_group"]:
        member = db.execute(
            "SELECT * FROM team_members WHERE team_id=? AND user_id=? AND status='accepted'",
            (conv["team_id"], uid)
        ).fetchone()
        team = db.execute("SELECT status FROM teams WHERE id=?", (conv["team_id"],)).fetchone()
        if not member or not team or team["status"] != "open":
            return jsonify({"ok": False, "error": "Group chat is only available for open teams."}), 403
    else:
        if conv["user1_id"] != uid and conv["user2_id"] != uid:
            return jsonify({"ok": False, "error": "Forbidden"}), 403

    # A reply must point at a real, non-deleted message in this same conversation
    reply_row = None
    if reply_to:
        reply_row = db.execute(
            "SELECT * FROM messages WHERE id=? AND conversation_id=?", (reply_to, conv_id)
        ).fetchone()
        if not reply_row:
            reply_to = None

    cur = db.execute(
        "INSERT INTO messages (conversation_id, sender_id, content, reply_to) VALUES (?, ?, ?, ?)",
        (conv_id, uid, content, reply_to)
    )
    db.commit()
    msg_id = cur.lastrowid

    msg = db.execute(
        """SELECT m.*, u.name AS sender_name, u.role AS sender_role FROM messages m
           JOIN users u ON u.id = m.sender_id WHERE m.id=?""",
        (msg_id,)
    ).fetchone()

    resp = {
        "ok": True,
        "id": msg["id"],
        "sender_id": msg["sender_id"],
        "sender_name": msg["sender_name"],
        "sender_role": msg["sender_role"],
        "content": msg["content"],
        "created_at": _fmt_ts(msg["created_at"]),
        "reply_to": reply_to,
        "reactions": {},
    }
    if reply_row:
        replier = get_user(reply_row["sender_id"])
        resp["reply_preview"] = {
            "sender_name": replier["name"] if replier else "Unknown",
            "content": "[Message deleted]" if reply_row["deleted"] else reply_row["content"],
        }
    return jsonify(resp)


@app.route("/chat/<int:conv_id>/poll")
@api_login_required
@json_safe
def poll_messages(conv_id):
    """Return messages newer than `after` (message id) for AJAX polling.
    Also re-includes the current state of recent messages, so reactions,
    edits, unsends, and reports made by the other participant show up
    live in the open chat window without a manual refresh."""
    uid      = session["user_id"]
    after_id = request.args.get("after", 0, type=int)
    db       = get_db()

    conv = db.execute("SELECT * FROM conversations WHERE id=?", (conv_id,)).fetchone()
    if not conv or not _user_can_access_conv(db, conv, uid):
        return jsonify([]), 403

    new_msgs = _fetch_conversation_messages(conv_id, uid, after_id)

    recent_after = max(after_id - 40, 0)
    recent_msgs = _fetch_conversation_messages(conv_id, uid, recent_after) if recent_after < after_id else []

    seen = {m["id"] for m in new_msgs}
    combined = new_msgs + [m for m in recent_msgs if m["id"] not in seen]
    combined.sort(key=lambda m: m["id"])
    return jsonify(combined)


@app.route("/chat/message/<int:msg_id>/delete", methods=["POST"])
@api_login_required
@json_safe
def delete_message(msg_id):
    """'Unsend' -- removes the message content for everyone in the chat.
    Only the original sender can do this."""
    uid = session["user_id"]
    db  = get_db()
    msg = db.execute("SELECT * FROM messages WHERE id=?", (msg_id,)).fetchone()
    if not msg or msg["sender_id"] != uid:
        return jsonify({"ok": False, "error": "Not allowed"}), 403
    db.execute(
        "UPDATE messages SET deleted=1, content='[Message deleted]' WHERE id=?", (msg_id,)
    )
    db.commit()
    return jsonify({"ok": True})


@app.route("/chat/message/<int:msg_id>/delete-for-me", methods=["POST"])
@api_login_required
@json_safe
def delete_message_for_me(msg_id):
    """Hides a message only from the current user's own view -- the
    message is untouched for everyone else in the conversation."""
    uid = session["user_id"]
    db  = get_db()
    msg = db.execute("SELECT * FROM messages WHERE id=?", (msg_id,)).fetchone()
    if not msg:
        return jsonify({"ok": False, "error": "Not found"}), 404

    conv = db.execute("SELECT * FROM conversations WHERE id=?", (msg["conversation_id"],)).fetchone()
    if not conv or not _user_can_access_conv(db, conv, uid):
        return jsonify({"ok": False, "error": "Not allowed"}), 403

    try:
        hidden_for = json.loads(msg["hidden_for"] or "[]")
    except Exception:
        hidden_for = []
    if uid not in hidden_for:
        hidden_for.append(uid)
    db.execute("UPDATE messages SET hidden_for=? WHERE id=?", (json.dumps(hidden_for), msg_id))
    db.commit()
    return jsonify({"ok": True})


@app.route("/chat/message/<int:msg_id>/edit", methods=["POST"])
@api_login_required
@json_safe
def edit_message(msg_id):
    uid     = session["user_id"]
    content = request.form.get("content", "").strip()
    if not content:
        return jsonify({"ok": False, "error": "Empty message"}), 400
    if not is_clean(content):
        return jsonify({"ok": False, "error": "Inappropriate content"}), 400
    db  = get_db()
    msg = db.execute("SELECT * FROM messages WHERE id=?", (msg_id,)).fetchone()
    if not msg or msg["sender_id"] != uid or msg["deleted"]:
        return jsonify({"ok": False, "error": "Not allowed"}), 403
    db.execute(
        "UPDATE messages SET content=?, edited=1 WHERE id=?", (content, msg_id)
    )
    db.commit()
    return jsonify({"ok": True, "content": content})


@app.route("/chat/message/<int:msg_id>/react", methods=["POST"])
@api_login_required
@json_safe
def react_message(msg_id):
    uid   = session["user_id"]
    emoji = request.form.get("emoji", "").strip()
    if emoji not in REACTION_EMOJIS:
        return jsonify({"ok": False, "error": "Invalid emoji"}), 400
    db  = get_db()
    msg = db.execute("SELECT * FROM messages WHERE id=?", (msg_id,)).fetchone()
    if not msg:
        return jsonify({"ok": False, "error": "Not found"}), 404

    conv = db.execute("SELECT * FROM conversations WHERE id=?", (msg["conversation_id"],)).fetchone()
    if not conv or not _user_can_access_conv(db, conv, uid):
        return jsonify({"ok": False, "error": "Not allowed"}), 403

    reactions = json.loads(msg["reactions"] or "{}")
    if emoji not in reactions:
        reactions[emoji] = []
    if uid in reactions[emoji]:
        reactions[emoji].remove(uid)   # toggle off
    else:
        reactions[emoji].append(uid)   # toggle on
    if not reactions[emoji]:
        del reactions[emoji]

    db.execute("UPDATE messages SET reactions=? WHERE id=?", (json.dumps(reactions), msg_id))
    db.commit()
    return jsonify({"ok": True, "reactions": reactions})


@app.route("/chat/message/<int:msg_id>/report", methods=["POST"])
@api_login_required
@json_safe
def report_message(msg_id):
    """Files a report on a message for moderator review. You can't report
    your own messages, and each person can only report a given message
    once (re-submitting just updates the reason/details)."""
    uid     = session["user_id"]
    reason  = request.form.get("reason", "other").strip()
    details = request.form.get("details", "").strip()[:500]
    if reason not in REPORT_REASONS:
        reason = "other"

    db  = get_db()
    msg = db.execute("SELECT * FROM messages WHERE id=?", (msg_id,)).fetchone()
    if not msg:
        return jsonify({"ok": False, "error": "Not found"}), 404
    if msg["sender_id"] == uid:
        return jsonify({"ok": False, "error": "You can't report your own message."}), 400

    conv = db.execute("SELECT * FROM conversations WHERE id=?", (msg["conversation_id"],)).fetchone()
    if not conv or not _user_can_access_conv(db, conv, uid):
        return jsonify({"ok": False, "error": "Not allowed"}), 403

    existing = db.execute(
        "SELECT id FROM message_reports WHERE message_id=? AND reporter_id=?", (msg_id, uid)
    ).fetchone()
    if existing:
        db.execute(
            "UPDATE message_reports SET reason=?, details=?, created_at=CURRENT_TIMESTAMP WHERE id=?",
            (reason, details, existing["id"])
        )
    else:
        db.execute(
            "INSERT INTO message_reports (message_id, reporter_id, reason, details) VALUES (?, ?, ?, ?)",
            (msg_id, uid, reason, details)
        )
    db.commit()
    return jsonify({"ok": True})


# -----------------------------------------------------------------------
# Routes -- Teams
# -----------------------------------------------------------------------
TEAM_TYPES = {
    "hackathon": "Hackathon",
    "coding_competition": "Coding Competition",
    "project": "Project",
    "other": "Other",
}


def get_team_member_count(team_id):
    """Return number of accepted members (including leader) in a team."""
    row = get_db().execute(
        "SELECT COUNT(*) AS c FROM team_members WHERE team_id=? AND status='accepted'",
        (team_id,)
    ).fetchone()
    return row["c"]


@app.route("/teams")
@login_required
def teams():
    uid = session["user_id"]
    db = get_db()
    filter_type = request.args.get("type", "")
    filter_status = request.args.get("status", "open")

    # Teams the user belongs to always show, even if they don't match the
    # current status/type filter (e.g. a team you're in that got closed
    # should stay visible instead of disappearing from the list).
    query = """
        SELECT t.*, u.name AS creator_name,
               (SELECT COUNT(*) FROM team_members tm
                WHERE tm.team_id=t.id AND tm.status='accepted') AS member_count
        FROM teams t
        JOIN users u ON u.id = t.creator_id
        WHERE (
            t.id IN (SELECT team_id FROM team_members WHERE user_id=? AND status='accepted')
            OR (1=1
    """
    params = [uid]
    if filter_type:
        query += " AND t.team_type = ?"
        params.append(filter_type)
    if filter_status:
        query += " AND t.status = ?"
        params.append(filter_status)
    query += ") )"
    query += " ORDER BY t.created_at DESC"

    all_teams = db.execute(query, params).fetchall()

    # teams user is a member of (accepted)
    my_team_ids = {
        r["team_id"] for r in db.execute(
            "SELECT team_id FROM team_members WHERE user_id=? AND status='accepted'", (uid,)
        )
    }
    # teams user has a pending request for
    pending_team_ids = {
        r["team_id"] for r in db.execute(
            "SELECT team_id FROM team_members WHERE user_id=? AND status='pending'", (uid,)
        )
    }

    return render_template(
        "teams.html",
        all_teams=all_teams,
        my_team_ids=my_team_ids,
        pending_team_ids=pending_team_ids,
        TEAM_TYPES=TEAM_TYPES,
        filter_type=filter_type,
        filter_status=filter_status,
    )


@app.route("/teams/create", methods=["POST"])
@login_required
def create_team():
    uid = session["user_id"]
    name = request.form.get("name", "").strip()
    description = request.form.get("description", "").strip()
    team_type = request.form.get("team_type", "other")
    max_members = request.form.get("max_members", 4, type=int)
    event_date = request.form.get("event_date", "").strip()

    if not name:
        flash("Team name is required.", "danger")
        return redirect(url_for("teams"))

    if team_type not in TEAM_TYPES:
        team_type = "other"
    max_members = max(2, min(20, max_members))

    db = get_db()
    cur = db.execute(
        """INSERT INTO teams (name, description, team_type, creator_id, max_members, event_date)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (name, description, team_type, uid, max_members, event_date or None)
    )
    team_id = cur.lastrowid
    # creator is automatically an accepted leader
    db.execute(
        "INSERT INTO team_members (team_id, user_id, role, status) VALUES (?, ?, 'leader', 'accepted')",
        (team_id, uid)
    )
    db.commit()
    flash(f'Team "{name}" created successfully!', "success")
    return redirect(url_for("team_detail", team_id=team_id))


@app.route("/teams/<int:team_id>")
@login_required
def team_detail(team_id):
    uid = session["user_id"]
    db = get_db()

    team = db.execute(
        """SELECT t.*, u.name AS creator_name
           FROM teams t JOIN users u ON u.id = t.creator_id
           WHERE t.id = ?""",
        (team_id,)
    ).fetchone()

    if not team:
        flash("Team not found.", "danger")
        return redirect(url_for("teams"))

    # all accepted members with user info
    members = db.execute(
        """SELECT tm.*, u.name, u.department, u.year, u.profile_picture, u.id AS uid
           FROM team_members tm JOIN users u ON u.id = tm.user_id
           WHERE tm.team_id = ? AND tm.status = 'accepted'
           ORDER BY tm.role DESC, tm.joined_at""",
        (team_id,)
    ).fetchall()

    # pending join requests (only visible to leader)
    pending_requests = db.execute(
        """SELECT tm.*, u.name, u.department, u.year, u.profile_picture, u.id AS uid
           FROM team_members tm JOIN users u ON u.id = tm.user_id
           WHERE tm.team_id = ? AND tm.status = 'pending'
           ORDER BY tm.joined_at""",
        (team_id,)
    ).fetchall()

    member_count = len(members)
    is_leader = team["creator_id"] == uid
    my_membership = db.execute(
        "SELECT * FROM team_members WHERE team_id=? AND user_id=?", (team_id, uid)
    ).fetchone()

    return render_template(
        "team_detail.html",
        team=team,
        members=members,
        pending_requests=pending_requests,
        member_count=member_count,
        is_leader=is_leader,
        my_membership=my_membership,
        TEAM_TYPES=TEAM_TYPES,
    )


@app.route("/teams/<int:team_id>/join", methods=["POST"])
@login_required
def join_team(team_id):
    uid = session["user_id"]
    db = get_db()

    team = db.execute("SELECT * FROM teams WHERE id=?", (team_id,)).fetchone()
    if not team:
        flash("Team not found.", "danger")
        return redirect(url_for("teams"))

    if team["status"] == "closed":
        flash("This team is no longer accepting members.", "warning")
        return redirect(url_for("team_detail", team_id=team_id))

    member_count = get_team_member_count(team_id)
    if member_count >= team["max_members"]:
        flash("This team is already full.", "warning")
        return redirect(url_for("team_detail", team_id=team_id))

    existing = db.execute(
        "SELECT * FROM team_members WHERE team_id=? AND user_id=?", (team_id, uid)
    ).fetchone()
    if existing:
        flash("You've already sent a request or are a member.", "info")
        return redirect(url_for("team_detail", team_id=team_id))

    db.execute(
        "INSERT INTO team_members (team_id, user_id, role, status) VALUES (?, ?, 'member', 'pending')",
        (team_id, uid)
    )
    db.commit()
    flash("Join request sent! Waiting for the team leader to accept.", "success")
    return redirect(url_for("team_detail", team_id=team_id))


@app.route("/teams/<int:team_id>/respond/<int:member_user_id>", methods=["POST"])
@login_required
def respond_team_request(team_id, member_user_id):
    uid = session["user_id"]
    db = get_db()

    team = db.execute("SELECT * FROM teams WHERE id=?", (team_id,)).fetchone()
    if not team or team["creator_id"] != uid:
        flash("Not authorised.", "danger")
        return redirect(url_for("teams"))

    action = request.form.get("action")
    if action == "accept":
        member_count = get_team_member_count(team_id)
        if member_count >= team["max_members"]:
            flash("Team is already full.", "warning")
            return redirect(url_for("team_detail", team_id=team_id))
        db.execute(
            "UPDATE team_members SET status='accepted' WHERE team_id=? AND user_id=?",
            (team_id, member_user_id)
        )
        db.commit()
        flash("Member accepted!", "success")
    elif action == "reject":
        db.execute(
            "UPDATE team_members SET status='rejected' WHERE team_id=? AND user_id=?",
            (team_id, member_user_id)
        )
        db.commit()
        flash("Request declined.", "info")

    return redirect(url_for("team_detail", team_id=team_id))


@app.route("/teams/<int:team_id>/leave", methods=["POST"])
@login_required
def leave_team(team_id):
    uid = session["user_id"]
    db = get_db()
    team = db.execute("SELECT * FROM teams WHERE id=?", (team_id,)).fetchone()
    if team and team["creator_id"] == uid:
        flash("You're the team leader — you can't leave your own team. Delete it instead.", "warning")
        return redirect(url_for("team_detail", team_id=team_id))
    db.execute("DELETE FROM team_members WHERE team_id=? AND user_id=?", (team_id, uid))
    db.commit()
    flash("You've left the team.", "info")
    return redirect(url_for("teams"))


@app.route("/teams/<int:team_id>/close", methods=["POST"])
@login_required
def close_team(team_id):
    uid = session["user_id"]
    db = get_db()
    db.execute(
        "UPDATE teams SET status='closed' WHERE id=? AND creator_id=?", (team_id, uid)
    )
    db.commit()
    flash("Team is now closed to new members.", "info")
    return redirect(url_for("team_detail", team_id=team_id))


@app.route("/teams/<int:team_id>/reopen", methods=["POST"])
@login_required
def reopen_team(team_id):
    uid = session["user_id"]
    db = get_db()
    db.execute(
        "UPDATE teams SET status='open' WHERE id=? AND creator_id=?", (team_id, uid)
    )
    db.commit()
    flash("Team is now open for new members!", "success")
    return redirect(url_for("team_detail", team_id=team_id))


@app.route("/teams/<int:team_id>/delete", methods=["POST"])
@login_required
def delete_team(team_id):
    uid = session["user_id"]
    db = get_db()
    db.execute("DELETE FROM teams WHERE id=? AND creator_id=?", (team_id, uid))
    db.commit()
    flash("Team deleted.", "info")
    return redirect(url_for("teams"))


# -----------------------------------------------------------------------
# Entry point
# -----------------------------------------------------------------------
if __name__ == "__main__":
    init_db()  # make sure tables exist before the app starts handling requests
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
else:
    # also ensure the DB exists when imported (e.g. by a WSGI server)
    init_db()
