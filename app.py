import json
import os
import sqlite3
import tempfile
from contextlib import contextmanager
from functools import wraps
from urllib.parse import urlparse, urlunparse

from flask import Flask, flash, redirect, render_template, request, session, url_for
from werkzeug.security import check_password_hash, generate_password_hash

try:
    import psycopg
    from psycopg.rows import dict_row
except ImportError:
    psycopg = None
    dict_row = None


app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY", "clave-local-de-desarrollo-cambiar-en-produccion")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")
ALBUM_FILE = os.path.join(DATA_DIR, "album.json")
USERS_FILE = os.path.join(DATA_DIR, "users.json")
DATABASE_URL = os.getenv("DATABASE_URL", "").strip()
USE_POSTGRES = bool(DATABASE_URL)
IS_VERCEL = os.getenv("VERCEL") == "1"
DB_FILE = os.getenv(
    "SQLITE_PATH",
    os.path.join(tempfile.gettempdir(), "album.db") if IS_VERCEL else os.path.join(DATA_DIR, "album.db"),
)


def normalize_database_url(url):
    """Vercel/Supabase usan PostgreSQL; psycopg espera postgresql://."""
    if url.startswith("postgres://"):
        return "postgresql://" + url[len("postgres://"):]
    return url


def convert_placeholders(sql):
    """Permite escribir SQL con ? y convertirlo a %s cuando se usa PostgreSQL."""
    return sql.replace("?", "%s") if USE_POSTGRES else sql


class Database:
    """Pequeño adaptador para usar SQLite local o PostgreSQL en producción."""

    def __init__(self, connection):
        self.connection = connection

    def execute(self, sql, params=()):
        return self.connection.execute(convert_placeholders(sql), params)

    def executescript(self, script):
        if USE_POSTGRES:
            for statement in script.split(";"):
                statement = statement.strip()
                if statement:
                    self.connection.execute(statement)
        else:
            self.connection.executescript(script)


def read_json(path, default):
    """Lee JSON en UTF-8. utf-8-sig evita problemas comunes de BOM en Windows."""
    if not os.path.exists(path):
        return default

    with open(path, "r", encoding="utf-8-sig") as file:
        return json.load(file)


@contextmanager
def get_db():
    """Abre una conexión de base de datos y devuelve filas tipo diccionario."""
    if USE_POSTGRES:
        if psycopg is None:
            raise RuntimeError("DATABASE_URL está configurado, pero psycopg no está instalado.")
        connection = psycopg.connect(normalize_database_url(DATABASE_URL), row_factory=dict_row)
    else:
        os.makedirs(os.path.dirname(DB_FILE), exist_ok=True)
        connection = sqlite3.connect(DB_FILE)
        connection.row_factory = sqlite3.Row

    db = Database(connection)
    try:
        yield db
        connection.commit()
    finally:
        connection.close()


def init_db():
    """Crea las tablas si no existen. Es seguro llamarla muchas veces."""
    sqlite_schema = """
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT NOT NULL UNIQUE,
                password_hash TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS owned_stickers (
                user_id INTEGER NOT NULL,
                sticker_code TEXT NOT NULL,
                PRIMARY KEY (user_id, sticker_code),
                FOREIGN KEY (user_id) REFERENCES users(id)
            );

            CREATE TABLE IF NOT EXISTS duplicate_stickers (
                user_id INTEGER NOT NULL,
                sticker_code TEXT NOT NULL,
                quantity INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (user_id, sticker_code),
                FOREIGN KEY (user_id) REFERENCES users(id)
            );

            CREATE TABLE IF NOT EXISTS friend_requests (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                sender_id INTEGER NOT NULL,
                receiver_id INTEGER NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (sender_id) REFERENCES users(id),
                FOREIGN KEY (receiver_id) REFERENCES users(id)
            );

            CREATE TABLE IF NOT EXISTS friendships (
                user_id INTEGER NOT NULL,
                friend_id INTEGER NOT NULL,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (user_id, friend_id),
                FOREIGN KEY (user_id) REFERENCES users(id),
                FOREIGN KEY (friend_id) REFERENCES users(id)
            );
            """

    postgres_schema = """
            CREATE TABLE IF NOT EXISTS users (
                id SERIAL PRIMARY KEY,
                username TEXT NOT NULL UNIQUE,
                password_hash TEXT NOT NULL,
                created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS owned_stickers (
                user_id INTEGER NOT NULL REFERENCES users(id),
                sticker_code TEXT NOT NULL,
                PRIMARY KEY (user_id, sticker_code)
            );

            CREATE TABLE IF NOT EXISTS duplicate_stickers (
                user_id INTEGER NOT NULL REFERENCES users(id),
                sticker_code TEXT NOT NULL,
                quantity INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (user_id, sticker_code)
            );

            CREATE TABLE IF NOT EXISTS friend_requests (
                id SERIAL PRIMARY KEY,
                sender_id INTEGER NOT NULL REFERENCES users(id),
                receiver_id INTEGER NOT NULL REFERENCES users(id),
                status TEXT NOT NULL DEFAULT 'pending',
                created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS friendships (
                user_id INTEGER NOT NULL REFERENCES users(id),
                friend_id INTEGER NOT NULL REFERENCES users(id),
                created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (user_id, friend_id)
            );
            """

    with get_db() as db:
        db.executescript(postgres_schema if USE_POSTGRES else sqlite_schema)


def migrate_users_json():
    """Migra datos anteriores desde users.json solo en SQLite local."""
    if USE_POSTGRES or IS_VERCEL or not os.path.exists(USERS_FILE):
        return

    old_users = read_json(USERS_FILE, {})
    if not old_users:
        return

    with get_db() as db:
        existing = db.execute("SELECT COUNT(*) AS total FROM users").fetchone()["total"]
        if existing:
            return

        id_by_username = {}
        for username, data in old_users.items():
            password_hash = data.get("password") or generate_password_hash("1234")
            cursor = db.execute(
                "INSERT INTO users (username, password_hash) VALUES (?, ?)",
                (username.lower(), password_hash),
            )
            id_by_username[username.lower()] = cursor.lastrowid

        for username, data in old_users.items():
            user_id = id_by_username[username.lower()]
            for code in data.get("owned", []):
                db.execute(
                    "INSERT OR IGNORE INTO owned_stickers (user_id, sticker_code) VALUES (?, ?)",
                    (user_id, code),
                )

            for code, quantity in data.get("duplicates", {}).items():
                if quantity > 0:
                    db.execute(
                        "INSERT OR REPLACE INTO duplicate_stickers (user_id, sticker_code, quantity) VALUES (?, ?, ?)",
                        (user_id, code, quantity),
                    )

        for username, data in old_users.items():
            user_id = id_by_username[username.lower()]
            for friend in data.get("friends", []):
                friend_id = id_by_username.get(friend.lower())
                if friend_id and friend_id != user_id:
                    create_friendship(db, user_id, friend_id)


def load_album():
    return read_json(ALBUM_FILE, {"specials": [], "groups": []})


def get_user_by_username(username):
    with get_db() as db:
        return db.execute(
            "SELECT * FROM users WHERE username = ?",
            (username.lower(),),
        ).fetchone()


def get_user_by_id(user_id):
    with get_db() as db:
        return db.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()


def get_current_user():
    user_id = session.get("user_id")
    if not user_id:
        return None
    return get_user_by_id(user_id)


def login_required(view):
    @wraps(view)
    def wrapped_view(*args, **kwargs):
        if "user_id" not in session:
            flash("Inicia sesión para ver tu álbum.", "warning")
            return redirect(url_for("login"))
        return view(*args, **kwargs)

    return wrapped_view


@app.context_processor
def inject_user():
    return {"session_user": session.get("username")}


def album_stickers(album):
    """Convierte el catálogo editable en una lista plana de láminas."""
    stickers = []

    for sticker in album.get("specials", []):
        sticker_copy = sticker.copy()
        sticker_copy["team_code"] = "SPECIALS"
        sticker_copy["team_name"] = "Especiales"
        sticker_copy["group"] = "Especiales"
        stickers.append(sticker_copy)

    for group in album.get("groups", []):
        for team in group.get("teams", []):
            for sticker in team.get("stickers", []):
                sticker_copy = sticker.copy()
                sticker_copy["team_code"] = team["code"]
                sticker_copy["team_name"] = team["name"]
                sticker_copy["group"] = group["letter"]
                stickers.append(sticker_copy)

    return stickers


def find_team(album, code):
    if not code:
        return None

    code = code.upper()
    for group in album.get("groups", []):
        for team in group.get("teams", []):
            if team["code"] == code:
                team_copy = team.copy()
                team_copy["group"] = group["letter"]
                return team_copy
    return None


def find_group(album, letter):
    letter = letter.upper()
    for group in album.get("groups", []):
        if group["letter"] == letter:
            return group
    return None


def get_owned_codes(user_id):
    with get_db() as db:
        rows = db.execute(
            "SELECT sticker_code FROM owned_stickers WHERE user_id = ?",
            (user_id,),
        ).fetchall()
    return {row["sticker_code"] for row in rows}


def get_duplicates(user_id):
    with get_db() as db:
        rows = db.execute(
            "SELECT sticker_code, quantity FROM duplicate_stickers WHERE user_id = ? AND quantity > 0",
            (user_id,),
        ).fetchall()
    return {row["sticker_code"]: row["quantity"] for row in rows}


def calculate_progress(user_id, stickers):
    owned = get_owned_codes(user_id)
    duplicates = get_duplicates(user_id)
    total = len(stickers)
    obtained = len([sticker for sticker in stickers if sticker["code"] in owned])
    missing = total - obtained
    percent = round((obtained / total) * 100, 1) if total else 0
    duplicate_total = sum(duplicates.values())
    return {
        "total": total,
        "total_stickers": total,
        "obtained": obtained,
        "owned_count": obtained,
        "missing": missing,
        "missing_count": missing,
        "duplicate_total": duplicate_total,
        "percent": percent,
    }


def team_progress(user_id, team):
    owned = get_owned_codes(user_id)
    total = len(team.get("stickers", []))
    obtained = len([sticker for sticker in team.get("stickers", []) if sticker["code"] in owned])
    percent = round((obtained / total) * 100, 1) if total else 0
    return {"total": total, "obtained": obtained, "missing": total - obtained, "percent": percent}


def group_progress(user_id, group):
    """Calcula el avance total de un grupo sumando sus selecciones."""
    total = 0
    obtained = 0

    for team in group.get("teams", []):
        data = team_progress(user_id, team)
        total += data["total"]
        obtained += data["obtained"]

    percent = round((obtained / total) * 100, 1) if total else 0
    return {"total": total, "obtained": obtained, "missing": total - obtained, "percent": percent}


def stickers_with_status(stickers, user_id):
    owned = get_owned_codes(user_id)
    duplicates = get_duplicates(user_id)
    enriched = []

    for sticker in stickers:
        sticker_copy = sticker.copy()
        sticker_copy["owned"] = sticker["code"] in owned
        sticker_copy["duplicates"] = duplicates.get(sticker["code"], 0)
        enriched.append(sticker_copy)

    return enriched


def create_friendship(db, user_id, friend_id):
    if USE_POSTGRES:
        sql = "INSERT INTO friendships (user_id, friend_id) VALUES (?, ?) ON CONFLICT DO NOTHING"
    else:
        sql = "INSERT OR IGNORE INTO friendships (user_id, friend_id) VALUES (?, ?)"

    db.execute(sql, (user_id, friend_id))
    db.execute(sql, (friend_id, user_id))


def are_friends(user_id, friend_id):
    with get_db() as db:
        row = db.execute(
            "SELECT 1 FROM friendships WHERE user_id = ? AND friend_id = ?",
            (user_id, friend_id),
        ).fetchone()
    return row is not None


def pending_request_exists(user_id, friend_id):
    with get_db() as db:
        row = db.execute(
            """
            SELECT 1 FROM friend_requests
            WHERE status = 'pending'
            AND (
                (sender_id = ? AND receiver_id = ?)
                OR (sender_id = ? AND receiver_id = ?)
            )
            """,
            (user_id, friend_id, friend_id, user_id),
        ).fetchone()
    return row is not None


def get_friend_rows(user_id):
    with get_db() as db:
        return db.execute(
            """
            SELECT users.id, users.username,
                   COALESCE(SUM(duplicate_stickers.quantity), 0) AS duplicate_total
            FROM friendships
            JOIN users ON users.id = friendships.friend_id
            LEFT JOIN duplicate_stickers ON duplicate_stickers.user_id = users.id
            WHERE friendships.user_id = ?
            GROUP BY users.id, users.username
            ORDER BY users.username
            """,
            (user_id,),
        ).fetchall()


def get_requests_for_user(user_id):
    with get_db() as db:
        received = db.execute(
            """
            SELECT friend_requests.id, users.username, friend_requests.created_at
            FROM friend_requests
            JOIN users ON users.id = friend_requests.sender_id
            WHERE friend_requests.receiver_id = ? AND friend_requests.status = 'pending'
            ORDER BY friend_requests.created_at DESC
            """,
            (user_id,),
        ).fetchall()
        sent = db.execute(
            """
            SELECT users.username, friend_requests.created_at
            FROM friend_requests
            JOIN users ON users.id = friend_requests.receiver_id
            WHERE friend_requests.sender_id = ? AND friend_requests.status = 'pending'
            ORDER BY friend_requests.created_at DESC
            """,
            (user_id,),
        ).fetchall()
    return received, sent


def group_repeated_stickers(stickers, duplicates):
    grouped = []
    for sticker in stickers:
        amount = duplicates.get(sticker["code"], 0)
        if amount <= 0:
            continue

        group_name = f"Grupo {sticker['group']}" if sticker["group"] != "Especiales" else "Especiales"
        team_name = sticker.get("team_name", "Especiales")
        group_entry = next((item for item in grouped if item["name"] == group_name), None)
        if not group_entry:
            group_entry = {"name": group_name, "teams": []}
            grouped.append(group_entry)

        team_entry = next((item for item in group_entry["teams"] if item["name"] == team_name), None)
        if not team_entry:
            team_entry = {"name": team_name, "stickers": []}
            group_entry["teams"].append(team_entry)

        sticker_copy = sticker.copy()
        sticker_copy["duplicates"] = amount
        team_entry["stickers"].append(sticker_copy)

    return grouped


@app.route("/")
@login_required
def index():
    album = load_album()
    user = get_current_user()
    stickers = album_stickers(album)
    progress = calculate_progress(user["id"], stickers)

    groups = []
    for group in album.get("groups", []):
        data = group_progress(user["id"], group)

        groups.append({
            "letter": group["letter"],
            "teams": group["teams"],
            "total": data["total"],
            "obtained": data["obtained"],
            "missing": data["missing"],
            "percent": data["percent"],
        })

    return render_template("index.html", username=user["username"], progress=progress, groups=groups)


@app.route("/registro", methods=["GET", "POST"])
def registro():
    if request.method == "POST":
        username = request.form.get("username", "").strip().lower()
        password = request.form.get("password", "").strip()

        if not username or not password:
            flash("Escribe usuario y contraseña.", "danger")
            return redirect(url_for("registro"))

        if get_user_by_username(username):
            flash("Ese usuario ya existe.", "danger")
            return redirect(url_for("registro"))

        password_hash = generate_password_hash(password)
        with get_db() as db:
            if USE_POSTGRES:
                cursor = db.execute(
                    "INSERT INTO users (username, password_hash) VALUES (?, ?) RETURNING id",
                    (username, password_hash),
                )
                user_id = cursor.fetchone()["id"]
            else:
                cursor = db.execute(
                    "INSERT INTO users (username, password_hash) VALUES (?, ?)",
                    (username, password_hash),
                )
                user_id = cursor.lastrowid

        session["user_id"] = user_id
        session["username"] = username
        flash("Cuenta creada. Tu álbum está listo.", "success")
        return redirect(url_for("index"))

    return render_template("registro.html")


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form.get("username", "").strip().lower()
        password = request.form.get("password", "").strip()
        user = get_user_by_username(username)

        if not user or not check_password_hash(user["password_hash"], password):
            flash("Usuario o contraseña incorrectos.", "danger")
            return redirect(url_for("login"))

        session["user_id"] = user["id"]
        session["username"] = user["username"]
        flash("Sesión iniciada.", "success")
        return redirect(url_for("index"))

    return render_template("login.html")


@app.route("/logout")
def logout():
    session.clear()
    flash("Sesión cerrada.", "success")
    return redirect(url_for("login"))


@app.route("/album")
@login_required
def album():
    album_data = load_album()
    user = get_current_user()
    progress = calculate_progress(user["id"], album_stickers(album_data))

    for group in album_data.get("groups", []):
        group["progress"] = group_progress(user["id"], group)
        for team in group.get("teams", []):
            team["progress"] = team_progress(user["id"], team)

    specials = stickers_with_status(album_data.get("specials", []), user["id"])
    return render_template("album.html", album=album_data, specials=specials, progress=progress)


@app.route("/grupo/<letter>")
@login_required
def grupo(letter):
    album_data = load_album()
    user = get_current_user()
    group = find_group(album_data, letter)
    if not group:
        flash("Grupo no encontrado.", "danger")
        return redirect(url_for("album"))

    group["progress"] = group_progress(user["id"], group)
    for team in group.get("teams", []):
        team["progress"] = team_progress(user["id"], team)

    return render_template("grupo.html", group=group)


@app.route("/pais/<code>")
@login_required
def pais(code):
    album_data = load_album()
    user = get_current_user()
    team = find_team(album_data, code)
    if not team:
        flash("Selección no encontrada.", "danger")
        return redirect(url_for("album"))

    stickers = stickers_with_status(team.get("stickers", []), user["id"])
    progress = team_progress(user["id"], team)
    return render_template("pais.html", team=team, stickers=stickers, progress=progress)


@app.post("/lamina/<code>/toggle")
@login_required
def toggle_lamina(code):
    user_id = session["user_id"]
    with get_db() as db:
        exists = db.execute(
            "SELECT 1 FROM owned_stickers WHERE user_id = ? AND sticker_code = ?",
            (user_id, code),
        ).fetchone()

        if exists:
            db.execute(
                "DELETE FROM owned_stickers WHERE user_id = ? AND sticker_code = ?",
                (user_id, code),
            )
            flash("Lámina marcada como faltante.", "success")
        else:
            db.execute(
                "INSERT INTO owned_stickers (user_id, sticker_code) VALUES (?, ?)",
                (user_id, code),
            )
            flash("Lámina marcada como obtenida.", "success")

    return redirect(request.referrer or url_for("album"))


@app.post("/lamina/<code>/repetida/sumar")
@login_required
def sumar_repetida(code):
    with get_db() as db:
        db.execute(
            """
            INSERT INTO duplicate_stickers (user_id, sticker_code, quantity)
            VALUES (?, ?, 1)
            ON CONFLICT(user_id, sticker_code)
            DO UPDATE SET quantity = quantity + 1
            """,
            (session["user_id"], code),
        )

    flash("Repetida agregada.", "success")
    return redirect(request.referrer or url_for("repetidas"))


@app.post("/lamina/<code>/repetida/restar")
@login_required
def restar_repetida(code):
    user_id = session["user_id"]
    with get_db() as db:
        row = db.execute(
            "SELECT quantity FROM duplicate_stickers WHERE user_id = ? AND sticker_code = ?",
            (user_id, code),
        ).fetchone()

        if row and row["quantity"] > 1:
            db.execute(
                "UPDATE duplicate_stickers SET quantity = quantity - 1 WHERE user_id = ? AND sticker_code = ?",
                (user_id, code),
            )
        else:
            db.execute(
                "DELETE FROM duplicate_stickers WHERE user_id = ? AND sticker_code = ?",
                (user_id, code),
            )

    flash("Repetida actualizada.", "success")
    return redirect(request.referrer or url_for("repetidas"))


@app.route("/repetidas")
@app.route("/repetidas/<team_code>")
@login_required
def repetidas(team_code=None):
    album_data = load_album()
    stickers = album_stickers(album_data)
    duplicates = get_duplicates(session["user_id"])
    teams = [team for group in album_data.get("groups", []) for team in group.get("teams", [])]
    selected_specials = bool(team_code and team_code.upper() == "SPECIALS")
    selected_team = find_team(album_data, team_code) if team_code and not selected_specials else None

    if team_code:
        stickers = [sticker for sticker in stickers if sticker.get("team_code") == team_code.upper()]

    grouped = group_repeated_stickers(stickers, duplicates)
    return render_template(
        "repetidas.html",
        grouped=grouped,
        teams=teams,
        selected_team=selected_team,
        selected_specials=selected_specials,
        friend=None,
    )


@app.route("/amigos", methods=["GET", "POST"])
@login_required
def amigos():
    user_id = session["user_id"]

    if request.method == "POST":
        friend_username = request.form.get("friend", "").strip().lower()
        friend = get_user_by_username(friend_username)

        if not friend_username:
            flash("Escribe un nombre de usuario.", "warning")
        elif not friend:
            flash("Ese usuario no existe.", "danger")
        elif friend["id"] == user_id:
            flash("No puedes enviarte una solicitud a ti mismo.", "warning")
        elif are_friends(user_id, friend["id"]):
            flash("Ese usuario ya está en tu lista de amigos.", "warning")
        elif pending_request_exists(user_id, friend["id"]):
            flash("Ya existe una solicitud pendiente entre ustedes.", "warning")
        else:
            with get_db() as db:
                db.execute(
                    "INSERT INTO friend_requests (sender_id, receiver_id) VALUES (?, ?)",
                    (user_id, friend["id"]),
                )
            flash("Solicitud de amistad enviada.", "success")

        return redirect(url_for("amigos", buscar=friend_username))

    search = request.args.get("buscar", "").strip().lower()
    results = []
    if search:
        with get_db() as db:
            results = db.execute(
                """
                SELECT id, username FROM users
                WHERE username LIKE ? AND id != ?
                ORDER BY username
                LIMIT 12
                """,
                (f"%{search}%", user_id),
            ).fetchall()

    friends = get_friend_rows(user_id)
    received, sent = get_requests_for_user(user_id)
    return render_template(
        "amigos.html",
        friends=friends,
        received_requests=received,
        sent_requests=sent,
        search=search,
        results=results,
    )


@app.post("/solicitudes/<int:request_id>/<action>")
@login_required
def responder_solicitud(request_id, action):
    if action not in ["aceptar", "rechazar"]:
        flash("Acción no válida.", "danger")
        return redirect(url_for("amigos"))

    with get_db() as db:
        friend_request = db.execute(
            """
            SELECT * FROM friend_requests
            WHERE id = ? AND receiver_id = ? AND status = 'pending'
            """,
            (request_id, session["user_id"]),
        ).fetchone()

        if not friend_request:
            flash("Solicitud no encontrada.", "danger")
            return redirect(url_for("amigos"))

        if action == "aceptar":
            db.execute(
                "UPDATE friend_requests SET status = 'accepted' WHERE id = ?",
                (request_id,),
            )
            create_friendship(db, friend_request["sender_id"], friend_request["receiver_id"])
            flash("Solicitud aceptada.", "success")
        else:
            db.execute(
                "UPDATE friend_requests SET status = 'rejected' WHERE id = ?",
                (request_id,),
            )
            flash("Solicitud rechazada.", "success")

    return redirect(url_for("amigos"))


@app.route("/amigos/<username>")
@login_required
def amigo_detalle(username):
    friend = get_user_by_username(username)
    if not friend or not are_friends(session["user_id"], friend["id"]):
        flash("Primero agrega ese usuario como amigo.", "warning")
        return redirect(url_for("amigos"))

    album_data = load_album()
    stickers = album_stickers(album_data)
    grouped = group_repeated_stickers(stickers, get_duplicates(friend["id"]))
    return render_template(
        "repetidas.html",
        grouped=grouped,
        teams=[],
        selected_team=None,
        selected_specials=False,
        friend=friend["username"],
    )


@app.route("/comparar/<username>")
@login_required
def comparar(username):
    friend = get_user_by_username(username)
    if not friend or not are_friends(session["user_id"], friend["id"]):
        flash("Primero agrega ese usuario como amigo.", "warning")
        return redirect(url_for("amigos"))

    album_data = load_album()
    stickers = album_stickers(album_data)
    my_owned = get_owned_codes(session["user_id"])
    friend_duplicates = get_duplicates(friend["id"])

    useful = []
    for sticker in stickers:
        amount = friend_duplicates.get(sticker["code"], 0)
        if sticker["code"] not in my_owned and amount > 0:
            sticker_copy = sticker.copy()
            sticker_copy["duplicates"] = amount
            useful.append(sticker_copy)

    return render_template("comparar.html", friend=friend["username"], useful=useful)


@app.errorhandler(404)
def page_not_found(error):
    flash("La página que buscas no existe. Te llevamos al inicio.", "warning")
    return redirect(url_for("index") if session.get("user_id") else url_for("login"))


@app.errorhandler(500)
def internal_error(error):
    return (
        "Ocurrió un problema temporal. Intenta de nuevo en unos minutos.",
        500,
        {"Content-Type": "text/plain; charset=utf-8"},
    )


init_db()
migrate_users_json()


if __name__ == "__main__":
    app.run(debug=os.getenv("FLASK_DEBUG") == "1")
