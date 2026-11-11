#!/usr/bin/env python3
"""Build and send acknowledged CMS1-to-CMS2 database transfers."""
import base64
import hashlib
import json
import math
import os
import sqlite3
import sys
import time
import uuid

VVS_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, VVS_DIR)
from scripts.config import DB_PATH
from scripts.init_db import SYNC_TABLES, init_sync_tracking

INTERVAL_SEC = 30
CHUNK_BYTES = 100
MAX_BATCH_BYTES = 4 * 1024
MAX_BATCH_EVENTS = 256
MAX_CHUNKS = (MAX_BATCH_BYTES + CHUNK_BYTES - 1) // CHUNK_BYTES
MAX_LORA_PAYLOAD_BYTES = 255
BAUD = 115200
SF = 7
BANDWIDTH_HZ = 125000
CODING_RATE_INDEX = 1
PREAMBLE_SYMBOLS = 8
CRC_ENABLED = True
IMPLICIT_HEADER = False
FIRMWARE_ACK_WINDOW_SEC = 1
SERIAL_REPLY_MARGIN_SEC = 0.75
FRAME_GUARD_SEC = 0.25
TABLES = {
    "vehicles": "updated_at",
    "gates": "updated_at",
    "verification_logs": "scan_time",
    "entry_exit": "updated_at",
    "lora_metrics": "timestamp",
    "operational_metrics": "timestamp",
    "power_metrics": "timestamp",
    "system_events": "timestamp",
}
PRIMARY_KEYS = SYNC_TABLES


def lora_airtime_seconds(payload_bytes):
    """Estimate airtime for the explicitly configured SX127x sync profile."""
    if not 0 <= payload_bytes <= MAX_LORA_PAYLOAD_BYTES:
        raise ValueError("LoRa payload must be between 0 and 255 bytes")

    low_data_rate_optimization = int((2 ** SF) / BANDWIDTH_HZ > 0.016)
    crc_bits = 16 if CRC_ENABLED else 0
    implicit_header = int(IMPLICIT_HEADER)
    numerator = (8 * payload_bytes - 4 * SF + 28 + crc_bits
                 - 20 * implicit_header)
    denominator = 4 * (SF - 2 * low_data_rate_optimization)
    encoded_groups = max(0, math.ceil(numerator / denominator))
    payload_symbols = 8 + encoded_groups * (CODING_RATE_INDEX + 4)
    symbol_seconds = (2 ** SF) / BANDWIDTH_HZ
    return (PREAMBLE_SYMBOLS + 4.25 + payload_symbols) * symbol_seconds


def dump_new(since, db_path=DB_PATH):
    connection = sqlite3.connect(db_path)
    connection.row_factory = sqlite3.Row
    try:
        result = {}
        for table, timestamp_column in TABLES.items():
            rows = [dict(row) for row in connection.execute(
                f"SELECT * FROM {table} WHERE {timestamp_column} >= ?",
                (since,))]
            if rows:
                result[table] = rows
        return result
    finally:
        connection.close()


class SyncPublisher:
    """Send one small radio frame per poll so CH1 can resume between frames."""

    def __init__(self, db_path=DB_PATH):
        self.db_path = db_path
        self.watermark = self._read_checkpoint()
        self._sync_tracking_ready = False
        self.next_poll = 0.0
        self.next_frame_at = 0.0
        self.frames = []
        self.frame_index = 0
        self.transfer_id = None
        self.candidate_seq = None
        self.candidate_keys = []
        self.awaiting_ack = False
        self.ack_deadline = 0.0
        self.retries = 0

    def _read_checkpoint(self):
        if not os.path.exists(self.db_path):
            return 0
        connection = sqlite3.connect(self.db_path)
        try:
            exists = connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='sync_state'"
            ).fetchone()
            if not exists:
                return 0
            row = connection.execute(
                "SELECT value FROM sync_state WHERE key='acked_seq'"
            ).fetchone()
            return int(row[0]) if row else 0
        finally:
            connection.close()

    @staticmethod
    def _serialize_batch(connection, after_seq, through_seq, changes):
        payload = {"_sync": {
            "schema": 1,
            "after_seq": after_seq,
            "through_seq": through_seq,
        }}
        deletes = {}
        rows_by_table = {}
        for (table, row_key), operation in changes.items():
            if operation == "delete":
                deletes.setdefault(table, []).append(
                    _database_key(table, row_key))
                continue
            primary_key = PRIMARY_KEYS[table]
            row = connection.execute(
                f'SELECT * FROM "{table}" WHERE "{primary_key}"=?',
                (row_key,)).fetchone()
            if row is None:
                deletes.setdefault(table, []).append(
                    _database_key(table, row_key))
                continue
            rows_by_table.setdefault(table, []).append(dict(row))
        for table in TABLES:
            if table in rows_by_table:
                payload[table] = rows_by_table[table]
        if deletes:
            payload["_deletes"] = deletes
        return payload

    def _record_oversized_row(self, table, row_key, byte_size):
        message = (f"unsynchronized {table} primary key {row_key}: "
                   f"serialized change exceeds {MAX_BATCH_BYTES} bytes "
                   f"({byte_size} bytes)")
        connection = sqlite3.connect(self.db_path)
        try:
            connection.execute("""
                INSERT INTO sync_errors (table_name, row_key, message,
                                          created_at, resolved_at)
                VALUES (?, ?, ?, CURRENT_TIMESTAMP, NULL)
                ON CONFLICT(table_name, row_key) DO UPDATE SET
                    message=excluded.message,
                    created_at=CURRENT_TIMESTAMP,
                    resolved_at=NULL
            """, (table, str(row_key), message))
            connection.commit()
        finally:
            connection.close()
        print(f"[SYNC-TX][ERROR] {message}; no watermark advanced")

    def _next_batch(self):
        if not self._sync_tracking_ready:
            init_sync_tracking(self.db_path)
            self.watermark = self._read_checkpoint()
            self._sync_tracking_ready = True
        connection = sqlite3.connect(self.db_path)
        connection.row_factory = sqlite3.Row
        changes = {}
        accepted_seq = None
        accepted_blob = None
        accepted_keys = []
        accepted_events = 0
        oversized = None
        result = None
        try:
            connection.execute("BEGIN")
            rows = connection.execute("""
                SELECT seq, table_name, row_key, operation
                FROM sync_change_log
                WHERE seq > ?
                ORDER BY seq
                LIMIT ?
            """, (self.watermark, MAX_BATCH_EVENTS))
            for entry in rows:
                table = entry["table_name"]
                row_key = entry["row_key"]
                key = (table, row_key)
                if table not in PRIMARY_KEYS:
                    raise ValueError(f"unknown table in sync journal: {table}")
                previous = changes.get(key)
                changes[key] = entry["operation"]
                payload = self._serialize_batch(
                    connection, self.watermark, entry["seq"], changes)
                blob = json.dumps(
                    payload, separators=(",", ":"), default=str
                ).encode("utf-8")
                if len(blob) > MAX_BATCH_BYTES:
                    changes.pop(key, None)
                    if previous is not None:
                        changes[key] = previous
                    if accepted_events == 0:
                        if entry["operation"] == "upsert":
                            oversized = (table, row_key, len(blob))
                        break
                    break
                accepted_seq = entry["seq"]
                accepted_blob = blob
                accepted_keys = list(changes)
                accepted_events += 1
            if accepted_seq is not None:
                result = (accepted_seq, accepted_blob, accepted_keys)
        finally:
            connection.rollback()
            connection.close()
        if oversized:
            self._record_oversized_row(*oversized)
        return result

    def _build_transfer(self, now):
        batch = self._next_batch()
        if batch is None:
            self.next_poll = now + INTERVAL_SEC
            return

        self.candidate_seq, blob, self.candidate_keys = batch
        transfer_id = uuid.uuid4().hex
        chunks = [blob[offset:offset + CHUNK_BYTES]
                  for offset in range(0, len(blob), CHUNK_BYTES)]
        if len(chunks) > MAX_CHUNKS:
            raise ValueError(
                f"batch invariant violated: {len(chunks)} exceeds "
                f"{MAX_CHUNKS} chunks")
        frames = [{"type": "SYNC_BEGIN", "id": transfer_id, "size": len(blob),
                   "chunks": len(chunks), "sha256": hashlib.sha256(blob).hexdigest()}]
        frames.extend({"type": "SYNC_CHUNK", "id": transfer_id, "index": index,
                       "data": base64.b64encode(chunk).decode("ascii")}
                      for index, chunk in enumerate(chunks))
        frames.append({"type": "SYNC_END", "id": transfer_id})

        self.frames = frames
        self.frame_index = 0
        self.transfer_id = transfer_id
        self.awaiting_ack = False
        self.retries = 0
        self.next_poll = now + INTERVAL_SEC
        print(f"[SYNC-TX] transfer {transfer_id}: {len(blob)} bytes, {len(chunks)} chunks")

    def poll(self, ser):
        now = time.monotonic()
        if self.awaiting_ack:
            if now < self.ack_deadline:
                return
            if self.retries >= 3:
                print(f"[SYNC-TX] transfer {self.transfer_id} not acknowledged")
                self._clear_transfer(now)
                return
            self.retries += 1
            self.frame_index = 0
            self.awaiting_ack = False
            print(f"[SYNC-TX] retry {self.retries} for {self.transfer_id}")

        if not self.frames:
            if now >= self.next_poll:
                self._build_transfer(now)
            if not self.frames:
                return

        if now < self.next_frame_at:
            return
        frame = self.frames[self.frame_index]
        is_end = frame["type"] == "SYNC_END"
        prefix = "C2W:" if is_end else "C2:"
        radio_payload = json.dumps(frame, separators=(",", ":")).encode("utf-8")
        if len(radio_payload) > MAX_LORA_PAYLOAD_BYTES:
            raise ValueError(f"serialized LoRa frame is too large: {len(radio_payload)} bytes")
        command = (prefix.encode("ascii") + radio_payload + b"\n")
        ser.write(command)
        serial_seconds = len(command) * 10 / BAUD
        radio_seconds = lora_airtime_seconds(len(radio_payload))
        self.next_frame_at = now + serial_seconds + radio_seconds + FRAME_GUARD_SEC
        self.frame_index += 1
        if is_end:
            self.awaiting_ack = True
            self.ack_deadline = (now + serial_seconds + radio_seconds
                                 + FIRMWARE_ACK_WINDOW_SEC
                                 + SERIAL_REPLY_MARGIN_SEC)

    def handle_ack(self, message):
        received_raw_json = isinstance(message, str)
        if isinstance(message, str):
            try:
                if len(message.encode("utf-8")) > MAX_LORA_PAYLOAD_BYTES:
                    return False
            except UnicodeEncodeError:
                return False
            try:
                message = json.loads(message, object_pairs_hook=_json_object)
            except (json.JSONDecodeError, ValueError):
                return False
        if not isinstance(message, dict):
            return False
        if getattr(message, "duplicate_keys", set()).intersection(
                {"type", "id"}):
            return False
        if (self.transfer_id is not None
                and message.get("type") in ("SYNC_TX_ERROR", "SYNC_TIMEOUT")
                and message.get("id") == self.transfer_id):
            print(f"[SYNC-TX] gateway radio failure for {self.transfer_id}; retrying")
            self.awaiting_ack = True
            self.ack_deadline = time.monotonic()
            self.next_frame_at = self.ack_deadline
            return True
        if not self.awaiting_ack or self.transfer_id is None:
            return False
        if message.get("type") == "SYNC_ACK" and not received_raw_json:
            return False
        ack_result = parse_sync_ack(message, self.transfer_id)
        if ack_result == "positive":
            self._persist_checkpoint(self.candidate_seq, self.candidate_keys)
            self.watermark = self.candidate_seq
            print(f"[SYNC-TX] acknowledged {self.transfer_id}")
        elif ack_result == "negative":
            print(f"[SYNC-TX] receiver rejected {self.transfer_id}: {message.get('error', '')}")
            self.awaiting_ack = True
            self.ack_deadline = time.monotonic()
            self.next_frame_at = self.ack_deadline
            return True
        else:
            return False
        self._clear_transfer(time.monotonic())
        return True

    def _clear_transfer(self, now):
        self.frames = []
        self.frame_index = 0
        self.transfer_id = None
        self.candidate_seq = None
        self.candidate_keys = []
        self.awaiting_ack = False
        self.next_poll = now + INTERVAL_SEC

    def _persist_checkpoint(self, candidate_seq, candidate_keys):
        if type(candidate_seq) is not int:
            raise ValueError("acknowledged transfer has no valid journal cursor")
        connection = sqlite3.connect(self.db_path)
        try:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT value FROM sync_state WHERE key='acked_seq'"
            ).fetchone()
            current = int(row[0]) if row else 0
            if candidate_seq < current:
                raise ValueError("sync checkpoint would move backwards")
            connection.execute("""
                INSERT INTO sync_state (key, value) VALUES ('acked_seq', ?)
                ON CONFLICT(key) DO UPDATE SET value=excluded.value
            """, (str(candidate_seq),))
            connection.execute(
                "DELETE FROM sync_change_log WHERE seq <= ?", (candidate_seq,))
            connection.executemany("""
                UPDATE sync_errors SET resolved_at=CURRENT_TIMESTAMP
                WHERE table_name=? AND row_key=? AND resolved_at IS NULL
            """, ((table, str(row_key)) for table, row_key in candidate_keys))
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()


class _JSONObject(dict):
    def __init__(self, pairs):
        super().__init__()
        self.duplicate_keys = set()
        for key, value in pairs:
            if key in self:
                self.duplicate_keys.add(key)
            self[key] = value


def _json_object(pairs):
    return _JSONObject(pairs)


def parse_sync_ack(message, expected_transfer_id):
    if isinstance(message, str):
        try:
            if len(message.encode("utf-8")) > MAX_LORA_PAYLOAD_BYTES:
                return "malformed"
        except UnicodeEncodeError:
            return "malformed"
        try:
            message = json.loads(message, object_pairs_hook=_json_object)
        except (json.JSONDecodeError, ValueError):
            return "malformed"
    if not isinstance(message, dict):
        return "malformed"
    if getattr(message, "duplicate_keys", set()).intersection(
            {"type", "id", "ok"}):
        return "malformed"
    if "type" not in message:
        return "unrelated"
    if not isinstance(message["type"], str):
        return "malformed"
    if message["type"] != "SYNC_ACK":
        return "unrelated"
    if ("id" not in message or "ok" not in message
            or not isinstance(message["id"], str)
            or type(message["ok"]) is not bool):
        return "malformed"
    if message["id"] != expected_transfer_id:
        return "wrong_transfer"
    return "positive" if message["ok"] is True else "negative"


def _database_key(table, value):
    if table == "gates":
        return str(value)
    return int(value)