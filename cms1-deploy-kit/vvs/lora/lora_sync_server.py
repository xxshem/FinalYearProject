#!/usr/bin/env python3
import serial, sqlite3, json, time, os, sys
from datetime import datetime

VVS_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, VVS_DIR)
from scripts.config import DB_PATH
from scripts.offline_queue import pending, mark_sent, mark_failed

SERIAL_PORT = "/dev/ttyUSB0"
BAUD = 115200
CHUNK = 180
INTERVAL = 10
TABLES = ["vehicles","gates","verification_logs","entry_exit",
          "lora_metrics","operational_metrics","power_metrics","system_events"]

def dump_new(since):
    c = sqlite3.connect(DB_PATH); c.row_factory = sqlite3.Row
    out = {}
    for t in TABLES:
        try:
            rows = [dict(r) for r in c.execute(
                f"SELECT * FROM {t} WHERE COALESCE(timestamp, created_at) > ?",
                (since,))]
        except sqlite3.OperationalError: rows = []
        if rows: out[t] = rows
    c.close(); return out

def main():
    ser = serial.Serial(SERIAL_PORT, BAUD, timeout=1)
    time.sleep(2)
    print(f"[SYNC-TX] pushing via {SERIAL_PORT}")
    last = "1970-01-01 00:00:00"
    while True:
        try:
            payload = dump_new(last)
            if payload:
                blob = json.dumps(payload)
                ser.write(("C2:" + json.dumps({"type":"SYNC_BEGIN","size":len(blob)}) + "\n").encode())
                time.sleep(0.2)
                for i in range(0, len(blob), CHUNK):
                    ser.write(("C2:" + blob[i:i+CHUNK] + "\n").encode())
                    time.sleep(0.35)
                ser.write(("C2:" + json.dumps({"type":"SYNC_END"}) + "\n").encode())
                for row in pending():
                    mark_sent(row["queue_id"])
                last = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                print(f"[SYNC-TX] {len(blob)} bytes, tables: {list(payload)}")
            time.sleep(INTERVAL)
        except Exception as e:
            for row in pending():
                mark_failed(row["queue_id"])
            print(f"[SYNC-TX] {e}"); time.sleep(5)

if __name__ == "__main__":
    while True:
        try: main()
        except Exception as e:
            print(f"[SYNC-TX] restart in 10s: {e}"); time.sleep(10)