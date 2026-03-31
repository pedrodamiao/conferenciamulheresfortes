from flask import Flask, render_template, request, redirect, url_for, flash, session, make_response
import sqlite3, json, os, datetime, csv, io, secrets
from contextlib import closing
from werkzeug.security import check_password_hash, generate_password_hash
from functools import wraps
from datetime import datetime as dt, timedelta

# =========================
# Configurações principais
# =========================
APP_SECRET     = os.environ.get("APP_SECRET", "changeme")
ADMIN_USER     = os.environ.get("ADMIN_USER", "admin")
ADMIN_PASS_HASH= os.environ.get("ADMIN_PASS_HASH")
ADMIN_PASS     = os.environ.get("ADMIN_PASS")
DB_PATH        = os.environ.get("DB_PATH", "inscricoes.db")
FALLBACK_DB    = "inscricoes.db"

PER_SLOT_CAPACITY = 40  # vagas por horário/oficina

app = Flask(__name__)
app.secret_key = APP_SECRET

# =========================
# Horários e bloqueios
# =========================
SLOTS = [
    {"id": 1, "hora": "14h00", "bloqueadas": ["FORTALECENDO-SE NO PODER DO ESPÍRITO"]},
    {"id": 2, "hora": "15h50", "bloqueadas": ["TRANSFORMANDO COMPORTAMENTOS DESTRUTIVOS", "VENCENDO AS MENTIRAS COM A VERDADE"]},
    {"id": 3, "hora": "19h00", "bloqueadas": ["CUIDANDO DO CORPO ONDE O ESPÍRITO HABITA", "DA FRAQUEZA À VITÓRIA: TORNANDO-SE FORTE NA PALAVRA"]},
    {"id": 4, "hora": "20h50", "bloqueadas": ["RAÍZES QUE PRECISAM SER ARRANCADAS", "DOMINANDO AS EMOÇÕES PARA QUE O ESPÍRITO SANTO GOVERNE"]},
]

# =========================
# Helpers de banco de dados
# =========================
def _choose_db_path(primary: str, fallback: str) -> str:
    dirpath = os.path.dirname(primary)
    try:
        if dirpath and not os.path.exists(dirpath):
            os.makedirs(dirpath, exist_ok=True)
        return primary
    except Exception:
        return fallback

def get_db():
    path = _choose_db_path(DB_PATH, FALLBACK_DB)
    conn = sqlite3.connect(path, timeout=10, isolation_level=None)
    conn.row_factory = sqlite3.Row
    return conn

# =========================
# Inicialização do BD
# =========================
def init_db():
    with closing(get_db()) as conn:
        cur = conn.cursor()
        cur.executescript("""
        PRAGMA journal_mode=WAL;
        CREATE TABLE IF NOT EXISTS workshops(
            id INTEGER PRIMARY KEY,
            name TEXT NOT NULL,
            capacity INTEGER NOT NULL,
            registered INTEGER NOT NULL DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS attendees(
            id INTEGER PRIMARY KEY,
            full_name TEXT NOT NULL,
            email TEXT NOT NULL UNIQUE,
            selections TEXT NOT NULL,
            selections_map TEXT,
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS workshop_slots(
            workshop_id INTEGER NOT NULL,
            slot_id     INTEGER NOT NULL,
            capacity    INTEGER NOT NULL,
            registered  INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (workshop_id, slot_id)
        );
        """)

        cur.execute("SELECT COUNT(*) c FROM workshops")
        if cur.fetchone()["c"] == 0:
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
                cur.execute("INSERT INTO workshops(name, capacity, registered) VALUES (?, ?, 0)", (n, PER_SLOT_CAPACITY))

        # Cria workshop_slots (por horário)
        ws = cur.execute("SELECT id, name FROM workshops").fetchall()
        id2name = {w["id"]: w["name"] for w in ws}
        existing = set((r["workshop_id"], r["slot_id"]) for r in cur.execute("SELECT workshop_id, slot_id FROM workshop_slots"))
        for slot in SLOTS:
            for wid, wname in id2name.items():
                if wname in slot["bloqueadas"]: 
                    continue
                if (wid, slot["id"]) not in existing:
                    cur.execute(
                        "INSERT INTO workshop_slots(workshop_id, slot_id, capacity, registered) VALUES (?, ?, ?, 0)",
                        (wid, slot["id"], PER_SLOT_CAPACITY)
                    )
        conn.commit()

# =========================
# Bootstrap + autenticação
# =========================
app.config["BOOTSTRAPPED"] = False

@app.before_request
def _bootstrap_once():
    if not app.config["BOOTSTRAPPED"]:
        init_db()
        global ADMIN_PASS_HASH
        if not ADMIN_PASS_HASH and ADMIN_PASS:
            ADMIN_PASS_HASH = generate_password_hash(ADMIN_PASS)
        app.config["BOOTSTRAPPED"] = True

def _get_csrf_token():
    tok = session.get("_csrf_token")
    if not tok:
        tok = secrets.token_hex(16)
        session["_csrf_token"] = tok
    return tok

def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not session.get("admin_logged"):
            return redirect(url_for("login", next=request.path))
        return view(*args, **kwargs)
    return wrapped

# =========================
# Página pública de inscrição
# =========================
@app.route("/")
def index():
    with closing(get_db()) as conn:
        workshops = conn.execute("SELECT * FROM workshops ORDER BY id").fetchall()
        slot_rows = conn.execute("SELECT workshop_id, slot_id, capacity, registered FROM workshop_slots").fetchall()

    avail = {}
    for r in slot_rows:
        sid, wid = int(r["slot_id"]), int(r["workshop_id"])
        cap, reg = int(r["capacity"]), int(r["registered"])
        avail.setdefault(sid, {})[wid] = {"capacity": cap, "registered": reg, "remaining": max(0, cap - reg)}

    return render_template("index.html", workshops=workshops, slots=SLOTS, avail=avail)

@app.route("/inscrever", methods=["POST"])
def inscrever():
    full_name = request.form.get("full_name", "").strip()
    email = request.form.get("email", "").strip().lower()
    consent = request.form.get("consent") == "on"

    selected_per_slot, chosen_ids = {}, []
    for slot in SLOTS:
        val = request.form.get(f"slot_{slot['id']}")
        if val:
            wid = int(val)
            selected_per_slot[slot["id"]] = wid
            chosen_ids.append(wid)

    if not consent:
        flash("Você precisa autorizar o uso dos dados.", "error")
        return redirect(url_for("index"))
    if not full_name or not email or "@" not in email:
        flash("Preencha nome e e-mail válidos.", "error")
        return redirect(url_for("index"))
    if len(chosen_ids) < 1:
        flash("Escolha pelo menos uma oficina.", "error")
        return redirect(url_for("index"))
    if len(chosen_ids) > 4 or len(chosen_ids) != len(set(chosen_ids)):
        flash("Escolha no máximo 4 oficinas e sem repetir.", "error")
        return redirect(url_for("index"))

    conn = get_db()
    try:
        conn.execute("BEGIN IMMEDIATE")
        cur = conn.cursor()
        if cur.execute("SELECT 1 FROM attendees WHERE email=?", (email,)).fetchone():
            conn.execute("ROLLBACK")
            flash("E-mail já inscrito.", "error")
            return redirect(url_for("index"))

        for sid, wid in selected_per_slot.items():
            row = cur.execute("SELECT capacity, registered FROM workshop_slots WHERE workshop_id=? AND slot_id=?", (wid, sid)).fetchone()
            if not row or row["registered"] >= row["capacity"]:
                conn.execute("ROLLBACK")
                flash("Uma das oficinas atingiu o limite.", "error")
                return redirect(url_for("index"))
            cur.execute("UPDATE workshop_slots SET registered = registered + 1 WHERE workshop_id=? AND slot_id=?", (wid, sid))
        for wid in chosen_ids:
            cur.execute("UPDATE workshops SET registered = registered + 1 WHERE id=?", (wid,))

        now_iso = datetime.datetime.utcnow().isoformat()
        cur.execute(
            "INSERT INTO attendees(full_name,email,selections,selections_map,created_at) VALUES(?,?,?,?,?)",
            (full_name, email, json.dumps(chosen_ids), json.dumps(selected_per_slot), now_iso)
        )
        conn.execute("COMMIT")
        return redirect(url_for("sucesso"))
    except Exception:
        conn.execute("ROLLBACK")
        flash("Erro ao salvar inscrição.", "error")
        return redirect(url_for("index"))
    finally:
        conn.close()

@app.route("/sucesso")
def sucesso():
    return render_template("success.html")

# =========================
# Login / Admin
# =========================
@app.route("/login", methods=["GET","POST"])
def login():
    if request.method == "POST":
        user = request.form.get("username","").strip()
        pwd  = request.form.get("password","")
        if user != ADMIN_USER or not ADMIN_PASS_HASH or not check_password_hash(ADMIN_PASS_HASH, pwd):
            flash("Usuário ou senha inválidos.", "error")
        else:
            session["admin_logged"] = True
            return redirect(request.args.get("next") or url_for("admin"))
    return render_template("login.html")

@app.route("/logout")
@login_required
def logout():
    session.clear()
    flash("Sessão encerrada.", "message")
    return redirect(url_for("login"))

@app.route("/admin")
@login_required
def admin():
    with closing(get_db()) as conn:
        cap_rows = conn.execute("""
            SELECT w.id AS wid, w.name AS name, COALESCE(SUM(ws.capacity),0) AS cap_total
            FROM workshops w
            LEFT JOIN workshop_slots ws ON ws.workshop_id = w.id
            GROUP BY w.id, w.name ORDER BY w.id
        """).fetchall()
        at_rows = conn.execute("SELECT id, full_name, email, selections, selections_map, created_at FROM attendees ORDER BY created_at DESC").fetchall()

    cap_by_wid  = {int(r["wid"]): int(r["cap_total"]) for r in cap_rows}
    name_by_wid = {int(r["wid"]): r["name"] for r in cap_rows}

    reg_by_wid = {wid: 0 for wid in cap_by_wid.keys()}
    for a in at_rows:
        try:
            for wid in json.loads(a["selections"]) or []:
                reg_by_wid[int(wid)] = reg_by_wid.get(int(wid), 0) + 1
        except Exception:
            pass

    workshops_view = [{
        "id": wid,
        "name": name_by_wid[wid],
        "capacity_total": cap_by_wid[wid],
        "registered_total": reg_by_wid[wid],
        "remaining_total": max(0, cap_by_wid[wid] - reg_by_wid[wid])
    } for wid in sorted(cap_by_wid)]

    parsed_attendees = []
    for a in at_rows:
        try:
            utc_dt = dt.fromisoformat(a["created_at"])
            created_local = (utc_dt - timedelta(hours=3)).strftime("%d/%m/%Y %H:%M")
        except Exception:
            created_local = a["created_at"]
        parsed_attendees.append({
            "id": a["id"],
            "full_name": a["full_name"],
            "email": a["email"],
            "selections": json.loads(a["selections"]) if a["selections"] else [],
            "created_at_local": created_local
        })

    return render_template("admin.html", workshops=workshops_view, attendees=parsed_attendees, csrf_token=_get_csrf_token())

# =========================
# Excluir inscrição individual
# =========================
@app.post("/admin/attendee/<int:att_id>/delete")
@login_required
def delete_attendee(att_id):
    token = request.form.get("_csrf_token") or ""
    if token != session.get("_csrf_token"):
        flash("CSRF inválido. Recarregue a página.", "error")
        return redirect(url_for("admin"))

    conn = get_db()
    try:
        conn.execute("BEGIN IMMEDIATE")
        cur = conn.cursor()
        row = cur.execute("SELECT selections, selections_map FROM attendees WHERE id=?", (att_id,)).fetchone()
        if not row:
            conn.execute("ROLLBACK")
            flash("Inscrição não encontrada.", "error")
            return redirect(url_for("admin"))

        sel_list = json.loads(row["selections"]) if row["selections"] else []
        sel_map  = json.loads(row["selections_map"]) if row["selections_map"] else {}

        for sid_raw, wid_raw in sel_map.items():
            try:
                sid, wid = int(sid_raw), int(wid_raw)
                cur.execute("""
                    UPDATE workshop_slots
                    SET registered = CASE WHEN registered > 0 THEN registered - 1 ELSE 0 END
                    WHERE workshop_id=? AND slot_id=?
                """, (wid, sid))
            except Exception:
                pass
        for wid_raw in set(sel_list):
            try:
                wid = int(wid_raw)
                cur.execute("""
                    UPDATE workshops
                    SET registered = CASE WHEN registered > 0 THEN registered - 1 ELSE 0 END
                    WHERE id=?
                """, (wid,))
            except Exception:
                pass

        cur.execute("DELETE FROM attendees WHERE id=?", (att_id,))
        conn.execute("COMMIT")
        flash("Inscrição excluída com sucesso.", "message")
    except Exception:
        conn.execute("ROLLBACK")
        flash("Erro ao excluir inscrição.", "error")
    finally:
        conn.close()

    return redirect(url_for("admin"))

# =========================
# Reset base
# =========================
@app.post("/admin/reset")
@login_required
def admin_reset():
    tok = request.form.get("_csrf_token") or ""
    if tok != session.get("_csrf_token"):
        flash("Falha CSRF. Recarregue a página.", "error")
        return redirect(url_for("admin"))
    with closing(get_db()) as conn:
        cur = conn.cursor()
        cur.execute("DELETE FROM attendees")
        cur.execute("UPDATE workshops SET registered=0")
        cur.execute("UPDATE workshop_slots SET registered=0")
        conn.commit()
    flash("Base resetada com sucesso.", "message")
    return redirect(url_for("admin"))

# =========================
# Relatórios e exports
# =========================
def _workshop_map(conn):
    return {r["id"]: r["name"] for r in conn.execute("SELECT id, name FROM workshops")}

def build_reports():
    with closing(get_db()) as conn:
        id2name = _workshop_map(conn)
        attendees = conn.execute("SELECT selections,selections_map FROM attendees").fetchall()
    by_workshop = {wid: {"name": name, "count": 0} for wid, name in id2name.items()}
    by_slot = {s["id"]: {"hora": s["hora"], "items": {wid: {"name": n, "count": 0} for wid, n in id2name.items()}} for s in SLOTS}

    for a in attendees:
        try:
            for wid in json.loads(a["selections"]) or []:
                by_workshop[int(wid)]["count"] += 1
        except Exception:
            pass
        try:
            sel_map = json.loads(a["selections_map"]) if a["selections_map"] else {}
            for sid_raw, wid_raw in sel_map.items():
                sid, wid = int(sid_raw), int(wid_raw)
                by_slot[sid]["items"][wid]["count"] += 1
        except Exception:
            pass
    return by_workshop, by_slot

@app.route("/reports")
@login_required
def reports():
    by_workshop, by_slot = build_reports()
    ws_sorted = sorted(by_workshop.items(), key=lambda kv: kv[1]["name"])
    slots_view = []
    for s in SLOTS:
        sid = s["id"]
        items = by_slot[sid]["items"]
        rows = sorted(items.items(), key=lambda kv: kv[1]["name"])
        slots_view.append({"id": sid, "hora": s["hora"], "rows": rows})
    return render_template("reports.html", ws=ws_sorted, slots=slots_view)

@app.route("/export_names_rows_by_workshop_slot.csv")
@login_required
def export_names_rows_by_workshop_slot_csv():
    with closing(get_db()) as conn:
        ws = conn.execute("SELECT id,name FROM workshops ORDER BY name").fetchall()
        attendees = conn.execute("SELECT full_name,email,selections,selections_map FROM attendees ORDER BY full_name").fetchall()
    id2name = {r["id"]: r["name"] for r in ws}
    slot_hour = {s["id"]: s["hora"] for s in SLOTS}
    rows = []
    for a in attendees:
        sel_list = json.loads(a["selections"]) if a["selections"] else []
        sel_map = json.loads(a["selections_map"]) if a["selections_map"] else {}
        wid_to_slot = {int(v): int(k) for k, v in sel_map.items()} if isinstance(sel_map, dict) else {}
        for wid in sel_list:
            wname = id2name.get(wid, f"ID {wid}")
            sid = wid_to_slot.get(wid)
            hora = slot_hour.get(sid, "")
            rows.append((wname, hora, a["full_name"], a["email"]))
    rows.sort(key=lambda r: (r[0].lower(), r[1], r[2].lower()))
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["Oficina", "Horário", "Nome", "E-mail"])
    for