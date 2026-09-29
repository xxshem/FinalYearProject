#!/usr/bin/env python3
"""Build and send acknowledged CMS1-to-CMS2 database transfers."""
import base64
import hashlib
import json
import os
import sqlite3
import sys
import time
import uuid
from datetime import datetime

VVS_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, VVS_DIR)
from scripts.config import DB_PATH
from scripts.offline_queue import mark_sent, pending

INTERVAL_SEC = 30
FRAME_INTERVAL_SEC = 0.5
ACK_TIMEOUT_SEC = 15
CHUNK_BYTES = 100
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
        self.watermark = "1970-01-01 00:00:00"
        self.next_poll = 0.0
        self.last_frame = 0.0
        self.frames = []
        self.frame_index = 0
        self.transfer_id = None
        self.candidate_watermark = None
        self.queue_ids = []
        self.awaiting_ack = False
        self.ack_deadline = 0.0
        self.retries = 0

    def _build_transfer(self, now):
        self.candidate_watermark = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        tables = dump_new(self.watermark, self.db_path)
        if not tables:
            self.next_poll = now + INTERVAL_SEC
            return

        blob = json.dumps(tables, separators=(",", ":"), default=str).encode("utf-8")
        transfer_id = uuid.uuid4().hex
        chunks = [blob[offset:offset + CHUNK_BYTES]
                  for offset in range(0, len(blob), CHUNK_BYTES)]
        frames = [{"type": "SYNC_BEGIN", "id": transfer_id, "size": len(blob),
                   "chunks": len(chunks), "sha256": hashlib.sha256(blob).hexdigest()}]
        frames.extend({"type": "SYNC_CHUNK", "id": transfer_id, "index": index,
                       "data": base64.b64encode(chunk).decode("ascii")}
                      for index, chunk in enumerate(chunks))
        frames.append({"type": "SYNC_END", "id": transfer_id})

        self.frames = frames
        self.frame_index = 0
        self.transfer_id = transfer_id
        self.queue_ids = [row["queue_id"] for row in pending()]
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

        if now - self.last_frame < FRAME_INTERVAL_SEC:
            return
        frame = self.frames[self.frame_index]
        is_end = frame["type"] == "SYNC_END"
        prefix = "C2W:" if is_end else "C2:"
        ser.write((prefix + json.dumps(frame, separators=(",", ":")) + "\n").encode())
        self.last_frame = now
        self.frame_index += 1
        if is_end:
            self.awaiting_ack = True
            self.ack_deadline = now + ACK_TIMEOUT_SEC

    def handle_ack(self, message):
        if message.get("type") != "SYNC_ACK" or message.get("id") != self.transfer_id:
            return False
        if message.get("ok") is True:
            for queue_id in self.queue_ids:
                mark_sent(queue_id)
            self.watermark = self.candidate_watermark
            print(f"[SYNC-TX] acknowledged {self.transfer_id}")
        else:
            print(f"[SYNC-TX] receiver rejected {self.transfer_id}: {message.get('error', '')}")
        self._clear_transfer(time.monotonic())
        return True

    def _clear_transfer(self, now):
        self.frames = []
        self.frame_index = 0
        self.transfer_id = None
        self.candidate_watermark = None
        self.queue_ids = []
        self.awaiting_ack = False
        self.next_poll = now + INTERVAL_SEC