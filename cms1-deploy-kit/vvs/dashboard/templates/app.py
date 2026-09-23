#!/usr/bin/env python3
"""
VVS Dashboard — Flask app.
Serves on port 5000 for both CMS1 and CMS2.
"""
from flask import Flask, render_template, request
import sqlite3, os

app = Flask(__name__)
DB = os.path.expanduser("~/vvs/database/vvs.db")


def q(sql, args=()):
    c = sqlite3.connect(DB)
    c.row_factory = sqlite3.Row
    rows = [dict(r) for r in c.execute(sql, args).fetchall()]
    c.close()
    return rows


def q1(sql, args=()):
    c = sqlite3.connect(DB)
    c.row_factory = sqlite3.Row
    r = c.execute(sql, args).fetchone()
    c.close()
    return dict(r) if r else {}


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
    rows = q("SELECT * FROM lora_metrics ORDER BY distance_m")
    return render_template("lora.html", rows=rows)


@app.route("/operational")
def operational():
    rows = q("SELECT * FROM operational_metrics ORDER BY timestamp DESC")
    return render_template("operational.html", rows=rows)


@app.route("/power")
def power():
    rows = q("SELECT * FROM power_metrics ORDER BY timestamp DESC")
    return render_template("power.html", rows=rows)


@app.route("/logs")
def logs():
    rows = q("""SELECT l.*, v.registration_no, v.owner_name
                FROM verification_logs l
                LEFT JOIN vehicles v ON v.vehicle_id = l.vehicle_id
                ORDER BY l.scan_time DESC LIMIT 200""")
    return render_template("logs.html", rows=rows)


@app.route("/events")
def events():
    rows = q("SELECT * FROM system_events ORDER BY timestamp DESC LIMIT 200")
    return render_template("events.html", rows=rows)


@app.route("/data")
def data_viewer():
    allowed = ["vehicles", "gates", "verification_logs", "entry_exit",
               "lora_metrics", "operational_metrics", "power_metrics",
               "system_events", "offline_queue"]
    table = request.args.get("table", "lora_metrics")
    if table not in allowed:
        table = "lora_metrics"
    rows = q(f"SELECT * FROM {table} ORDER BY rowid DESC LIMIT 100")
    cols = list(rows[0].keys()) if rows else []
    counts = {t: q1(f"SELECT COUNT(*) n FROM {t}")["n"] for t in allowed}
    return render_template("data.html", table=table, tables=allowed,
                           rows=rows, cols=cols, counts=counts)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=False)