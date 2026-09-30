#!/usr/bin/env python3
"""VVS Dashboard — with admin CRUD, QR gen, CSV export, stats API."""
from flask import (Flask, render_template, request, jsonify,
                   redirect, url_for, Response, abort)
import sqlite3, os, io, csv, hashlib, secrets, base64
from datetime import datetime, timedelta

sys_path = os.path.expanduser("~/vvs")
import sys
sys.path.append(sys_path)
from scripts.config import DB_PATH as DB
from scripts.qr_handler import QRHandler

app = Flask(__name__)
ALL_TABLES = ["vehicles", "gates", "verification_logs", "entry_exit",
              "lora_metrics", "operational_metrics", "power_metrics",
              "system_events", "offline_queue"]


def q(sql, args=()):
    c = sqlite3.connect(DB); c.row_factory = sqlite3.Row
    rows = [dict(r) for r in c.execute(sql, args).fetchall()]
    c.close(); return rows

def q1(sql, args=()):
    c = sqlite3.connect(DB); c.row_factory = sqlite3.Row
    r = c.execute(sql, args).fetchone()
    c.close(); return dict(r) if r else {}


# ── Existing pages ─────────────────────────────────────
@app.route("/")
def home():
    stats = {
        "vehicles":     q1("SELECT COUNT(*) n FROM vehicles")["n"],
        "gates_online": q1("SELECT COUNT(*) n FROM gates WHERE status='online'")["n"],
        "gates_total":  q1("SELECT COUNT(*) n FROM gates")["n"],
        "logs_today":   q1("SELECT COUNT(*) n FROM verification_logs WHERE date(scan_time)=date('now')")["n"],
        "lora_tests":   q1("SELECT COUNT(*) n FROM lora_metrics")["n"],
    }
    gates = q("SELECT * FROM gates ORDER BY gate_id")
    recent = q("""SELECT v.registration_no, v.owner_name, l.gate_id,
                         l.scan_time, l.verification_result
                  FROM verification_logs l
                  LEFT JOIN vehicles v ON v.vehicle_id = l.vehicle_id
                  ORDER BY l.scan_time DESC LIMIT 10""")
    return render_template("home.html", stats=stats, gates=gates, recent=recent)

@app.route("/lora")
def lora():
    return render_template("lora.html",
        rows=q("SELECT * FROM lora_metrics ORDER BY timestamp DESC LIMIT 500"))

@app.route("/operational")
def operational():
    return render_template("operational.html",
        rows=q("SELECT * FROM operational_metrics ORDER BY timestamp DESC LIMIT 500"))

@app.route("/power")
def power():
    return render_template("power.html",
        rows=q("SELECT * FROM power_metrics ORDER BY timestamp DESC LIMIT 500"))

@app.route("/logs")
def logs():
    return render_template("logs.html", rows=q("""
        SELECT l.*, v.registration_no, v.owner_name
        FROM verification_logs l
        LEFT JOIN vehicles v ON v.vehicle_id=l.vehicle_id
        ORDER BY l.scan_time DESC LIMIT 200"""))

@app.route("/events")
def events():
    return render_template("events.html",
        rows=q("SELECT * FROM system_events ORDER BY timestamp DESC LIMIT 200"))

@app.route("/data")
def data_viewer():
    table = request.args.get("table", "lora_metrics")
    if table not in ALL_TABLES: table = "lora_metrics"
    rows = q(f"SELECT * FROM {table} ORDER BY rowid DESC LIMIT 100")
    cols = list(rows[0].keys()) if rows else []
    counts = {t: q1(f"SELECT COUNT(*) n FROM {t}")["n"] for t in ALL_TABLES}
    return render_template("data.html", table=table, tables=ALL_TABLES,
                           rows=rows, cols=cols, counts=counts)


# ── Gap 2.1 — Vehicle management ───────────────────────
@app.route("/vehicles")
def vehicles():
    rows = q("""SELECT vehicle_id, vid, owner_name, registration_no,
                       vehicle_type, valid_from, valid_to, is_active, created_at
                FROM vehicles ORDER BY created_at DESC""")
    return render_template("vehicles.html", vehicles=rows)

@app.route("/vehicles/new", methods=["GET", "POST"])
def vehicle_new():
    if request.method == "POST":
        owner = request.form["owner_name"].strip()
        reg   = request.form["registration_no"].strip().upper()
        vtype = request.form["vehicle_type"]
        days  = int(request.form.get("valid_days", 365))

        vid = hashlib.sha256(secrets.token_bytes(32)).hexdigest()[:32]
        tok = (base64.b64encode(secrets.token_bytes(16)).decode()
               .replace("=","").replace("+","").replace("/",""))
        today    = datetime.now().date()
        valid_to = today + timedelta(days=days)

        c = sqlite3.connect(DB)
        try:
            c.execute("""INSERT INTO vehicles
                (vid, owner_name, registration_no, vehicle_type,
                 qr_token, valid_from, valid_to, is_active)
                VALUES (?,?,?,?,?,?,?,1)""",
                (vid, owner, reg, vtype, tok,
                 today.isoformat(), valid_to.isoformat()))
            c.commit()
            new_id = c.lastrowid
            return redirect(url_for("vehicle_qr", vehicle_id=new_id))
        except sqlite3.IntegrityError as e:
            return render_template("vehicle_new.html", error=str(e))
        finally:
            c.close()

    return render_template("vehicle_new.html")

@app.route("/vehicles/<int:vehicle_id>/qr")
def vehicle_qr(vehicle_id):
    row = q1("SELECT vid, qr_token, registration_no FROM vehicles WHERE vehicle_id=?",
             (vehicle_id,))
    if not row:
        abort(404)
    import qrcode
    from io import BytesIO
    sig = QRHandler().generate_signature(row["vid"], row["qr_token"])
    payload = f"{row['vid']}|{row['qr_token']}|{sig}"
    img = qrcode.make(payload)
    buf = BytesIO(); img.save(buf, format="PNG"); buf.seek(0)
    fname = f"qr_{row['registration_no'].replace(' ','_')}.png"
    return Response(buf.getvalue(), mimetype="image/png",
                    headers={"Content-Disposition": f'inline; filename="{fname}"'})

@app.route("/vehicles/<int:vehicle_id>/revoke", methods=["POST"])
def vehicle_revoke(vehicle_id):
    c = sqlite3.connect(DB)
    c.execute("UPDATE vehicles SET is_active=0 WHERE vehicle_id=?", (vehicle_id,))
    c.commit(); c.close()
    return redirect(url_for("vehicles"))

@app.route("/vehicles/<int:vehicle_id>/activate", methods=["POST"])
def vehicle_activate(vehicle_id):
    c = sqlite3.connect(DB)
    c.execute("UPDATE vehicles SET is_active=1 WHERE vehicle_id=?", (vehicle_id,))
    c.commit(); c.close()
    return redirect(url_for("vehicles"))


# ── Gap 2.4 — CSV export ────────────────────────────────
@app.route("/export/<table>")
def export_csv(table):
    if table not in ALL_TABLES:
        abort(404)
    c = sqlite3.connect(DB); c.row_factory = sqlite3.Row
    rows = c.execute(f"SELECT * FROM {table}").fetchall()
    c.close()
    out = io.StringIO()
    w = csv.writer(out)
    if rows:
        w.writerow(rows[0].keys())
        for r in rows:
            w.writerow([r[k] for k in r.keys()])
    else:
        w.writerow(["empty"])
    return Response(out.getvalue(), mimetype="text/csv",
                    headers={"Content-Disposition":
                             f"attachment; filename={table}.csv"})


# ── Gap 2.5 — live stats API ────────────────────────────
@app.route("/api/stats")
def api_stats():
    return jsonify({
        "vehicles":     q1("SELECT COUNT(*) n FROM vehicles")["n"],
        "gates_online": q1("SELECT COUNT(*) n FROM gates WHERE status='online'")["n"],
        "gates_total":  q1("SELECT COUNT(*) n FROM gates")["n"],
        "logs_today":   q1("SELECT COUNT(*) n FROM verification_logs WHERE date(scan_time)=date('now')")["n"],
        "lora_tests":   q1("SELECT COUNT(*) n FROM lora_metrics")["n"],
    })


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=False)