from flask import Flask, render_template, request, redirect, url_for, flash, session
import sqlite3, json, os, datetime
from contextlib import closing
from werkzeug.security import check_password_hash, generate_password_hash
from functools import wraps

APP_SECRET = os.environ.get("APP_SECRET", "changeme")
ADMIN_USER = os.environ.get("ADMIN_USER", "admin")
ADMIN_PASS_HASH = os.environ.get("ADMIN_PASS_HASH")
ADMIN_PASS = os.environ.get("ADMIN_PASS")

app = Flask(__name__)
app.secret_key = APP_SECRET
DB_PATH = os.environ.get("DB_PATH", "inscricoes.db")


# ================== DB ==================
def get_db():
    conn = sqlite3.connect(DB_PATH, timeout=10, isolation_level=None)
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
                "TRANSFORMANDO COMPORTAMENTOS DESTRUTIVOS",
                "RAÍZES QUE PRECISAM SER ARRANCADAS",
                "VENCENDO AS MENTIRAS COM A VERDADE",
                "CUIDANDO DO CORPO ONDE O ESPÍRITO HABITA",
                "DOMINANDO AS EMOÇÕES PARA QUE O ESPÍRITO SANTO GOVERNE",
                "DA FRAQUEZA À VITÓRIA: TORNANDO-SE FORTE NA PALAVRA",
                "FORTALECENDO-SE NO PODER DO ESPÍRITO",
            ]

            for n in names:
                cur.execute(
                    "INSERT INTO workshops(name, capacity) VALUES (?, ?)",
                    (n, 30)
                )

        conn.commit()


# ================== AUTH ==================
def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not session.get("admin_logged"):
            return redirect(url_for("login"))
        return view(*args, **kwargs)
    return wrapped


@app.before_request
def bootstrap():
    os.makedirs(os.path.dirname(DB_PATH) or ".", exist_ok=True)
    init_db()

    global ADMIN_PASS_HASH
    if not ADMIN_PASS_HASH and ADMIN_PASS:
        ADMIN_PASS_HASH = generate_password_hash(ADMIN_PASS)


# ================== ROTAS ==================
@app.route("/")
def index():
    conn = get_db()
    cur = conn.cursor()

    slots = [
        {"id": 1, "hora": "14h", "bloqueadas": ["FORTALECENDO-SE NO PODER DO ESPÍRITO"]},
        {"id": 2, "hora": "15:50h", "bloqueadas": ["TRANSFORMANDO COMPORTAMENTOS DESTRUTIVOS", "VENCENDO AS MENTIRAS COM A VERDADE"]},
        {"id": 3, "hora": "19h", "bloqueadas": ["CUIDANDO DO CORPO ONDE O ESPÍRITO HABITA", "DA FRAQUEZA À VITÓRIA: TORNANDO-SE FORTE NA PALAVRA"]},
        {"id": 4, "hora": "20:50h", "bloqueadas": ["RAÍZES QUE PRECISAM SER ARRANCADAS", "DOMINANDO AS EMOÇÕES PARA QUE O ESPÍRITO SANTO GOVERNE"]},
    ]

    cur.execute("SELECT * FROM workshops")
    workshops = [dict(row) for row in cur.fetchall()]

    conn.close()
    return render_template("index.html", slots=slots, workshops=workshops)


@app.route("/", methods=["POST"])
def inscrever():
    full_name = request.form.get("full_name")
    email = request.form.get("email")
    consent = request.form.get("consent")

    if not full_name or not email or not consent:
        flash(("error", "Preencha todos os campos."))
        return redirect(url_for("index"))

    selections = []
    for i in range(1, 5):
        val = request.form.get(f"slot_{i}")
        if val:
            selections.append(int(val))

    if len(selections) < 1:
        flash(("error", "Escolha pelo menos uma oficina."))
        return redirect(url_for("index"))

    if len(selections) != len(set(selections)):
        flash(("error", "Não repita oficinas."))
        return redirect(url_for("index"))

    conn = get_db()
    cur = conn.cursor()

    cur.execute("SELECT id FROM attendees WHERE email=?", (email,))
    if cur.fetchone():
        conn.close()
        flash(("error", "E-mail já cadastrado."))
        return redirect(url_for("index"))

    # valida vagas por horário (30 por oficina)
    for wid in selections:
        cur.execute("""
            SELECT COUNT(*) FROM attendees
            WHERE instr(selections, ?) > 0
        """, (str(wid),))
        count = cur.fetchone()[0]

        cur.execute("SELECT capacity FROM workshops WHERE id=?", (wid,))
        cap = cur.fetchone()[0]

        if count >= cap:
            conn.close()
            flash(("error", "Uma oficina já lotou."))
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

    flash(("message", "Inscrição realizada com sucesso!"))
    return redirect(url_for("index"))


@app.route("/login", methods=["GET", "POST"])
def login():
    error = None
    if request.method == "POST":
        if request.form["username"] == ADMIN_USER and check_password_hash(ADMIN_PASS_HASH, request.form["password"]):
            session["admin_logged"] = True
            return redirect(url_for("admin"))
        error = "Login inválido"
    return render_template("login.html", error=error)


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


@app.route("/admin")
@login_required
def admin():
    conn = get_db()
    cur = conn.cursor()

    cur.execute("SELECT * FROM workshops")
    workshops = [dict(row) for row in cur.fetchall()]

    cur.execute("SELECT * FROM attendees ORDER BY created_at DESC")
    attendees = []

    for row in cur.fetchall():
        dt = datetime.datetime.fromisoformat(row["created_at"]) - datetime.timedelta(hours=3)

        attendees.append({
            "id": row["id"],
            "full_name": row["full_name"],
            "email": row["email"],
            "selections": json.loads(row["selections"]),
            "created_at_local": dt.strftime("%d/%m/%Y %H:%M")
        })

    total_attendees = len(attendees)

    conn.close()
    return render_template("admin.html", workshops=workshops, attendees=attendees, total_attendees=total_attendees)


@app.route("/delete/<int:att_id>", methods=["POST"])
@login_required
def delete_attendee(att_id):
    conn = get_db()
    cur = conn.cursor()
    cur.execute("DELETE FROM attendees WHERE id=?", (att_id,))
    conn.commit()
    conn.close()
    flash(("message", "Inscrição excluída."))
    return redirect(url_for("admin"))


@app.route("/admin/reset", methods=["POST"])
@login_required
def reset():
    conn = get_db()
    cur = conn.cursor()
    cur.execute("DELETE FROM attendees")
    conn.commit()
    conn.close()
    flash(("message", "Base resetada."))
    return redirect(url_for("admin"))


@app.route("/reports")
@login_required
def reports():
    conn = get_db()
    cur = conn.cursor()

    cur.execute("SELECT * FROM attendees")
    rows = cur.fetchall()

    people = []
    for r in rows:
        sel = json.loads(r["selections"])

        entry = {
            "full_name": r["full_name"],
            "email": r["email"],
        }

        for i in range(1, 5):
            entry[f"slot_{i}"] = sel[i-1] if i <= len(sel) else ""

        people.append(entry)

    slots = [
        {"id": 1, "hora": "14h"},
        {"id": 2, "hora": "15:50h"},
        {"id": 3, "hora": "19h"},
        {"id": 4, "hora": "20:50h"},
    ]

    conn.close()
    return render_template("reports.html", people=people, slots=slots)


if __name__ == "__main__":
    app.run(debug=True)
