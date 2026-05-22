import json
import os
from functools import wraps

from flask import Flask, flash, redirect, render_template, request, session, url_for
from werkzeug.security import check_password_hash, generate_password_hash


app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY", "clave-local-de-desarrollo-cambiar-en-produccion")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")
ALBUM_FILE = os.path.join(DATA_DIR, "album.json")
USERS_FILE = os.path.join(DATA_DIR, "users.json")


def read_json(path, default):
    """Lee un archivo JSON usando UTF-8."""
    if not os.path.exists(path):
        return default

    with open(path, "r", encoding="utf-8-sig") as file:
        return json.load(file)


def write_json(path, data):
    """Guarda JSON con sangría para que sea fácil de leer y editar."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as file:
        json.dump(data, file, ensure_ascii=False, indent=4)


def empty_user_store():
    return {"users": {}, "friend_requests": [], "next_request_id": 1}


def normalize_username(username):
    return username.strip().lower()


def normalize_user_store(data):
    """Acepta el formato nuevo o el formato antiguo de users.json."""
    if not data:
        return empty_user_store()

    if "users" in data:
        data.setdefault("friend_requests", [])
        data.setdefault("next_request_id", 1)
        return data

    users = {}
    for username, values in data.items():
        clean_username = normalize_username(username)
        users[clean_username] = {
            "password": values.get("password", ""),
            "owned": sorted(set(values.get("owned", []))),
            "duplicates": {
                code: int(amount)
                for code, amount in values.get("duplicates", {}).items()
                if int(amount) > 0
            },
            "friends": sorted(set(normalize_username(friend) for friend in values.get("friends", []))),
        }

    return {"users": users, "friend_requests": [], "next_request_id": 1}


def load_users():
    return normalize_user_store(read_json(USERS_FILE, empty_user_store()))


def save_users(data):
    write_json(USERS_FILE, data)


def load_album():
    return read_json(ALBUM_FILE, {"name": "Álbum Mundial 2026", "specials": [], "groups": []})


def get_user(username):
    users = load_users()["users"]
    return users.get(normalize_username(username))


def get_current_username():
    return session.get("username")


def get_current_user():
    username = get_current_username()
    if not username:
        return None
    return get_user(username)


def login_required(view):
    @wraps(view)
    def wrapped_view(*args, **kwargs):
        if not get_current_username() or not get_current_user():
            flash("Inicia sesión para abrir tu álbum.", "warning")
            return redirect(url_for("login"))
        return view(*args, **kwargs)

    return wrapped_view


@app.context_processor
def inject_user():
    return {"session_user": get_current_username()}


def album_stickers(album):
    stickers = []

    for sticker in album.get("specials", []):
        item = sticker.copy()
        item["group"] = "Especiales"
        item["team_code"] = "SPECIALS"
        item["team_name"] = "Láminas especiales"
        stickers.append(item)

    for group in album.get("groups", []):
        for team in group.get("teams", []):
            for sticker in team.get("stickers", []):
                item = sticker.copy()
                item["group"] = group.get("letter", "")
                item["team_code"] = team.get("code", "")
                item["team_name"] = team.get("name", "")
                stickers.append(item)

    return stickers


def find_group(album, letter):
    selected = letter.upper()
    for group in album.get("groups", []):
        if group.get("letter", "").upper() == selected:
            return group
    return None


def find_team(album, code):
    selected = (code or "").upper()
    for group in album.get("groups", []):
        for team in group.get("teams", []):
            if team.get("code", "").upper() == selected:
                team = team.copy()
                team["group"] = group.get("letter", "")
                return team
    return None


def sticker_exists(code):
    return any(sticker["code"] == code for sticker in album_stickers(load_album()))


def owned_set(user):
    return set(user.get("owned", []))


def duplicate_map(user):
    return {code: int(amount) for code, amount in user.get("duplicates", {}).items() if int(amount) > 0}


def calculate_progress(stickers, owned, duplicates):
    total = len(stickers)
    owned_count = len([sticker for sticker in stickers if sticker["code"] in owned])
    duplicate_total = sum(duplicates.values())
    missing_count = max(total - owned_count, 0)
    percent = round((owned_count / total) * 100) if total else 0

    return {
        "total_stickers": total,
        "owned_count": owned_count,
        "duplicate_total": duplicate_total,
        "missing_count": missing_count,
        "percent": percent,
    }


def compact_sticker_code(code, team_code=None):
    """Convierte CZE 1 o FW16 en un código corto tipo CZE 01."""
    clean_code = str(code).strip().upper()
    clean_team = str(team_code or "").strip().upper()

    if clean_team == "SPECIALS":
        clean_team = "FWC"

    if " " in clean_code:
        prefix, number = clean_code.split(" ", 1)
    else:
        prefix = clean_team or clean_code[:3]
        number = "".join(character for character in clean_code if character.isdigit())

    prefix = (clean_team or prefix)[:3].ljust(3, "X")
    digits = "".join(character for character in number if character.isdigit()) or "0"
    return f"{prefix} {int(digits):02d}"


def compact_album_lists(stickers, owned, duplicates, limit=18):
    missing = []
    repeated = []

    for sticker in stickers:
        item = sticker.copy()
        item["compact_code"] = compact_sticker_code(sticker["code"], sticker.get("team_code"))
        if sticker["code"] not in owned and len(missing) < limit:
            missing.append(item)
        if duplicates.get(sticker["code"], 0) > 0 and len(repeated) < limit:
            item["duplicates"] = duplicates[sticker["code"]]
            repeated.append(item)

    return {"missing": missing, "repeated": repeated}


def team_progress(team, owned):
    stickers = team.get("stickers", [])
    obtained = len([sticker for sticker in stickers if sticker["code"] in owned])
    total = len(stickers)
    return {
        "total": total,
        "obtained": obtained,
        "missing": max(total - obtained, 0),
        "percent": round((obtained / total) * 100) if total else 0,
    }


def group_progress(group, owned):
    stickers = [sticker for team in group.get("teams", []) for sticker in team.get("stickers", [])]
    obtained = len([sticker for sticker in stickers if sticker["code"] in owned])
    total = len(stickers)
    return {
        "total": total,
        "obtained": obtained,
        "missing": max(total - obtained, 0),
        "percent": round((obtained / total) * 100) if total else 0,
    }


def stickers_with_status(stickers, owned, duplicates):
    result = []
    for sticker in stickers:
        item = sticker.copy()
        item["owned"] = sticker["code"] in owned
        item["duplicates"] = duplicates.get(sticker["code"], 0)
        result.append(item)
    return result


def group_repeated_stickers(stickers, duplicates):
    grouped = []
    group_lookup = {}
    team_lookup = {}

    for sticker in stickers:
        amount = duplicates.get(sticker["code"], 0)
        if amount <= 0:
            continue

        group_name = sticker.get("group", "Especiales")
        team_name = sticker.get("team_name", "Láminas especiales")

        if group_name not in group_lookup:
            group_lookup[group_name] = {"name": group_name, "teams": []}
            grouped.append(group_lookup[group_name])

        team_key = (group_name, team_name)
        if team_key not in team_lookup:
            display_code = sticker.get("team_code", "")
            if display_code == "SPECIALS":
                display_code = "FWC"
            team_lookup[team_key] = {"name": team_name, "code": display_code, "stickers": []}
            group_lookup[group_name]["teams"].append(team_lookup[team_key])

        item = sticker.copy()
        item["duplicates"] = amount
        item["compact_code"] = compact_sticker_code(sticker["code"], sticker.get("team_code"))
        team_lookup[team_key]["stickers"].append(item)

    return grouped


def flat_repeated_stickers(stickers, duplicates):
    repeated = []

    for sticker in stickers:
        amount = duplicates.get(sticker["code"], 0)
        if amount <= 0:
            continue

        repeated.append({
            "compact_code": compact_sticker_code(sticker["code"], sticker.get("team_code")),
            "duplicates": amount,
        })

    return repeated


def are_friends(username, friend_username):
    user = get_user(username)
    if not user:
        return False
    return normalize_username(friend_username) in user.get("friends", [])


def pending_request_exists(data, sender, receiver):
    sender = normalize_username(sender)
    receiver = normalize_username(receiver)
    for friend_request in data.get("friend_requests", []):
        same_direction = friend_request["from"] == sender and friend_request["to"] == receiver
        reverse_direction = friend_request["from"] == receiver and friend_request["to"] == sender
        if friend_request.get("status") == "pending" and (same_direction or reverse_direction):
            return True
    return False


def add_friendship(data, username, friend_username):
    username = normalize_username(username)
    friend_username = normalize_username(friend_username)
    users = data["users"]

    if friend_username not in users[username]["friends"]:
        users[username]["friends"].append(friend_username)
        users[username]["friends"].sort()

    if username not in users[friend_username]["friends"]:
        users[friend_username]["friends"].append(username)
        users[friend_username]["friends"].sort()


def friend_rows(username):
    data = load_users()
    users = data["users"]
    user = users[username]
    rows = []

    for friend_username in user.get("friends", []):
        friend = users.get(friend_username)
        if not friend:
            continue
        rows.append({
            "username": friend_username,
            "duplicate_total": sum(duplicate_map(friend).values()),
        })

    return rows


def request_rows(username):
    data = load_users()
    received = []
    sent = []

    for friend_request in data.get("friend_requests", []):
        if friend_request.get("status") != "pending":
            continue
        if friend_request["to"] == username:
            received.append({"id": friend_request["id"], "username": friend_request["from"]})
        elif friend_request["from"] == username:
            sent.append({"id": friend_request["id"], "username": friend_request["to"]})

    return received, sent


@app.route("/")
@login_required
def index():
    album = load_album()
    user = get_current_user()
    owned = owned_set(user)
    duplicates = duplicate_map(user)
    stickers = album_stickers(album)
    progress = calculate_progress(stickers, owned, duplicates)
    compact_lists = compact_album_lists(stickers, owned, duplicates)

    groups = []
    for group in album.get("groups", []):
        data = group_progress(group, owned)
        groups.append({
            "letter": group["letter"],
            "teams": group["teams"],
            "total": data["total"],
            "obtained": data["obtained"],
            "missing": data["missing"],
            "percent": data["percent"],
        })

    return render_template(
        "index.html",
        username=get_current_username(),
        progress=progress,
        groups=groups,
        compact_lists=compact_lists,
    )


@app.route("/registro", methods=["GET", "POST"])
def registro():
    if request.method == "POST":
        username = normalize_username(request.form.get("username", ""))
        password = request.form.get("password", "").strip()
        data = load_users()

        if not username or not password:
            flash("Escribe usuario y contraseña.", "danger")
            return redirect(url_for("registro"))

        if username in data["users"]:
            flash("Ese usuario ya existe.", "danger")
            return redirect(url_for("registro"))

        data["users"][username] = {
            "password": generate_password_hash(password),
            "owned": [],
            "duplicates": {},
            "friends": [],
        }
        save_users(data)

        session["username"] = username
        flash("Cuenta creada. Tu álbum está listo.", "success")
        return redirect(url_for("index"))

    return render_template("registro.html")


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = normalize_username(request.form.get("username", ""))
        password = request.form.get("password", "").strip()
        user = get_user(username)

        if not user or not check_password_hash(user.get("password", ""), password):
            flash("Usuario o contraseña incorrectos.", "danger")
            return redirect(url_for("login"))

        session["username"] = username
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
    owned = owned_set(user)
    duplicates = duplicate_map(user)
    progress = calculate_progress(album_stickers(album_data), owned, duplicates)

    for group in album_data.get("groups", []):
        group["progress"] = group_progress(group, owned)
        for team in group.get("teams", []):
            team["progress"] = team_progress(team, owned)

    specials = stickers_with_status(album_data.get("specials", []), owned, duplicates)
    return render_template("album.html", album=album_data, specials=specials, progress=progress)


@app.route("/grupo/<letter>")
@login_required
def grupo(letter):
    album_data = load_album()
    owned = owned_set(get_current_user())
    group = find_group(album_data, letter)

    if not group:
        flash("Grupo no encontrado.", "danger")
        return redirect(url_for("album"))

    group["progress"] = group_progress(group, owned)
    for team in group.get("teams", []):
        team["progress"] = team_progress(team, owned)

    return render_template("grupo.html", group=group)


@app.route("/pais/<code>")
@login_required
def pais(code):
    album_data = load_album()
    user = get_current_user()
    owned = owned_set(user)
    duplicates = duplicate_map(user)
    team = find_team(album_data, code)

    if not team:
        flash("Selección no encontrada.", "danger")
        return redirect(url_for("album"))

    stickers = stickers_with_status(team.get("stickers", []), owned, duplicates)
    progress = team_progress(team, owned)
    return render_template("pais.html", team=team, stickers=stickers, progress=progress)


@app.post("/lamina/<code>/toggle")
@login_required
def toggle_lamina(code):
    if not sticker_exists(code):
        flash("Lámina no encontrada.", "danger")
        return redirect(request.referrer or url_for("album"))

    data = load_users()
    user = data["users"][get_current_username()]
    owned = set(user.get("owned", []))

    if code in owned:
        owned.remove(code)
        user.get("duplicates", {}).pop(code, None)
        message = "Lámina marcada como faltante."
    else:
        owned.add(code)
        message = "Lámina marcada como obtenida."

    user["owned"] = sorted(owned)
    save_users(data)
    flash(message, "success")
    return redirect(request.referrer or url_for("album"))


@app.post("/lamina/<code>/repetida/sumar")
@login_required
def sumar_repetida(code):
    if not sticker_exists(code):
        flash("Lámina no encontrada.", "danger")
        return redirect(request.referrer or url_for("album"))

    data = load_users()
    user = data["users"][get_current_username()]

    if code not in user.get("owned", []):
        flash("No puedes marcar esta lámina como repetida porque aún no la tienes en tu álbum.", "warning")
        return redirect(request.referrer or url_for("album"))

    duplicates = user.setdefault("duplicates", {})
    duplicates[code] = int(duplicates.get(code, 0)) + 1
    save_users(data)
    flash("Repetida agregada.", "success")
    return redirect(request.referrer or url_for("repetidas"))


@app.post("/lamina/<code>/repetida/restar")
@login_required
def restar_repetida(code):
    data = load_users()
    user = data["users"][get_current_username()]
    duplicates = user.setdefault("duplicates", {})
    amount = int(duplicates.get(code, 0))

    if amount > 1:
        duplicates[code] = amount - 1
    else:
        duplicates.pop(code, None)

    save_users(data)
    flash("Repetida actualizada.", "success")
    return redirect(request.referrer or url_for("repetidas"))


@app.route("/repetidas")
@app.route("/repetidas/<team_code>")
@login_required
def repetidas(team_code=None):
    album_data = load_album()
    stickers = album_stickers(album_data)
    duplicates = duplicate_map(get_current_user())
    selected_specials = bool(team_code and team_code.upper() == "SPECIALS")
    selected_team = find_team(album_data, team_code) if team_code and not selected_specials else None

    if selected_specials:
        stickers = [sticker for sticker in stickers if sticker.get("team_code") == "SPECIALS"]
    elif team_code:
        stickers = [sticker for sticker in stickers if sticker.get("team_code") == team_code.upper()]

    repeated_stickers = flat_repeated_stickers(stickers, duplicates)
    repeated_total = sum(sticker["duplicates"] for sticker in repeated_stickers)
    return render_template(
        "repetidas.html",
        repeated_stickers=repeated_stickers,
        repeated_total=repeated_total,
        share_owner=get_current_username(),
        selected_team=selected_team,
        selected_specials=selected_specials,
        friend=None,
    )


@app.route("/amigos", methods=["GET", "POST"])
@login_required
def amigos():
    username = get_current_username()
    data = load_users()

    if request.method == "POST":
        friend_username = normalize_username(request.form.get("friend", ""))

        if not friend_username:
            flash("Escribe un nombre de usuario.", "warning")
        elif friend_username not in data["users"]:
            flash("Ese usuario no existe.", "danger")
        elif friend_username == username:
            flash("No puedes enviarte una solicitud a ti mismo.", "warning")
        elif friend_username in data["users"][username].get("friends", []):
            flash("Ese usuario ya está en tu lista de amigos.", "warning")
        elif pending_request_exists(data, username, friend_username):
            flash("Ya existe una solicitud pendiente entre ustedes.", "warning")
        else:
            request_id = int(data.get("next_request_id", 1))
            data["friend_requests"].append({
                "id": request_id,
                "from": username,
                "to": friend_username,
                "status": "pending",
            })
            data["next_request_id"] = request_id + 1
            save_users(data)
            flash("Solicitud de amistad enviada.", "success")

        return redirect(url_for("amigos", buscar=friend_username))

    search = normalize_username(request.args.get("buscar", ""))
    results = []
    if search:
        for candidate in sorted(data["users"]):
            if candidate != username and search in candidate:
                results.append({"username": candidate})
            if len(results) == 12:
                break

    received, sent = request_rows(username)
    return render_template(
        "amigos.html",
        friends=friend_rows(username),
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

    data = load_users()
    username = get_current_username()
    friend_request = None

    for item in data.get("friend_requests", []):
        if item["id"] == request_id and item["to"] == username and item.get("status") == "pending":
            friend_request = item
            break

    if not friend_request:
        flash("Solicitud no encontrada.", "danger")
        return redirect(url_for("amigos"))

    if action == "aceptar":
        friend_request["status"] = "accepted"
        add_friendship(data, friend_request["from"], friend_request["to"])
        flash("Solicitud aceptada.", "success")
    else:
        friend_request["status"] = "rejected"
        flash("Solicitud rechazada.", "success")

    save_users(data)
    return redirect(url_for("amigos"))


@app.route("/amigos/<username>")
@login_required
def amigo_detalle(username):
    friend_username = normalize_username(username)

    if not get_user(friend_username) or not are_friends(get_current_username(), friend_username):
        flash("Primero agrega ese usuario como amigo.", "warning")
        return redirect(url_for("amigos"))

    album_data = load_album()
    stickers = album_stickers(album_data)
    friend_duplicates = duplicate_map(get_user(friend_username))
    repeated_stickers = flat_repeated_stickers(stickers, friend_duplicates)
    repeated_total = sum(sticker["duplicates"] for sticker in repeated_stickers)
    return render_template(
        "repetidas.html",
        repeated_stickers=repeated_stickers,
        repeated_total=repeated_total,
        share_owner=friend_username,
        selected_team=None,
        selected_specials=False,
        friend=friend_username,
    )


@app.route("/comparar/<username>")
@login_required
def comparar(username):
    friend_username = normalize_username(username)

    if not get_user(friend_username) or not are_friends(get_current_username(), friend_username):
        flash("Primero agrega ese usuario como amigo.", "warning")
        return redirect(url_for("amigos"))

    stickers = album_stickers(load_album())
    my_owned = owned_set(get_current_user())
    friend_duplicates = duplicate_map(get_user(friend_username))
    useful = []

    for sticker in stickers:
        amount = friend_duplicates.get(sticker["code"], 0)
        if sticker["code"] not in my_owned and amount > 0:
            item = sticker.copy()
            item["duplicates"] = amount
            item["compact_code"] = compact_sticker_code(sticker["code"], sticker.get("team_code"))
            useful.append(item)

    return render_template("comparar.html", friend=friend_username, useful=useful)


@app.errorhandler(404)
def page_not_found(error):
    flash("La página que buscas no existe. Te llevamos al inicio.", "warning")
    return redirect(url_for("index") if get_current_username() else url_for("login"))


@app.errorhandler(500)
def internal_error(error):
    return (
        "Ocurrió un problema temporal. Intenta de nuevo en unos minutos.",
        500,
        {"Content-Type": "text/plain; charset=utf-8"},
    )


if __name__ == "__main__":
    app.run(debug=os.getenv("FLASK_DEBUG") == "1")
