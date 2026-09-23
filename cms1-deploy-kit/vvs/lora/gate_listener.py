#!/usr/bin/env python3
"""CMS1 gate listener. Reads CH1 from gateway modem, verifies vs SQLite."""
import serial, sqlite3, json, os, sys, time
from datetime import datetime

try:
    from vvs.scripts.config import DB_PATH
    from vvs.scripts.offline_queue import enqueue
except ImportError:
    # Keep direct execution from the vvs/lora directory working as well.
    sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "scripts")))
    from config import DB_PATH  # type: ignore[import-not-found]
    from offline_queue import enqueue  # type: ignore[import-not-found]

SERIAL_PORT = "/dev/ttyUSB0"
BAUD = 115200

def verify(token, gate_id):
    c = sqlite3.connect(DB_PATH); c.row_factory = sqlite3.Row
    row = c.execute("SELECT * FROM vehicles WHERE qr_token=?", (token,)).fetchone()
    granted, reason = False, None
    if not row:
        reason = "unknown token"
    elif not row["is_active"]:
        reason = "inactive"
    else:
        today = datetime.now().date()
        vf = datetime.fromisoformat(row["valid_from"]).date()
        vt = datetime.fromisoformat(row["valid_to"]).date()
        if not (vf <= today <= vt): reason = "expired"
        else: granted = True

    c.execute("""INSERT INTO verification_logs
        (vehicle_id, gate_id, qr_token, scan_time, verification_result, denial_reason)
        VALUES (?,?,?,?,?,?)""",
        (row["vehicle_id"] if row else None, gate_id, token,
         datetime.now(), "granted" if granted else "denied", reason))
    c.commit(); c.close()

    enqueue("verify",
            vehicle_id=row["vehicle_id"] if row else None,
            gate_id=gate_id, qr_token=token,
            payload={"result": "granted" if granted else "denied",
                     "reason": reason})

    return {"type":"VERIFY_RESP","granted":granted,"reason":reason or "",
            "reg": row["registration_no"] if row else ""}

def read_tagged(ser):
    raw = ser.readline().decode("utf-8", errors="ignore").strip()
    if not raw: return None, None
    if raw.startswith("C1:"): return 1, raw[3:]
    if raw.startswith("C2:"): return 2, raw[3:]
    return None, None

def main():
    print(f"[CMS1] listening on {SERIAL_PORT}")
    try:
        ser = serial.Serial(SERIAL_PORT, BAUD, timeout=1)
    except Exception as e:
        print(f"[CMS1] serial error: {e}")
        time.sleep(10); return
    time.sleep(2)
    while True:
        try:
            ch, payload = read_tagged(ser)
            if ch is None: continue
            pkt = json.loads(payload)
            if ch == 1 and pkt.get("type") == "VERIFY_REQ":
                resp = verify(pkt["token"], pkt["gate"])
                ser.write(("C1:" + json.dumps(resp) + "\n").encode())
                print(f"[CMS1] {pkt['gate']} granted={resp['granted']}")
        except json.JSONDecodeError: continue
        except Exception as e:
            print(f"[CMS1] error: {e}"); time.sleep(1)

if __name__ == "__main__":
    while True:
        try: main()
        except Exception as e:
            print(f"[CMS1] restart in 10s: {e}"); time.sleep(10)