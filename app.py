from flask import Flask, render_template, request, redirect, url_for, flash, session
import sqlite3, json, os, datetime
from contextlib import closing
from werkzeug.security import check_password_hash, generate_password_hash
from functools import wraps

APP_SECRET = os.environ.get("APP_SECRET", "changeme")
ADMIN_USER = os.environ.get("ADMIN_USER", "admin")
ADMIN_PASS_HASH = os.environ.get("ADMIN_PASS_HASH")

app = Flask(__name__)
app.secret_key = APP_SECRET
DB_PATH = os.environ.get("DB_PATH", "/data/inscricoes.db")


# ================== DB ==================
def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    with closing(get_db()) as conn:
        cur = conn.cursor()

        cur.executescript("""
        CREATE TABLE IF NOT EXISTS workshops(
            id INTEGER PRIMARY KEY,
            name TEXT NOT NULL,
            capacity INTEGER NOT NULL
        );

        CREATE TABLE IF NOT EXISTS attendees(
            id INTEGER PRIMARY KEY,
            full_name TEXT NOT NULL,
            email TEXT NOT NULL UNIQUE,
            selections TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        """)

        cur.execute("SELECT COUNT(*) FROM workshops")
        if cur.fetchone()[0] == 0:
            names = [
                "REGULANDO AS EMOÇÕES",
                "RAÍZES QUE PRECISAM SER ARRANCADAS",
                "VENCENDO A MENTIRA COM A VERDADE",
                "COMO SE FORTALECER ESPIRITUALMENTE",
                "COMO VENCER A AUTOSSABOTAGEM",
                "COMO CUIDAR DO CORPO",
                "FORTALECIDA NA PALAVRA",
            ]

            for n in names:
                cur.execute(
                    "INSERT INTO workshops(name, capacity) VALUES (?, ?)",
                    (n, 10)
                )

        conn.commit()


with app.app_context():
    init_db()


# ================== AUTH ==================
def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not session.get("admin_logged"):
            return redirect(url_for("login"))
        return view(*args, **kwargs)
    return wrapped


# ================== INDEX ==================
@app.route("/")
def index():
    conn = get_db()
    cur = conn.cursor()

    slots = [
        {"id": 1, "hora": "14h", "bloqueadas": ["COMO SE FORTALECER ESPIRITUALMENTE", "FORTALECIDA NA PALAVRA"]},
        {"id": 2, "hora": "15:50h", "bloqueadas": ["VENCENDO A MENTIRA COM A VERDADE", "COMO CUIDAR DO CORPO"]},
        {"id": 3, "hora": "19h", "bloqueadas": ["RAÍZES QUE PRECISAM SER ARRANCADAS"]},
        {"id": 4, "hora": "20:50h", "bloqueadas": ["REGULANDO AS EMOÇÕES", "COMO VENCER A AUTOSSABOTAGEM"]},
    ]

    cur.execute("SELECT * FROM workshops")
    workshops = [dict(row) for row in cur.fetchall()]

    # CONTAGEM POR SLOT
    cur.execute("SELECT selections FROM attendees")
    all_sel = [json.loads(r["selections"]) for r in cur.fetchall()]

    count_map = {}
    for s in all_sel:
        if isinstance(s, list):
            s = {str(i+1): v for i, v in enumerate(s)}

        for slot, wid in s.items():
            key = (wid, slot)
            count_map[key] = count_map.get(key, 0) + 1

    # MAPA DE LOTAÇÃO
    lotadas = set()

    for w in workshops:
        wid = w["id"]
        cap = w["capacity"]

        for slot in range(1, 5):
            if count_map.get((wid, str(slot)), 0) >= cap:
                lotadas.add((wid, str(slot)))

    conn.close()

    return render_template(
        "index.html",
        slots=slots,
        workshops=workshops,
        lotadas=lotadas
    )


# ================== INSCRIÇÃO ==================
@app.route("/", methods=["POST"])
def inscrever():
    full_name = request.form.get("full_name")
    email = request.form.get("email")
    consent = request.form.get("consent")

    if not full_name or not email or not consent:
        flash("Preencha todos os campos.", "error")
        return redirect(url_for("index"))

    selections = {}
    for i in range(1, 5):
        val = request.form.get(f"slot_{i}")
        if val:
            selections[str(i)] = int(val)

    if len(set(selections.values())) != len(selections.values()):
        flash("Não repita oficinas.", "error")
        return redirect(url_for("index"))

    conn = get_db()
    cur = conn.cursor()

    cur.execute("SELECT selections FROM attendees")
    all_sel = [json.loads(r["selections"]) for r in cur.fetchall()]

    count_map = {}
    for s in all_sel:
        if isinstance(s, list):
            s = {str(i+1): v for i, v in enumerate(s)}

        for slot, wid in s.items():
            key = (wid, slot)
            count_map[key] = count_map.get(key, 0) + 1

    slots_map = {
        "1": "14h",
        "2": "15:50h",
        "3": "19h",
        "4": "20:50h"
    }

    for slot, wid in selections.items():
        current = count_map.get((wid, slot), 0)

        cur.execute("SELECT capacity FROM workshops WHERE id=?", (wid,))
        cap = cur.fetchone()["capacity"]

        if current >= cap:
            conn.close()
            flash(f"A oficina já lotou no horário {slots_map[slot]}.", "error")
            return redirect(url_for("index"))

    cur.execute("""
        INSERT INTO attendees(full_name, email, selections, created_at)
        VALUES (?, ?, ?, ?)
    """, (
        full_name,
        email,
        json.dumps(selections),
        datetime.datetime.utcnow().isoformat()
    ))

    conn.commit()
    conn.close()

    return redirect(url_for("sucesso"))


@app.route("/sucesso")
def sucesso():
    data = session.get("last_registration")
    return render_template("sucesso.html", data=data)


# ================== LOGIN ==================
@app.route("/login", methods=["GET", "POST"])
def login():
    error = None

    if request.method == "POST":
        username = request.form.get("username")
        password = request.form.get("password")

        if username == ADMIN_USER and check_password_hash(ADMIN_PASS_HASH, password):
            session["admin_logged"] = True
            return redirect(url_for("admin"))
        else:
            error = "Usuário ou senha incorretos."

    return render_template("login.html", error=error)


# ================== ADMIN ==================
@app.route("/admin")
@login_required
def admin():
    conn = get_db()
    cur = conn.cursor()

    cur.execute("SELECT * FROM workshops")
    workshops = [dict(row) for row in cur.fetchall()]

    cur.execute("SELECT * FROM attendees ORDER BY created_at DESC")
    attendees = [dict(row) for row in cur.fetchall()]

    conn.close()

    return render_template("admin.html", workshops=workshops, attendees=attendees)


if __name__ == "__main__":
    app.run(debug=True)
