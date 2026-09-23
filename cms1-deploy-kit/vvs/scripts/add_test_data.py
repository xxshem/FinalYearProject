#!/usr/bin/env python3
import sqlite3, sys, os, hashlib, secrets, base64
from datetime import datetime, timedelta

DB = os.path.expanduser("~/vvs/database/vvs.db")

def c():
    x = sqlite3.connect(DB); x.row_factory = sqlite3.Row; return x

def vehicle(owner, reg, vtype):
    x = c()
    vid = hashlib.sha256(secrets.token_bytes(32)).hexdigest()[:32]
    tok = base64.b64encode(secrets.token_bytes(16)).decode().replace("=","").replace("+","").replace("/","")
    today = datetime.now().date()
    x.execute("""INSERT INTO vehicles
        (vid, owner_name, registration_no, vehicle_type, qr_token,
         valid_from, valid_to)
        VALUES (?,?,?,?,?,?,?)""",
        (vid, owner, reg, vtype, tok, today, today+timedelta(days=365)))
    x.commit(); x.close()
    print(f"[OK] {reg} registered. Token: {tok}")

def gate(gid, loc):
    x = c()
    x.execute("INSERT OR REPLACE INTO gates (gate_id, location, status, last_seen) VALUES (?,?,?,?)",
              (gid, loc, "online", datetime.now()))
    x.commit(); x.close()
    print(f"[OK] {gid} -> {loc}")

def lora(test_id, dist, rssi, snr, sent, recv, lat):
    pdr = (int(recv)/int(sent)*100) if int(sent) else 0
    x = c()
    x.execute("""INSERT INTO lora_metrics
        (test_id, channel, distance_m, rssi_dbm, snr_db, packets_sent,
         packets_received, pdr_percent, latency_ms, test_environment)
        VALUES (?,?,?,?,?,?,?,?,?,?)""",
        (test_id, 1, int(dist), float(rssi), float(snr), int(sent), int(recv),
         round(pdr,2), float(lat), "LOS" if "LOS" in test_id.upper() else "Urban"))
    x.commit(); x.close()
    print(f"[OK] {test_id}: RSSI={rssi} PDR={pdr:.1f}%")

def op(test_id, resp, hit, tput=0, db=0):
    x = c()
    x.execute("""INSERT INTO operational_metrics
        (test_id, response_time_ms, cache_hit, throughput_vpm, db_query_ms)
        VALUES (?,?,?,?,?)""", (test_id, float(resp), int(hit), float(tput), float(db)))
    x.commit(); x.close()
    print(f"[OK] {test_id}: {resp}ms")

def power(state, volts, ma):
    x = c()
    x.execute("""INSERT INTO power_metrics
        (test_id, state, voltage_v, current_ma, power_mw)
        VALUES (?,?,?,?,?)""",
        (f"POWER_{state}", state, float(volts), float(ma), float(volts)*float(ma)))
    x.commit(); x.close()
    print(f"[OK] {state}: {volts}V x {ma}mA")

def log(gid, reg, result):
    x = c()
    r = x.execute("SELECT vehicle_id FROM vehicles WHERE registration_no=?", (reg,)).fetchone()
    if not r:
        print(f"[!] {reg} not found"); x.close(); return
    x.execute("""INSERT INTO verification_logs
        (vehicle_id, gate_id, qr_token, scan_time, verification_result)
        VALUES (?,?,?,?,?)""", (r["vehicle_id"], gid, "TEST", datetime.now(), result))
    x.commit(); x.close()
    print(f"[OK] {reg} @ {gid}: {result}")

def event(etype, source, desc):
    x = c()
    x.execute("INSERT INTO system_events (event_type, source, description) VALUES (?,?,?)",
              (etype, source, desc))
    x.commit(); x.close()
    print(f"[OK] {etype}")

def summary():
    x = c()
    for t in ["vehicles","gates","verification_logs","entry_exit",
              "lora_metrics","operational_metrics","power_metrics",
              "system_events","offline_queue"]:
        n = x.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
        print(f"  {t:24s}: {n}")
    x.close()

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Commands: vehicle|gate|lora|op|power|log|event|summary")
        sys.exit(0)
    cmd, a = sys.argv[1], sys.argv[2:]
    {"vehicle":vehicle,"gate":gate,"lora":lora,"op":op,"power":power,
     "log":log,"event":event,"summary":summary}[cmd](*a)