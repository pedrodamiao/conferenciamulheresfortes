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
                    (n, 10)  # capacidade por horário
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

    conn.close()
    return render_template("index.html", slots=slots, workshops=workshops)


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

    if len(selections) < 1:
        flash("Escolha pelo menos uma oficina.", "error")
        return redirect(url_for("index"))

    if len(set(selections.values())) != len(selections.values()):
        flash("Não repita oficinas.", "error")
        return redirect(url_for("index"))

    conn = get_db()
    cur = conn.cursor()

    cur.execute("SELECT id FROM attendees WHERE email=?", (email,))
    if cur.fetchone():
        conn.close()
        flash("E-mail já cadastrado.", "error")
        return redirect(url_for("index"))

    # 🔥 CONTAGEM POR SLOT
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

    # 🔥 VALIDAÇÃO POR HORÁRIO
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

    # montar tela sucesso
    selected_data = []
    for slot, wid in selections.items():
        cur.execute("SELECT name FROM workshops WHERE id=?", (wid,))
        name = cur.fetchone()["name"]

        selected_data.append({
            "horario": slots_map[slot],
            "oficina": name
        })

    conn.close()

    selected_data = sorted(selected_data, key=lambda x: x["horario"])

    session["last_registration"] = {
        "nome": full_name,
        "email": email,
        "selecoes": selected_data
    }

    return redirect(url_for("sucesso"))


@app.route("/sucesso")
def sucesso():
    data = session.get("last_registration")
    if not data:
        return redirect(url_for("index"))
    return render_template("sucesso.html", data=data)


# ================== LOGIN ==================
@app.route("/login", methods=["GET", "POST"])
def login():
    error = None

    if request.method == "POST":
        username = request.form.get("username")
        password = request.form.get("password")

        if not ADMIN_PASS_HASH:
            error = "Senha do admin não configurada no servidor."
        
        elif username == ADMIN_USER and check_password_hash(ADMIN_PASS_HASH, password):
            session["admin_logged"] = True
            return redirect(url_for("admin"))
        
        else:
            error = "Usuário ou senha incorretos."

    return render_template("login.html", error=error)


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


# ================== ADMIN ==================
@app.route("/admin")
@login_required
def admin():
    conn = get_db()
    cur = conn.cursor()

    cur.execute("SELECT id, name, capacity FROM workshops")
    workshops_raw = cur.fetchall()

    cur.execute("SELECT selections FROM attendees")
    all_sel = [json.loads(r["selections"]) for r in cur.fetchall()]

    count_map = {}
    for s in all_sel:
        if isinstance(s, list):
            s = {str(i+1): v for i, v in enumerate(s)}

        for slot, wid in s.items():
            key = (wid, slot)
            count_map[key] = count_map.get(key, 0) + 1

    workshops = []
    for w in workshops_raw:
        wid = w["id"]
        cap = w["capacity"]

        reg = sum(
            count_map.get((wid, str(slot)), 0)
            for slot in range(1, 5)
        )

        workshops.append({
            "id": wid,
            "name": w["name"],
            "capacity_total": cap,
            "registered_total": reg,
            "remaining_total": max((cap * 4) - reg, 0)
        })

    cur.execute("SELECT * FROM attendees ORDER BY created_at DESC")

    attendees = []
    for row in cur.fetchall():
        dt = datetime.datetime.fromisoformat(row["created_at"]) - datetime.timedelta(hours=3)

        attendees.append({
            "id": row["id"],
            "full_name": row["full_name"],
            "email": row["email"],
            "created_at_local": dt.strftime("%d/%m/%Y %H:%M")
        })

    conn.close()

    return render_template("admin.html",
        workshops=workshops,
        attendees=attendees,
        total_attendees=len(attendees)
    )


# ================== UPDATE CAPACITY ==================
@app.route("/admin/update_capacity/<int:workshop_id>", methods=["POST"])
@login_required
def update_capacity(workshop_id):
    try:
        new_capacity = int(request.form.get("capacity"))

        if new_capacity <= 0:
            flash("A capacidade deve ser maior que zero.", "error")
            return redirect(url_for("admin"))

        conn = get_db()
        cur = conn.cursor()

        cur.execute(
            "UPDATE workshops SET capacity = ? WHERE id = ?",
            (new_capacity, workshop_id)
        )

        conn.commit()
        conn.close()

        flash("Capacidade atualizada com sucesso.", "message")

    except Exception:
        flash("Erro ao atualizar capacidade.", "error")

    return redirect(url_for("admin"))


# ================== REPORTS ==================
@app.route("/reports")
@login_required
def reports():
    conn = get_db()
    cur = conn.cursor()

    cur.execute("SELECT * FROM attendees ORDER BY created_at DESC")
    rows = cur.fetchall()

    cur.execute("SELECT id, name FROM workshops")
    workshops_map = {row["id"]: row["name"] for row in cur.fetchall()}

    people = []

    for r in rows:
        sel = json.loads(r["selections"])

        if isinstance(sel, list):
            sel = {str(i+1): v for i, v in enumerate(sel)}

        data = {
            "full_name": r["full_name"],
            "email": r["email"]
        }

        for i in range(1, 5):
            wid = sel.get(str(i))
            data[f"slot_{i}"] = workshops_map.get(wid, "") if wid else ""

        people.append(data)

    slots = [
        {"id": 1, "hora": "14h"},
        {"id": 2, "hora": "15:50h"},
        {"id": 3, "hora": "19h"},
        {"id": 4, "hora": "20:50h"},
    ]

    conn.close()
    return render_template("reports.html", people=people, slots=slots)


# ================== DELETE ==================
@app.route("/delete/<int:att_id>", methods=["POST"])
@login_required
def delete_attendee(att_id):
    conn = get_db()
    cur = conn.cursor()
    cur.execute("DELETE FROM attendees WHERE id=?", (att_id,))
    conn.commit()
    conn.close()
    flash("Inscrição excluída.", "message")
    return redirect(url_for("admin"))


# ================== RESET ==================
@app.route("/admin/reset", methods=["POST"])
@login_required
def reset():
    conn = get_db()
    cur = conn.cursor()
    cur.execute("DELETE FROM attendees")
    conn.commit()
    conn.close()
    flash("Base resetada.", "message")
    return redirect(url_for("admin"))


if __name__ == "__main__":
    app.run(debug=True)
