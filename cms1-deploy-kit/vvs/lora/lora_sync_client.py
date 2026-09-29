#!/usr/bin/env python3
"""Receive CMS1 LoRa transfers, reconcile them locally, and acknowledge them."""
import base64
import hashlib
import json
import os
import sys

import serial

VVS_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, VVS_DIR)
from scripts.reconciliation import reconcile_blob

SERIAL_PORT = os.environ.get("VVS_SERIAL_PORT", "/dev/ttyUSB0")
BAUD = 115200
MAX_TRANSFER_BYTES = 4 * 1024 * 1024
MAX_CHUNKS = 50000


class SyncReceiver:
    def __init__(self):
        self.transfer = None

    def handle(self, message):
        message_type = message.get("type")
        if message_type == "SYNC_BEGIN":
            size = message.get("size")
            chunks = message.get("chunks")
            if (not isinstance(size, int) or not 0 <= size <= MAX_TRANSFER_BYTES
                    or not isinstance(chunks, int) or not 0 <= chunks <= MAX_CHUNKS):
                self.transfer = None
                return self._ack(message.get("id"), False, "invalid transfer bounds")
            self.transfer = {"id": message.get("id"), "size": size,
                             "chunks": chunks, "sha256": message.get("sha256"),
                             "parts": {}}
            return None

        if not self.transfer or message.get("id") != self.transfer["id"]:
            return None

        if message_type == "SYNC_CHUNK":
            index = message.get("index")
            if not isinstance(index, int) or not 0 <= index < self.transfer["chunks"]:
                return self._ack(self.transfer["id"], False, "invalid chunk index")
            try:
                self.transfer["parts"][index] = base64.b64decode(
                    message["data"], validate=True)
            except (KeyError, ValueError):
                return self._ack(self.transfer["id"], False, "invalid chunk encoding")
            return None

        if message_type == "SYNC_END":
            transfer = self.transfer
            self.transfer = None
            try:
                if len(transfer["parts"]) != transfer["chunks"]:
                    raise ValueError("incomplete transfer")
                blob = b"".join(transfer["parts"][index]
                                 for index in range(transfer["chunks"]))
                if len(blob) != transfer["size"]:
                    raise ValueError("size mismatch")
                if hashlib.sha256(blob).hexdigest() != transfer["sha256"]:
                    raise ValueError("checksum mismatch")
                data = json.loads(blob.decode("utf-8"))
                if not isinstance(data, dict):
                    raise ValueError("transfer root must be an object")
                report = reconcile_blob(data)
                summary = ", ".join(
                    f"{table}:inserted={counts[0]} updated={counts[1]} kept={counts[2]}"
                    for table, counts in report.items())
                print(f"[SYNC-RX] reconciled {transfer['id']}: {summary}")
                return self._ack(transfer["id"], True)
            except Exception as error:
                return self._ack(transfer["id"], False, str(error))
        return None

    @staticmethod
    def _ack(transfer_id, ok, error=""):
        ack = {"type": "SYNC_ACK", "id": transfer_id, "ok": ok}
        if error:
            ack["error"] = error
        return ack


def main():
    receiver = SyncReceiver()
    print(f"[SYNC-RX] listening on {SERIAL_PORT}")
    with serial.Serial(SERIAL_PORT, BAUD, timeout=1) as ser:
        while True:
            raw = ser.readline().decode("utf-8", errors="ignore").strip()
            if not raw:
                continue
            if raw.startswith("C2:"):
                raw = raw[3:]
            try:
                message = json.loads(raw)
                if isinstance(message, dict):
                    ack = receiver.handle(message)
                    if ack:
                        ser.write((json.dumps(ack, separators=(",", ":")) + "\n").encode())
                        print(f"[SYNC-RX] {ack['id']} ok={ack['ok']}")
            except json.JSONDecodeError:
                print(f"[SYNC-RX] ignored malformed frame: {raw[:80]}")


if __name__ == "__main__":
    main()