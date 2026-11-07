#!/usr/bin/env python3
"""Receive CMS1 LoRa transfers, reconcile them locally, and acknowledge them."""
import base64
import hashlib
import json
import os
import sys
import time

import serial

VVS_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, VVS_DIR)
from scripts.reconciliation import reconcile_blob

SERIAL_PORT = os.environ.get("VVS_SERIAL_PORT", "").strip()
BAUD = 115200
MAX_BATCH_BYTES = 4 * 1024
MAX_TRANSFER_BYTES = MAX_BATCH_BYTES
CHUNK_BYTES = 100
MAX_CHUNKS = (MAX_BATCH_BYTES + CHUNK_BYTES - 1) // CHUNK_BYTES
SERIAL_ERRORS = (getattr(serial, "SerialException", OSError), OSError)


class SyncReceiver:
    def __init__(self):
        self.transfer = None

    def handle(self, message):
        message_type = message.get("type")
        if message_type == "SYNC_BEGIN":
            transfer_id = message.get("id")
            size = message.get("size")
            chunks = message.get("chunks")
            checksum = message.get("sha256")
            if (not isinstance(transfer_id, str) or not transfer_id
                    or len(transfer_id) > 64
                    or type(size) is not int or not 0 < size <= MAX_TRANSFER_BYTES
                    or type(chunks) is not int
                    or not 0 < chunks <= MAX_CHUNKS
                    or chunks != (size + CHUNK_BYTES - 1) // CHUNK_BYTES
                    or not isinstance(checksum, str) or len(checksum) != 64
                    or any(char not in "0123456789abcdef" for char in checksum)):
                self.transfer = None
                return self._ack(message.get("id"), False, "invalid transfer bounds")
            self.transfer = {"id": transfer_id, "size": size,
                             "chunks": chunks, "sha256": checksum,
                             "parts": {}, "received_bytes": 0}
            return None

        if not self.transfer or message.get("id") != self.transfer["id"]:
            return None

        if message_type == "SYNC_CHUNK":
            index = message.get("index")
            if not isinstance(index, int) or not 0 <= index < self.transfer["chunks"]:
                return self._ack(self.transfer["id"], False, "invalid chunk index")
            try:
                encoded = message["data"]
                if not isinstance(encoded, str):
                    raise ValueError("chunk data must be a string")
                part = base64.b64decode(encoded, validate=True)
                if len(part) > CHUNK_BYTES:
                    raise ValueError("chunk exceeds declared chunk size")
            except (KeyError, TypeError, ValueError):
                return self._ack(self.transfer["id"], False, "invalid chunk encoding")
            old_part = self.transfer["parts"].get(index, b"")
            received_bytes = (self.transfer["received_bytes"] - len(old_part)
                              + len(part))
            if received_bytes > self.transfer["size"]:
                return self._ack(self.transfer["id"], False, "chunk data exceeds declared size")
            self.transfer["parts"][index] = part
            self.transfer["received_bytes"] = received_bytes
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
                data = json.loads(
                    blob.decode("utf-8"), object_pairs_hook=_unique_object)
                if not isinstance(data, dict):
                    raise ValueError("transfer root must be an object")
                report = reconcile_blob(data)
            except Exception as error:
                return self._ack(transfer["id"], False, str(error))
            summary = ", ".join(
                f"{table}:inserted={counts[0]} updated={counts[1]} kept={counts[2]}"
                for table, counts in report.items())
            print(f"[SYNC-RX] reconciled {transfer['id']}: {summary}")
            return self._ack(transfer["id"], True)
        return None

    @staticmethod
    def _ack(transfer_id, ok, error=""):
        ack = {"type": "SYNC_ACK", "id": transfer_id, "ok": ok}
        if error:
            safe_error = error.encode("ascii", "replace").decode("ascii")
            safe_error = "".join(
                character if 0x20 <= ord(character) <= 0x7e else "?"
                for character in safe_error)[:80]
            ack["error"] = safe_error
        return ack


def main():
    receiver = SyncReceiver()
    if not SERIAL_PORT:
        raise SystemExit("[SYNC-RX] VVS_SERIAL_PORT must be configured")

    retry_delay = 1
    while True:
        try:
            with serial.Serial(SERIAL_PORT, BAUD, timeout=1) as ser:
                print(f"[SYNC-RX] connected to {SERIAL_PORT} at {BAUD} baud")
                while True:
                    try:
                        raw_bytes = ser.readline()
                        if raw_bytes:
                            retry_delay = 1
                            _process_serial_line(raw_bytes, receiver, ser)
                    except SERIAL_ERRORS as error:
                        print(f"[SYNC-RX] serial disconnected: {error}")
                        break
        except SERIAL_ERRORS as error:
            print(f"[SYNC-RX] cannot open {SERIAL_PORT}: {error}")

        print(f"[SYNC-RX] retrying serial connection in {retry_delay}s")
        time.sleep(retry_delay)
        retry_delay = min(retry_delay * 2, 30)


def _process_serial_line(raw_bytes, receiver, ser):
    try:
        raw = raw_bytes.decode("utf-8").strip()
    except UnicodeDecodeError:
        print(f"[SYNC-RX] ignored non-UTF-8 serial frame: {len(raw_bytes)} bytes")
        return
    if not raw:
        return
    if raw.startswith("C2:"):
        raw = raw[3:]
    try:
        message = json.loads(raw, object_pairs_hook=_unique_object)
    except (json.JSONDecodeError, ValueError):
        print(f"[SYNC-RX] ignored malformed frame: {len(raw_bytes)} bytes")
        return
    if not isinstance(message, dict):
        print(f"[SYNC-RX] ignored non-object JSON frame: {len(raw_bytes)} bytes")
        return

    message_type = message.get("type")
    transfer_id = message.get("id")
    safe_type = repr(message_type[:32]) if isinstance(message_type, str) else "<invalid>"
    safe_id = repr(transfer_id[:64]) if isinstance(transfer_id, str) else "<invalid>"
    details = ""
    if message_type == "SYNC_BEGIN":
        details = f" size={message.get('size')} chunks={message.get('chunks')}"
    elif message_type == "SYNC_CHUNK":
        data = message.get("data")
        encoded_size = len(data) if isinstance(data, str) else 0
        details = f" chunk={message.get('index')} encoded_bytes={encoded_size}"
    print(f"[SYNC-RX] received type={safe_type} id={safe_id}{details}")
    ack = receiver.handle(message)
    if ack:
        ser.write(("C2:" + json.dumps(
            ack, separators=(",", ":"), ensure_ascii=True) + "\n").encode())
        ack_id = ack.get("id")
        safe_ack_id = repr(ack_id[:64]) if isinstance(ack_id, str) else "<invalid>"
        print(f"[SYNC-RX] queued ACK id={safe_ack_id} ok={ack['ok']}")


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


if __name__ == "__main__":
    main()