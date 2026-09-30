#!/usr/bin/env python3
"""CMS1 gate listener. Reads CH1 from gateway modem, verifies vs SQLite."""
import serial, sqlite3, json, os, sys, time
from collections import OrderedDict
from datetime import datetime, timedelta

try:
    from vvs.scripts.config import DB_PATH
    from vvs.scripts.offline_queue import enqueue
    from vvs.scripts.qr_handler import QRHandler
    from vvs.scripts.init_db import init as init_database
    from vvs.lora.lora_sync_server import SyncPublisher
except ImportError:
    # Keep direct execution from the vvs/lora directory working as well.
    sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "scripts")))
    from config import DB_PATH  # type: ignore[import-not-found]
    from offline_queue import enqueue  # type: ignore[import-not-found]
    from qr_handler import QRHandler  # type: ignore[import-not-found]
    from init_db import init as init_database  # type: ignore[import-not-found]
    from lora_sync_server import SyncPublisher  # type: ignore[import-not-found]

SERIAL_PORT = os.environ.get("VVS_SERIAL_PORT", "/dev/ttyUSB0")
BAUD = 115200
CLONE_WINDOW_SEC = 5
REQUEST_CACHE = OrderedDict()

def verify_and_authorize(payload, gate_id, request_id=None, radio_metrics=None):
    processing_started = time.perf_counter()
    cache_key = (gate_id, str(request_id)) if request_id is not None else None
    if cache_key in REQUEST_CACHE:
        cached_payload, cached_response = REQUEST_CACHE[cache_key]
        if cached_payload == payload:
            REQUEST_CACHE.move_to_end(cache_key)
            return dict(cached_response)
        return {"type": "VERIFY_RESP", "granted": False,
                "reason": "request sequence reused", "reg": "", "action": None}

    parts = payload.split("|") if isinstance(payload, str) else []
    vid, token, signature = (parts + ["", "", ""])[:3]
    now = datetime.now()
    now_text = now.isoformat(sep=" ", timespec="seconds")
    c = sqlite3.connect(DB_PATH)
    c.row_factory = sqlite3.Row
    db_query_started = time.perf_counter()
    row = c.execute("SELECT * FROM vehicles WHERE qr_token=?", (token,)).fetchone()
    granted = False
    reason = "invalid QR code"
    action = None

    if row is None:
        reason = "unknown token"
    elif row["vid"] != vid or not QRHandler().verify_signature(vid, token, signature):
        reason = "invalid QR signature"
    elif not row["is_active"]:
        reason = "inactive"
    else:
        valid_from = datetime.fromisoformat(row["valid_from"]).date()
        valid_to = datetime.fromisoformat(row["valid_to"]).date()
        if not valid_from <= now.date() <= valid_to:
            reason = "expired"
        else:
            open_entry = c.execute("""SELECT record_id FROM entry_exit
                WHERE vehicle_id=? AND exit_time IS NULL
                ORDER BY entry_time DESC LIMIT 1""", (row["vehicle_id"],)).fetchone()
            if open_entry:
                last_grant = c.execute("""SELECT gate_id, scan_time FROM verification_logs
                    WHERE vehicle_id=? AND verification_result='granted'
                    ORDER BY scan_time DESC LIMIT 1""", (row["vehicle_id"],)).fetchone()
                if last_grant:
                    try:
                        last_time = datetime.fromisoformat(last_grant["scan_time"])
                    except ValueError:
                        last_time = now - timedelta(seconds=CLONE_WINDOW_SEC + 1)
                    if (now - last_time).total_seconds() < CLONE_WINDOW_SEC:
                        reason = ("possible clone detected" if last_grant["gate_id"] != gate_id
                                  else "duplicate scan")
                        if last_grant["gate_id"] != gate_id:
                            c.execute("""INSERT INTO system_events
                                (event_type, source, description)
                                VALUES ('clone_alert', ?, ?)""",
                                (gate_id, f"Vehicle {row['registration_no']} scanned at multiple gates"))
                    else:
                        action = "exit"
                else:
                    action = "exit"

                if action == "exit":
                    entry = c.execute("SELECT entry_time FROM entry_exit WHERE record_id=?",
                                      (open_entry["record_id"],)).fetchone()
                    entry_time = datetime.fromisoformat(entry["entry_time"])
                    duration = max(0.0, (now - entry_time).total_seconds() / 60)
                    c.execute("""UPDATE entry_exit SET exit_gate=?, exit_time=?,
                        duration_minutes=? WHERE record_id=?""",
                        (gate_id, now_text, duration, open_entry["record_id"]))
                    granted, reason = True, ""
            else:
                c.execute("""INSERT INTO entry_exit (vehicle_id, entry_gate, entry_time)
                    VALUES (?, ?, ?)""", (row["vehicle_id"], gate_id, now_text))
                granted, reason, action = True, "", "entry"

    c.execute("""INSERT INTO verification_logs
        (vehicle_id, gate_id, qr_token, scan_time, verification_result, denial_reason)
        VALUES (?, ?, ?, ?, ?, ?)""",
        (row["vehicle_id"] if row else None, gate_id, token, now_text,
         "granted" if granted else "denied", reason or None))
    c.execute("UPDATE gates SET status='online', last_seen=? WHERE gate_id=?",
              (now_text, gate_id))

    db_query_ms = (time.perf_counter() - db_query_started) * 1000
    response_time_ms = (time.perf_counter() - processing_started) * 1000
    rssi_dbm = radio_metrics.get("rssi_dbm") if radio_metrics else None
    snr_db = radio_metrics.get("snr_db") if radio_metrics else None
    metric_id = str(request_id or f"{gate_id}-{now.strftime('%Y%m%d%H%M%S%f')}")
    c.execute("""INSERT INTO lora_metrics
        (test_id, gate_id, channel, rssi_dbm, snr_db, packets_sent,
         packets_received, pdr_percent, latency_ms, test_environment, notes)
        VALUES (?, ?, 1, ?, ?, 1, 1, 100, ?, 'runtime', ?)""",
        (metric_id, gate_id, rssi_dbm, snr_db, response_time_ms,
         "CMS1 processing latency; radio receive measurements"))
    queue_size = c.execute(
        "SELECT COUNT(*) FROM offline_queue WHERE status='pending'").fetchone()[0]
    c.execute("""INSERT INTO operational_metrics
        (test_id, gate_id, response_time_ms, cache_hit, db_query_ms,
         offline_mode, queue_size, notes)
        VALUES (?, ?, ?, 0, ?, 0, ?, ?)""",
        (metric_id, gate_id, response_time_ms, db_query_ms, queue_size,
         f"{action or 'denied'}; request handling time on CMS1"))
    c.commit()
    c.close()

    try:
        enqueue("verify", vehicle_id=row["vehicle_id"] if row else None,
                gate_id=gate_id, qr_token=token,
                payload={"result": "granted" if granted else "denied",
                         "reason": reason, "action": action})
    except sqlite3.Error as exc:
        print(f"[CMS1] offline queue error: {exc}")

    response = {"type": "VERIFY_RESP", "granted": granted, "reason": reason,
                "reg": row["registration_no"] if row else "", "action": action,
                "response_time_ms": round(response_time_ms, 2)}
    if cache_key is not None:
        REQUEST_CACHE[cache_key] = (payload, response)
        if len(REQUEST_CACHE) > 128:
            REQUEST_CACHE.popitem(last=False)
    return response

def read_tagged(ser):
    raw = ser.readline().decode("utf-8", errors="ignore").strip()
    if not raw: return None, None, None
    if raw.startswith("C1:"):
        payload = raw[3:]
        radio_metrics = None
        metrics, separator, candidate = payload.partition(":")
        if separator and "," in metrics:
            try:
                rssi, snr = metrics.split(",", 1)
                radio_metrics = {"rssi_dbm": float(rssi), "snr_db": float(snr)}
            except ValueError:
                pass
            else:
                payload = candidate
        return 1, payload, radio_metrics
    if raw.startswith("C2:"): return 2, raw[3:], None
    return None, None, None

def main():
    init_database()
    print(f"[CMS1] listening on {SERIAL_PORT}")
    try:
        ser = serial.Serial(SERIAL_PORT, BAUD, timeout=0.2)
    except Exception as e:
        print(f"[CMS1] serial error: {e}")
        time.sleep(10); return
    time.sleep(2)
    sync_publisher = SyncPublisher()
    while True:
        try:
            ch, payload, radio_metrics = read_tagged(ser)
            if ch is not None:
                pkt = json.loads(payload)
                if ch == 2:
                    sync_publisher.handle_ack(pkt)
                elif ch == 1 and pkt.get("type") == "VERIFY_REQ":
                      gate_id = pkt.get("gate", "UNKNOWN")
                      request_id = (f"{pkt.get('boot')}:{pkt.get('seq')}"
                                if "seq" in pkt else None)
                      request_started = time.perf_counter()
                      resp = verify_and_authorize(
                        pkt.get("payload", ""), gate_id, request_id, radio_metrics)
                      ser.write(("C1:" + json.dumps(resp) + "\n").encode())
                      metrics = radio_metrics or {}
                      print(f"[METRICS] {gate_id} RSSI={metrics.get('rssi_dbm', 'n/a')} dBm "
                          f"SNR={metrics.get('snr_db', 'n/a')} dB "
                          f"Latency={(time.perf_counter() - request_started) * 1000:.1f} ms "
                          f"Response={resp['response_time_ms']} ms")
                      print(f"[CMS1] {gate_id} granted={resp['granted']} "
                          f"action={resp['action']} reason={resp['reason']}")
            sync_publisher.poll(ser)
        except json.JSONDecodeError: continue
        except Exception as e:
            print(f"[CMS1] error: {e}"); time.sleep(1)

if __name__ == "__main__":
    while True:
        try: main()
        except Exception as e:
            print(f"[CMS1] restart in 10s: {e}"); time.sleep(10)