#!/usr/bin/env python3
"""CMS1 gate listener. Reads CH1 from gateway modem, verifies vs SQLite."""
import serial, sqlite3, json, os, signal, sys, time
from datetime import datetime, timedelta

try:
    from vvs.scripts.config import DB_PATH
    from vvs.scripts.offline_queue import enqueue
    from vvs.scripts.qr_handler import QRHandler
    from vvs.scripts.init_db import init as init_database
    from vvs.lora.lora_sync_server import MAX_LORA_PAYLOAD_BYTES, SyncPublisher
except ImportError:
    # Keep direct execution from the vvs/lora directory working as well.
    sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "scripts")))
    from config import DB_PATH  # type: ignore[import-not-found]
    from offline_queue import enqueue  # type: ignore[import-not-found]
    from qr_handler import QRHandler  # type: ignore[import-not-found]
    from init_db import init as init_database  # type: ignore[import-not-found]
    from lora_sync_server import (  # type: ignore[import-not-found]
        MAX_LORA_PAYLOAD_BYTES, SyncPublisher)

SERIAL_PORT = os.environ.get("VVS_SERIAL_PORT", "/dev/ttyUSB0")
BAUD = 115200
CLONE_WINDOW_SEC = 5
SERIAL_RECONNECT_INITIAL_SEC = 1
SERIAL_RECONNECT_MAX_SEC = 30
SERIAL_SETTLE_SEC = 2
SERIAL_FRAME_TIMEOUT_SEC = 1.0
MAX_SERIAL_FRAME_BYTES = 4096
SERIAL_EXCEPTIONS = (OSError,)
if hasattr(serial, "SerialException"):
    SERIAL_EXCEPTIONS = (serial.SerialException, OSError)
_radio_stats_requested = False


def _request_radio_stats(signum, frame):
    global _radio_stats_requested
    _radio_stats_requested = True


def _install_radio_stats_handler():
    signal_number = getattr(signal, "SIGUSR1", None)
    if signal_number is not None:
        signal.signal(signal_number, _request_radio_stats)


def verify_and_authorize(payload, gate_id, request_identity=None, radio_metrics=None):
    processing_started = time.perf_counter()
    if request_identity is not None:
        boot_id, sequence = request_identity
        if (type(boot_id) is not int or not 0 <= boot_id <= 0xFFFFFFFF or
                type(sequence) is not int or not 0 <= sequence <= 0xFFFF):
            raise ValueError("request identity must contain a uint32 boot and uint16 sequence")
    else:
        boot_id = sequence = None

    parts = payload.split("|") if isinstance(payload, str) else []
    vid, token, signature = (parts + ["", "", ""])[:3]
    c = sqlite3.connect(DB_PATH, timeout=10)
    c.row_factory = sqlite3.Row
    try:
        c.execute("BEGIN IMMEDIATE")
        if request_identity is not None:
            receipt = c.execute("""SELECT request_payload, response_json
                FROM verification_request_receipts
                WHERE gate_id=? AND boot_id=? AND sequence_number=?""",
                (gate_id, boot_id, sequence)).fetchone()
            if receipt is not None:
                if receipt["request_payload"] == payload:
                    return json.loads(receipt["response_json"])
                return {
                    "type": "VERIFY_RESP", "granted": False,
                    "reason": "request identity reused with different payload",
                    "reg": "", "action": None, "boot": boot_id, "seq": sequence,
                    "response_time_ms": round(
                        (time.perf_counter() - processing_started) * 1000, 2),
                }

        now = datetime.now()
        now_text = now.isoformat(sep=" ", timespec="seconds")
        db_query_started = time.perf_counter()
        row = c.execute("SELECT * FROM vehicles WHERE qr_token=?", (token,)).fetchone()
        c.execute("SAVEPOINT verification_transition")
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

        db_query_ms = (time.perf_counter() - db_query_started) * 1000
        response_time_ms = (time.perf_counter() - processing_started) * 1000
        rssi_dbm = radio_metrics.get("rssi_dbm") if radio_metrics else None
        snr_db = radio_metrics.get("snr_db") if radio_metrics else None
        request_label = (f"{gate_id}:{boot_id}:{sequence}" if request_identity is not None
                         else f"{gate_id}-{now.strftime('%Y%m%d%H%M%S%f')}")
        queue_size = c.execute(
            "SELECT COUNT(*) FROM offline_queue WHERE status='pending'").fetchone()[0]

        response = {"type": "VERIFY_RESP", "granted": granted, "reason": reason,
                    "reg": row["registration_no"] if row else "", "action": action,
                    "response_time_ms": round(response_time_ms, 2)}
        if request_identity is not None:
            response.update({"boot": boot_id, "seq": sequence})

        original_response = json.dumps(
            response, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
        serialized_response = serialize_verify_response(response, gate_id)
        if len(original_response) > MAX_LORA_PAYLOAD_BYTES:
            if granted:
                c.execute("ROLLBACK TO verification_transition")
            granted = False
            reason = "response_too_large"
            action = None
            compact_response = json.loads(serialized_response)
            compact_response["reason"] = reason
            serialized_response = json.dumps(
                compact_response, separators=(",", ":"), ensure_ascii=False
            ).encode("utf-8")
            if len(serialized_response) > MAX_LORA_PAYLOAD_BYTES:
                raise ValueError("compact VERIFY_RESP exceeds the LoRa payload limit")
        c.execute("RELEASE verification_transition")
        response = json.loads(serialized_response)

        c.execute("""INSERT INTO verification_logs
            (vehicle_id, gate_id, qr_token, scan_time, verification_result, denial_reason)
            VALUES (?, ?, ?, ?, ?, ?)""",
            (row["vehicle_id"] if row else None, gate_id, token, now_text,
             "granted" if granted else "denied", reason or None))
        c.execute("UPDATE gates SET status='online', last_seen=? WHERE gate_id=?",
                  (now_text, gate_id))
        c.execute("""INSERT INTO lora_metrics
            (test_id, gate_id, channel, rssi_dbm, snr_db, packets_sent,
             packets_received, pdr_percent, latency_ms, test_environment, notes)
            VALUES (?, ?, 1, ?, ?, 1, 1, 100, ?, 'runtime', ?)""",
            (request_label, gate_id, rssi_dbm, snr_db, response_time_ms,
             "CMS1 processing latency; radio receive measurements"))
        c.execute("""INSERT INTO operational_metrics
            (test_id, gate_id, response_time_ms, cache_hit, db_query_ms,
             offline_mode, queue_size, notes)
            VALUES (?, ?, ?, 0, ?, 0, ?, ?)""",
            (request_label, gate_id, response_time_ms, db_query_ms, queue_size,
             f"{action or 'denied'}; request handling time on CMS1"))

        if request_identity is not None:
            c.execute("""INSERT INTO verification_request_receipts
                (gate_id, boot_id, sequence_number, request_payload, response_json)
                VALUES (?, ?, ?, ?, ?)""",
                (gate_id, boot_id, sequence, payload,
                 serialized_response.decode("utf-8")))
        c.commit()
    except Exception:
        c.rollback()
        raise
    finally:
        c.close()

    try:
        enqueue("verify", vehicle_id=row["vehicle_id"] if row else None,
                gate_id=gate_id, qr_token=token,
                payload={"result": "granted" if granted else "denied",
                         "reason": reason, "action": action})
    except sqlite3.Error as exc:
        print(f"[CMS1] offline queue error: {exc}")

    return response

def attach_request_identity(response, request):
    correlated_response = dict(response)
    for key in ("boot", "seq"):
        if key in request:
            correlated_response[key] = request[key]
    return correlated_response


def serialize_verify_response(response, gate_id):
    serialized = json.dumps(
        response, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    if len(serialized) <= MAX_LORA_PAYLOAD_BYTES:
        return serialized

    print(f"[CMS1] oversized VERIFY_RESP gate={gate_id} bytes={len(serialized)}; "
          "sending compact denial")
    error_response = {
        "type": "VERIFY_RESP",
        "granted": False,
        "error": "response_too_large",
        "reason": "response_too_large",
        "reg": "",
        "action": None,
        "response_time_ms": response.get("response_time_ms", 0.0),
    }
    for key in ("boot", "seq"):
        if key in response:
            error_response[key] = response[key]

    serialized = json.dumps(
        error_response, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    if len(serialized) > MAX_LORA_PAYLOAD_BYTES:
        raise ValueError(
            f"compact VERIFY_RESP is {len(serialized)} bytes; "
            f"limit is {MAX_LORA_PAYLOAD_BYTES}")
    return serialized


def read_tagged(ser):
    raw = ser.readline()
    return parse_tagged_line(raw)


def parse_tagged_line(raw):
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8")
    raw = raw.strip()
    if not raw: return None, None, None
    if raw.startswith("STATS:"): return 3, raw[6:], None
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


class SerialFrameReader:
    def __init__(self):
        self.buffer = bytearray()
        self.discarding = False
        self.last_byte_at = None

    def reset(self):
        self.buffer.clear()
        self.discarding = False
        self.last_byte_at = None

    def read_tagged(self, ser):
        if (self.buffer or self.discarding) and self.last_byte_at is not None:
            if time.monotonic() - self.last_byte_at > SERIAL_FRAME_TIMEOUT_SEC:
                print("[CMS1] discarded incomplete stale serial frame")
                self.reset()

        raw = ser.readline()
        if not raw:
            return None, None, None

        self.last_byte_at = time.monotonic()
        for value in raw:
            if self.discarding:
                if value == 10:
                    self.discarding = False
                continue
            self.buffer.append(value)
            if len(self.buffer) > MAX_SERIAL_FRAME_BYTES:
                print("[CMS1] discarded oversized serial frame")
                self.buffer.clear()
                self.discarding = value != 10
                continue
            if value == 10:
                line = bytes(self.buffer)
                self.buffer.clear()
                self.last_byte_at = None
                try:
                    return parse_tagged_line(line)
                except UnicodeDecodeError:
                    print("[CMS1] discarded serial frame with invalid UTF-8")
                    return (None, None, None)

        return None, None, None


def _close_serial(ser):
    if ser is None:
        return
    try:
        ser.close()
    except Exception as exc:
        print(f"[CMS1] serial close error: {exc}")


def main():
    global _radio_stats_requested
    _install_radio_stats_handler()
    init_database()
    print(f"[CMS1] listening on {SERIAL_PORT}")
    sync_publisher = SyncPublisher()
    ser = None
    frame_reader = SerialFrameReader()
    stats_response_deadline = None
    reconnect_attempt = 0
    reconnect_delay = SERIAL_RECONNECT_INITIAL_SEC
    reconnect_at = time.monotonic()

    while True:
        if ser is None:
            wait = reconnect_at - time.monotonic()
            if wait > 0:
                time.sleep(wait)
            reconnect_attempt += 1
            print(f"[CMS1] serial reconnect attempt {reconnect_attempt} on {SERIAL_PORT}")
            try:
                ser = serial.Serial(SERIAL_PORT, BAUD, timeout=0.2, write_timeout=2)
                if hasattr(ser, "reset_input_buffer"):
                    ser.reset_input_buffer()
                time.sleep(SERIAL_SETTLE_SEC)
            except SERIAL_EXCEPTIONS as exc:
                _close_serial(ser)
                ser = None
                frame_reader.reset()
                reconnect_at = time.monotonic() + reconnect_delay
                print(f"[CMS1] serial reconnect failed: {exc}; retry in "
                      f"{reconnect_delay}s")
                reconnect_delay = min(reconnect_delay * 2, SERIAL_RECONNECT_MAX_SEC)
                continue
            print("[CMS1] serial connected")
            reconnect_attempt = 0
            reconnect_delay = SERIAL_RECONNECT_INITIAL_SEC
            frame_reader.reset()

        try:
            if _radio_stats_requested and sync_publisher.transfer_id is None:
                ser.write(b"STATS?\n")
                _radio_stats_requested = False
                stats_response_deadline = time.monotonic() + 2.0
                print("[CMS1] radio stats query sent")
            if (stats_response_deadline is not None and
                    time.monotonic() >= stats_response_deadline):
                print("[CMS1] radio stats query timed out")
                stats_response_deadline = None
            ch, payload, radio_metrics = frame_reader.read_tagged(ser)
            if ch is not None:
                pkt = json.loads(payload)
                if ch == 3:
                    stats_response_deadline = None
                    if isinstance(pkt, dict) and pkt.get("type") == "RADIO_STATS":
                        print("[CMS1] radio stats " + json.dumps(pkt, sort_keys=True))
                    else:
                        print("[CMS1] ignored invalid radio stats frame")
                elif ch == 2:
                    sync_publisher.handle_ack(payload)
                elif ch == 1 and pkt.get("type") == "VERIFY_REQ":
                    gate_id = pkt.get("gate", "UNKNOWN")
                    boot_id, sequence = pkt.get("boot"), pkt.get("seq")
                    valid_identity = (
                        type(boot_id) is int and 0 <= boot_id <= 0xFFFFFFFF and
                        type(sequence) is int and 0 <= sequence <= 0xFFFF)
                    if not isinstance(gate_id, str) or not gate_id or not valid_identity:
                        print("[CMS1] rejected VERIFY_REQ with missing or invalid identity")
                        continue
                    request_started = time.perf_counter()
                    resp = verify_and_authorize(
                        pkt.get("payload", ""), gate_id, (boot_id, sequence), radio_metrics)
                    resp = attach_request_identity(resp, pkt)
                    response_bytes = serialize_verify_response(resp, gate_id)
                    resp = json.loads(response_bytes)
                    ser.write(b"C1:" + response_bytes + b"\n")
                    metrics = radio_metrics or {}
                    print(f"[METRICS] {gate_id} RSSI={metrics.get('rssi_dbm', 'n/a')} dBm "
                          f"SNR={metrics.get('snr_db', 'n/a')} dB "
                          f"Latency={(time.perf_counter() - request_started) * 1000:.1f} ms "
                          f"Response={resp['response_time_ms']} ms")
                    print(f"[CMS1] {gate_id} granted={resp['granted']} "
                          f"action={resp['action']} reason={resp['reason']}")
            sync_publisher.poll(ser)
        except SERIAL_EXCEPTIONS as exc:
            print(f"[CMS1] serial disconnected: {exc}")
            _close_serial(ser)
            ser = None
            frame_reader.reset()
            reconnect_at = time.monotonic() + reconnect_delay
        except json.JSONDecodeError as exc:
            print(f"[CMS1] discarded invalid JSON serial frame: {exc}")
            continue
        except Exception as e:
            print(f"[CMS1] error: {e}")
            time.sleep(1)

if __name__ == "__main__":
    while True:
        try: main()
        except Exception as e:
            print(f"[CMS1] restart in 10s: {e}"); time.sleep(10)