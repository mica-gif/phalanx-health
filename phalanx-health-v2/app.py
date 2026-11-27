"""Phalanx Health - clinic queue system (CpE 411 Data Structures and Algorithms)."""
import csv
import heapq
import io
import os
import random
import re
import sqlite3
import time
from collections import deque
from datetime import datetime, timedelta
from functools import wraps

from flask import (Flask, Response, flash, g, jsonify, redirect,
                   render_template, request, session, url_for)
from werkzeug.security import check_password_hash, generate_password_hash

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "dev-secret-change-me")
DB_PATH = os.environ.get("DB_PATH", os.path.join(os.path.dirname(__file__), "phalanx.db"))
AVG_MIN_PER_PATIENT = 5
HIDE_DAYS = 30

EMERGENCY_WORDS = ["chest pain", "difficulty breathing", "shortness of breath", "can't breathe",
                   "cannot breathe", "unconscious", "fainted", "seizure", "stroke", "heart attack",
                   "severe bleeding", "heavy bleeding", "overdose", "choking"]

SCHEMA = """
CREATE TABLE IF NOT EXISTS users(id INTEGER PRIMARY KEY, code TEXT UNIQUE, name TEXT, age INTEGER,
  gender TEXT, role TEXT, pw TEXT, blood TEXT DEFAULT '', allergies TEXT DEFAULT '',
  contact TEXT DEFAULT '', sms INTEGER DEFAULT 1, email INTEGER DEFAULT 1, deleted_at TEXT);
CREATE TABLE IF NOT EXISTS tickets(id INTEGER PRIMARY KEY, kind TEXT, user_id INTEGER, complaint TEXT,
  status TEXT DEFAULT 'waiting', created TEXT, called_at TEXT, removed INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS visits(id INTEGER PRIMARY KEY, user_id INTEGER, date TEXT, complaint TEXT,
  temp TEXT, bp TEXT, pulse TEXT, spo2 TEXT, diagnosis TEXT, notes TEXT, rx TEXT, followup TEXT);
CREATE TABLE IF NOT EXISTS appts(id INTEGER PRIMARY KEY, user_id INTEGER, date TEXT, slot TEXT, reason TEXT);
CREATE TABLE IF NOT EXISTS meds(id INTEGER PRIMARY KEY, name TEXT UNIQUE, qty INTEGER, low INTEGER);
CREATE TABLE IF NOT EXISTS log(id INTEGER PRIMARY KEY, ts TEXT, who TEXT, action TEXT,
  undo_type TEXT, undo_id INTEGER, undone INTEGER DEFAULT 0);
"""


# ---------------------------------------------------------------- database
def now():
    return datetime.utcnow() + timedelta(hours=8)  # Philippine time


def ts():
    return now().strftime("%Y-%m-%d %H:%M:%S")


def db():
    if "db" not in g:
        g.db = sqlite3.connect(DB_PATH)
        g.db.row_factory = sqlite3.Row
    return g.db


@app.teardown_appcontext
def close_db(_):
    d = g.pop("db", None)
    if d:
        d.close()


def init_db():
    d = sqlite3.connect(DB_PATH)
    d.executescript(SCHEMA)
    if not d.execute("SELECT 1 FROM users LIMIT 1").fetchone():
        users = [("ADMIN", "Administrator", 35, "Female", "admin", "admin123"),
                 ("DOC-001", "Dr. A. Castillo, MD", 42, "Male", "doctor", "doctor123"),
                 ("NURSE-01", "Nurse L. Reyes", 30, "Female", "nurse", "nurse123"),
                 ("24-09303", "Juan P. Dela Cruz", 20, "Male", "student", "student123")]
        for c, n, a, gd, r, p in users:
            d.execute("INSERT INTO users(code,name,age,gender,role,pw) VALUES(?,?,?,?,?,?)",
                      (c, n, a, gd, r, generate_password_hash(p)))
        for n, q, lo in [("Paracetamol", 12, 20), ("Cetirizine", 80, 20),
                         ("Oral rehydration salts", 60, 20), ("Bandages", 8, 15)]:
            d.execute("INSERT INTO meds(name,qty,low) VALUES(?,?,?)", (n, q, lo))
        uid = d.execute("SELECT id FROM users WHERE code='24-09303'").fetchone()[0]
        d.execute("INSERT INTO visits(user_id,date,complaint,rx,followup) VALUES(?,?,?,?,?)",
                  (uid, "2026-10-12", "Cough and cold", "Cetirizine", "done"))
        d.execute("INSERT INTO visits(user_id,date,complaint,rx,followup) VALUES(?,?,?,?,?)",
                  (uid, "2026-09-03", "Headache", "Paracetamol", ""))
    d.commit()
    d.close()


init_db()


def log(who, action, undo_type=None, undo_id=None):
    db().execute("INSERT INTO log(ts,who,action,undo_type,undo_id) VALUES(?,?,?,?,?)",
                 (ts(), who, action, undo_type, undo_id))


# ------------------------------------------------------------------- auth
def current_user():
    if "u" not in g:
        g.u = None
        if session.get("uid"):
            g.u = db().execute("SELECT * FROM users WHERE id=? AND deleted_at IS NULL",
                               (session["uid"],)).fetchone()
    return g.u


def login_required(roles=None):
    def deco(f):
        @wraps(f)
        def wrapper(*a, **k):
            u = current_user()
            if not u:
                return redirect(url_for("login"))
            if roles and u["role"] not in roles:
                flash("You do not have access to that page.", "err")
                return redirect(url_for("home"))
            return f(*a, **k)
        return wrapper
    return deco


STAFF = ("doctor", "nurse", "admin")
DOCTOR_UP = ("doctor", "admin")


def who(u):
    return u["name"].split(",")[0] if u["role"] != "admin" else "ADMIN"


@app.template_filter("tno")
def tno(t):
    return f"{'E' if t['kind'] == 'emergency' else 'R'}-{t['id']:03d}"


@app.template_filter("nicedate")
def nicedate(s):
    try:
        return datetime.strptime(s[:10], "%Y-%m-%d").strftime("%b %d, %Y")
    except Exception:
        return s or ""


@app.context_processor
def inject():
    u = current_user()
    greeting = ""
    if u:
        if u["role"] == "doctor":
            greeting = "Dr. " + u["name"].split(",")[0].split()[-1]
        else:
            greeting = u["name"].split()[0]
    return dict(user=u, greeting=greeting)


@app.route("/")
def home():
    u = current_user()
    if not u:
        return redirect(url_for("login"))
    return redirect(url_for("checkin" if u["role"] == "student" else "dashboard"))


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        code = request.form.get("code", "").strip()
        u = db().execute("SELECT * FROM users WHERE upper(code)=upper(?) AND deleted_at IS NULL",
                         (code,)).fetchone()
        if u and check_password_hash(u["pw"], request.form.get("password", "")):
            session["uid"] = u["id"]
            return redirect(url_for("home"))
        flash("Wrong SR-Code, Staff ID or password. Check them and try again.", "err")
    return render_template("auth.html")


@app.route("/register", methods=["POST"])
def register():
    f = request.form
    code, name, pw = f.get("code", "").strip(), f.get("name", "").strip(), f.get("password", "")
    if not re.fullmatch(r"\d{2}-\d{5}", code):
        flash("SR-Code must look like 24-12345.", "err")
    elif not name or len(pw) < 6:
        flash("Enter your full name and a password of at least 6 characters.", "err")
    elif not f.get("agree"):
        flash("Please agree to the Data Privacy notice.", "err")
    elif db().execute("SELECT 1 FROM users WHERE code=?", (code,)).fetchone():
        flash("That SR-Code is already registered.", "err")
    else:
        try:
            age = int(f.get("age") or 0)
        except ValueError:
            age = 0
        cur = db().execute("INSERT INTO users(code,name,age,gender,role,pw) VALUES(?,?,?,?,?,?)",
                           (code, name, age, f.get("gender", "Male"), "student", generate_password_hash(pw)))
        db().commit()
        session["uid"] = cur.lastrowid
        return redirect(url_for("home"))
    return redirect(url_for("login"))


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


# ------------------------------------------------------------------ queue
def waiting_order():
    """Priority queue (binary heap): emergencies first, then first come first served."""
    rows = db().execute("SELECT t.*, u.name FROM tickets t JOIN users u ON u.id=t.user_id "
                        "WHERE t.status='waiting' AND t.removed=0").fetchall()
    heap = [(0 if r["kind"] == "emergency" else 1, r["created"], r["id"], r) for r in rows]
    heapq.heapify(heap)
    out = []
    while heap:
        out.append(heapq.heappop(heap)[3])
    return out


def active_ticket(uid):
    return db().execute("SELECT * FROM tickets WHERE user_id=? AND removed=0 AND status IN "
                        "('waiting','called','hold') ORDER BY id DESC LIMIT 1", (uid,)).fetchone()


def suggest(text):
    t = (text or "").lower()
    return next((k for k in EMERGENCY_WORDS if k in t), None)


def board_data(uid=None):
    order = waiting_order()
    em = [t for t in order if t["kind"] == "emergency"]
    ro = [t for t in order if t["kind"] == "routine"]
    d = db()

    def serving(kind):
        return d.execute("SELECT * FROM tickets WHERE kind=? AND status='called' AND removed=0 "
                         "ORDER BY called_at DESC, id DESC LIMIT 1", (kind,)).fetchone()
    se = serving("emergency")
    e_line = f"{tno(se)} · Now serving" if se else ("No emergency being served" if not em
                                                    else f"{tno(em[0])} · Next")
    mine = active_ticket(uid) if uid else None
    parts = []
    if ro:
        parts.append(f"{tno(ro[0])} next")
    if mine and mine["kind"] == "routine":
        parts.append(f"{tno(mine)} is you")
    r_line = " · ".join(parts) if parts else "No one waiting"
    return dict(e_count=len(em), r_count=len(ro), e_line=e_line, r_line=r_line)


def my_status(uid):
    t = active_ticket(uid)
    if not t:
        return None
    order = waiting_order()
    ids = [x["id"] for x in order]
    ahead = ids.index(t["id"]) if t["id"] in ids else 0
    stage = 3 if t["status"] == "called" else 2
    return dict(t=t, ahead=ahead, stage=stage, eta=ahead * AVG_MIN_PER_PATIENT)


# --------------------------------------------------------------- students
@app.route("/checkin", methods=["GET", "POST"])
@login_required(["student"])
def checkin():
    u = current_user()
    if request.method == "POST":
        text = request.form.get("complaint", "").strip()
        kind = "emergency" if request.form.get("urgency") == "emergency" else "routine"
        if not text:
            flash("Describe your symptoms before joining the queue.", "err")
        elif active_ticket(u["id"]):
            flash("You already have a ticket in the queue.", "err")
        else:
            if suggest(text):
                kind = "emergency"
            cur = db().execute("INSERT INTO tickets(kind,user_id,complaint,created) VALUES(?,?,?,?)",
                               (kind, u["id"], text, ts()))
            log(u["name"], f"Checked in {'E' if kind == 'emergency' else 'R'}-{cur.lastrowid:03d}")
            db().commit()
            flash("You are in the queue.", "ok")
        return redirect(url_for("checkin"))
    st = my_status(u["id"])
    est = (len(waiting_order()) if not st else st["eta"]) 
    return render_template("checkin.html", st=st, est=est, board=board_data(u["id"]))


@app.route("/leave", methods=["POST"])
@login_required(["student"])
def leave():
    t = active_ticket(current_user()["id"])
    if t:
        db().execute("UPDATE tickets SET removed=1 WHERE id=?", (t["id"],))
        db().commit()
        flash("You left the queue.", "ok")
    return redirect(url_for("checkin"))


@app.route("/board")
@login_required(["student"])
def board():
    return render_template("board.html", board=board_data(current_user()["id"]))


@app.route("/partial/board")
@login_required()
def board_partial():
    return render_template("_board.html", board=board_data(current_user()["id"]))


@app.route("/api/status")
@login_required(["student"])
def api_status():
    st = my_status(current_user()["id"])
    return jsonify(stage=st["stage"] if st else 0, ahead=st["ahead"] if st else 0)


@app.route("/history")
@login_required(["student"])
def history():
    u = current_user()
    d = db()
    visits = d.execute("SELECT * FROM visits WHERE user_id=? ORDER BY date DESC, id DESC", (u["id"],)).fetchall()
    appts = d.execute("SELECT * FROM appts WHERE user_id=? ORDER BY date", (u["id"],)).fetchall()
    st = my_status(u["id"])
    nxt = bool(st and st["stage"] == 2 and st["ahead"] == 0)
    return render_template("history.html", visits=visits, appts=appts, is_next=nxt,
                           slots=["09:00 AM", "10:00 AM", "01:00 PM", "02:00 PM"])


@app.route("/profile", methods=["POST"])
@login_required(["student"])
def profile():
    f = request.form
    db().execute("UPDATE users SET blood=?, allergies=?, contact=? WHERE id=?",
                 (f.get("blood", ""), f.get("allergies", ""), f.get("contact", ""), current_user()["id"]))
    db().commit()
    flash("Profile saved.", "ok")
    return redirect(url_for("history"))


@app.route("/book", methods=["POST"])
@login_required(["student"])
def book():
    f = request.form
    if not f.get("date") or not f.get("slot"):
        flash("Pick a date and a time slot.", "err")
    elif db().execute("SELECT 1 FROM appts WHERE date=? AND slot=?", (f["date"], f["slot"])).fetchone():
        flash("That slot is already taken. Pick another.", "err")
    else:
        db().execute("INSERT INTO appts(user_id,date,slot,reason) VALUES(?,?,?,?)",
                     (current_user()["id"], f["date"], f["slot"], f.get("reason", "")))
        db().commit()
        flash("Appointment booked.", "ok")
    return redirect(url_for("history"))


@app.route("/settings", methods=["POST"])
@login_required(["student"])
def settings():
    key = request.json.get("key")
    if key in ("sms", "email"):
        db().execute(f"UPDATE users SET {key}=? WHERE id=?", (1 if request.json.get("on") else 0,
                                                              current_user()["id"]))
        db().commit()
    return jsonify(ok=True)


@app.route("/certificate/<int:vid>")
@login_required()
def certificate(vid):
    v = db().execute("SELECT v.*, u.name, u.code, u.age FROM visits v JOIN users u ON u.id=v.user_id "
                     "WHERE v.id=?", (vid,)).fetchone()
    u = current_user()
    if not v or (u["role"] == "student" and v["user_id"] != u["id"]):
        flash("Certificate not found.", "err")
        return redirect(url_for("home"))
    return render_template("certificate.html", v=v)


# ------------------------------------------------------------------ staff
@app.route("/dashboard")
@login_required(STAFF)
def dashboard():
    d = db()
    order = waiting_order()
    e = sum(1 for t in order if t["kind"] == "emergency")
    r = len(order) - e
    tickets = d.execute("SELECT t.*, u.name FROM tickets t JOIN users u ON u.id=t.user_id "
                        "WHERE t.removed=0 AND t.status IN ('waiting','called','hold') "
                        "ORDER BY CASE t.kind WHEN 'emergency' THEN 0 ELSE 1 END, t.created").fetchall()
    q = request.args.get("q", "").strip()
    sql = "SELECT * FROM users WHERE role='student' AND deleted_at IS NULL"
    args = []
    if q:
        sql += " AND (code LIKE ? OR name LIKE ?)"
        args = [f"%{q}%", f"%{q}%"]
    students = d.execute(sql + " ORDER BY name", args).fetchall()
    total_students = d.execute("SELECT COUNT(*) FROM users WHERE role='student' AND deleted_at IS NULL").fetchone()[0]
    load = []
    for i in range(5, -1, -1):
        day = (now() - timedelta(days=i)).strftime("%Y-%m-%d")
        load.append(d.execute("SELECT COUNT(*) FROM tickets WHERE created LIKE ? AND removed=0",
                              (day + "%",)).fetchone()[0])
    mx = max(load) or 1
    load = [max(10, round(c / mx * 100)) if c else 10 for c in load]
    return render_template("dashboard.html", tickets=tickets, e=e, r=r, students=students,
                           total_students=total_students, q=q, load=load, undo=session.pop("undo", None),
                           confirm=request.args.get("confirm", type=int), nav="dash")


@app.route("/q/call/<kind>", methods=["POST"])
@login_required(STAFF)
def call_next(kind):
    nxt = next((t for t in waiting_order() if t["kind"] == kind), None)
    if not nxt:
        flash(f"No {kind} cases are waiting.", "err")
    else:
        db().execute("UPDATE tickets SET status='called', called_at=? WHERE id=?", (ts(), nxt["id"]))
        log(who(current_user()), f"Called {tno(nxt)}")
        db().commit()
        flash(f"Called {tno(nxt)} · {nxt['name']}.", "ok")
    return redirect(url_for("dashboard"))


@app.route("/q/<int:tid>/<action>", methods=["POST"])
@login_required(STAFF)
def ticket_action(tid, action):
    u, d = current_user(), db()
    t = d.execute("SELECT * FROM tickets WHERE id=?", (tid,)).fetchone()
    if not t:
        return redirect(url_for("dashboard"))
    label = tno(t)
    if action == "remove":
        if u["role"] not in DOCTOR_UP:
            flash("Only a doctor or admin can remove cases.", "err")
            return redirect(request.referrer or url_for("dashboard"))
        d.execute("UPDATE tickets SET removed=1 WHERE id=?", (tid,))
        log(who(u), f"Removed {label} from queue (soft delete)", "ticket", tid)
        session["undo"] = tid
    elif action == "undo":
        d.execute("UPDATE tickets SET removed=0 WHERE id=?", (tid,))
    else:
        sets = {"call": ("status='called', called_at=?", ts()), "recall": ("status='called', called_at=?", ts()),
                "done": ("status='seen'", None), "hold": ("status='hold'", None),
                "noshow": ("status='noshow'", None), "skip": ("status='waiting', created=?", ts()),
                "accept": ("kind='emergency'", None), "override": ("kind='routine'", None),
                "transfer": ("kind=CASE kind WHEN 'emergency' THEN 'routine' ELSE 'emergency' END", None)}
        if action in sets:
            clause, val = sets[action]
            d.execute(f"UPDATE tickets SET {clause} WHERE id=?", ([val, tid] if val else [tid]))
            log(who(u), f"{action.capitalize()} {label}")
    d.commit()
    return redirect(request.referrer or url_for("dashboard"))


@app.route("/students/<int:uid>/delete", methods=["POST"])
@login_required(STAFF)
def delete_student(uid):
    if current_user()["role"] != "admin":
        flash("Only an admin can delete students.", "err")
        return redirect(url_for("dashboard"))
    s = db().execute("SELECT * FROM users WHERE id=? AND role='student'", (uid,)).fetchone()
    if s:
        db().execute("UPDATE users SET deleted_at=? WHERE id=?", (ts(), uid))
        db().execute("UPDATE tickets SET removed=1 WHERE user_id=?", (uid,))
        log("ADMIN", f"Deleted student {s['code']} (hidden for {HIDE_DAYS} days)", "user", uid)
        db().commit()
        flash(f"Deleted {s['code']}.", "ok")
    else:
        flash("Student not found.", "err")
    return redirect(url_for("dashboard"))


@app.route("/log/<int:lid>/restore", methods=["POST"])
@login_required(["admin"])
def restore(lid):
    row = db().execute("SELECT * FROM log WHERE id=?", (lid,)).fetchone()
    if row and row["undo_type"] and not row["undone"]:
        table = {"ticket": "UPDATE tickets SET removed=0 WHERE id=?",
                 "user": "UPDATE users SET deleted_at=NULL WHERE id=?"}[row["undo_type"]]
        db().execute(table, (row["undo_id"],))
        db().execute("UPDATE log SET undone=1 WHERE id=?", (lid,))
        log("ADMIN", "Restored: " + row["action"])
        db().commit()
        flash("Restored.", "ok")
    return redirect(url_for("reports"))


@app.route("/record")
@login_required(STAFF)
def record_home():
    order = waiting_order()
    if order:
        return redirect(url_for("record", uid=order[0]["user_id"]))
    first = db().execute("SELECT id FROM users WHERE role='student' AND deleted_at IS NULL ORDER BY name LIMIT 1").fetchone()
    if first:
        return redirect(url_for("record", uid=first["id"]))
    flash("No students are registered yet.", "err")
    return redirect(url_for("dashboard"))


@app.route("/record/<int:uid>", methods=["GET", "POST"])
@login_required(STAFF)
def record(uid):
    d, u = db(), current_user()
    s = d.execute("SELECT * FROM users WHERE id=? AND role='student' AND deleted_at IS NULL", (uid,)).fetchone()
    if not s:
        flash("Student not found.", "err")
        return redirect(url_for("dashboard"))
    t = active_ticket(uid)
    if request.method == "POST":
        f = request.form
        is_nurse = u["role"] == "nurse"
        rx = "" if is_nurse else f.get("rx", "")
        cur = d.execute(
            "INSERT INTO visits(user_id,date,complaint,temp,bp,pulse,spo2,diagnosis,notes,rx,followup) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            (uid, now().strftime("%Y-%m-%d"), (t["complaint"] if t else f.get("diagnosis", "")) or "Consultation",
             f.get("temp", ""), f.get("bp", ""), f.get("pulse", ""), f.get("spo2", ""),
             "" if is_nurse else f.get("diagnosis", ""), "" if is_nurse else f.get("notes", ""),
             rx, f.get("followup", "")))
        if rx:
            d.execute("UPDATE meds SET qty=MAX(qty-1,0) WHERE name=?", (rx,))
        if t:
            d.execute("UPDATE tickets SET status='seen' WHERE id=?", (t["id"],))
        log(who(u), f"Record edit saved for {s['code']}")
        d.commit()
        flash("Record saved.", "ok")
        if f.get("action") == "cert":
            return redirect(url_for("certificate", vid=cur.lastrowid))
        return redirect(url_for("record", uid=uid))
    visits = d.execute("SELECT * FROM visits WHERE user_id=? ORDER BY date DESC, id DESC", (uid,)).fetchall()
    activity = d.execute("SELECT * FROM log WHERE action LIKE ? ORDER BY id DESC LIMIT 5",
                         (f"%{s['code']}%",)).fetchall()
    meds = d.execute("SELECT * FROM meds ORDER BY name").fetchall()
    return render_template("record.html", s=s, t=t, kw=suggest(t["complaint"]) if t else None,
                           visits=visits, activity=activity, meds=meds, nav="work",
                           opened=ts(), readonly=(u["role"] == "nurse"))


@app.route("/reports")
@login_required(DOCTOR_UP)
def reports():
    d = db()
    today = now().strftime("%Y-%m-%d")
    visits_today = d.execute("SELECT COUNT(*) FROM tickets WHERE created LIKE ? AND removed=0", (today + "%",)).fetchone()[0]
    waits = [(datetime.strptime(r["called_at"], "%Y-%m-%d %H:%M:%S") -
              datetime.strptime(r["created"], "%Y-%m-%d %H:%M:%S")).total_seconds() / 60
             for r in d.execute("SELECT created, called_at FROM tickets WHERE called_at IS NOT NULL AND removed=0")]
    avg_wait = f"{round(sum(waits) / len(waits))} min" if waits else "0 min"
    hours = {}
    for r in d.execute("SELECT created FROM tickets WHERE removed=0"):
        h = int(r["created"][11:13])
        hours[h] = hours.get(h, 0) + 1
    if hours:
        h = max(hours, key=hours.get)
        peak = f"{(h % 12) or 12}:00 {'AM' if h < 12 else 'PM'}"
    else:
        peak = "—"
    monday = now() - timedelta(days=now().weekday())
    week = [d.execute("SELECT COUNT(*) FROM tickets WHERE created LIKE ? AND removed=0",
                      ((monday + timedelta(days=i)).strftime("%Y-%m-%d") + "%",)).fetchone()[0] for i in range(6)]
    mx = max(week) or 1
    week_h = [max(8, round(c / mx * 100)) if c else 8 for c in week]
    texts = [(r[0] or "").lower() for r in d.execute("SELECT complaint FROM tickets WHERE removed=0")] + \
            [(r[0] or "").lower() for r in d.execute("SELECT complaint FROM visits")]
    complaints = []
    for label, keys in [("Headache", ["headache"]), ("Cough and cold", ["cough", "cold"]),
                        ("Fever", ["fever"]), ("Injury", ["injury", "wound", "sprain"])]:
        complaints.append([label, sum(1 for t in texts if any(k in t for k in keys))])
    cmax = max(c[1] for c in complaints) or 1
    complaints = [(l, max(4, round(c / cmax * 100)) if c else 4) for l, c in complaints]
    meds = d.execute("SELECT * FROM meds ORDER BY id").fetchall()
    logs = d.execute("SELECT * FROM log ORDER BY id DESC LIMIT 15").fetchall()
    return render_template("reports.html", visits_today=visits_today, avg_wait=avg_wait, peak=peak,
                           week_h=week_h, complaints=complaints, meds=meds, logs=logs, nav="work",
                           hide_days=HIDE_DAYS)


@app.route("/reports/export.csv")
@login_required(DOCTOR_UP)
def export_csv():
    out = io.StringIO()
    w = csv.writer(out)
    w.writerow(["date", "sr_code", "name", "complaint", "diagnosis", "prescription", "follow_up"])
    for r in db().execute("SELECT v.*, u.code, u.name FROM visits v JOIN users u ON u.id=v.user_id ORDER BY v.date DESC"):
        w.writerow([r["date"], r["code"], r["name"], r["complaint"], r["diagnosis"], r["rx"], r["followup"]])
    return Response(out.getvalue(), mimetype="text/csv",
                    headers={"Content-Disposition": "attachment; filename=phalanx-visits.csv"})


# -------------------------------------------------------------- benchmark
@app.route("/benchmark", methods=["POST"])
@login_required(STAFF)
def benchmark():
    """Compare three ways to run a two-priority queue (CpE 411)."""
    n = 2000
    items = [(random.random() < 0.2, i) for i in range(n)]  # (is_emergency, id)

    def run_list():
        q = []
        for e, i in items:
            q.append((0 if e else 1, i))
        while q:
            q.remove(min(q))  # O(n) scan every time

    def run_deques():
        em, ro = deque(), deque()
        for e, i in items:
            (em if e else ro).append(i)
        while em or ro:
            (em or ro).popleft()

    def run_heap():
        h = []
        for e, i in items:
            heapq.heappush(h, (0 if e else 1, i))
        while h:
            heapq.heappop(h)

    res = []
    for name, big_o, fn in [("Unsorted list + min()", "O(n) per call", run_list),
                            ("Two FIFO deques", "O(1) per call", run_deques),
                            ("Binary heap (heapq)", "O(log n) per call", run_heap)]:
        t0 = time.perf_counter()
        fn()
        res.append(dict(name=name, big_o=big_o, ms=round((time.perf_counter() - t0) * 1000, 2)))
    return jsonify(n=n, results=res)


if __name__ == "__main__":
    app.run(debug=True)
