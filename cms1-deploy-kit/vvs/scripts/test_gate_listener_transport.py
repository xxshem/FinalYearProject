import json
import os
import sqlite3
import sys
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lora"))

try:
    import serial
except ImportError:
    serial = None

if serial is None:
    sys.modules["serial"] = type("serial", (), {})()

import gate_listener
from gate_listener import read_tagged
import init_db
import lora_sync_client
import lora_sync_server
from lora_sync_server import dump_new
from qr_handler import QRHandler


class SerialLine:
    def __init__(self, line):
        self.line = line.encode()

    def readline(self):
        return self.line


class GateListenerTransportTests(unittest.TestCase):
    def test_reads_channel_one_payload_with_rssi_and_snr(self):
        expected = {"type": "VERIFY_REQ", "gate": "GATE_A", "token": "abc"}
        line = "C1:-112,7.5:" + json.dumps(expected) + "\n"

        channel, payload = read_tagged(SerialLine(line))

        self.assertEqual(channel, 1)
        self.assertEqual(json.loads(payload), expected)

    def test_reads_documented_channel_one_payload_without_metrics(self):
        expected = {"type": "VERIFY_REQ", "gate": "GATE_A", "token": "abc"}
        line = "C1:" + json.dumps(expected) + "\n"

        channel, payload = read_tagged(SerialLine(line))

        self.assertEqual(channel, 1)
        self.assertEqual(json.loads(payload), expected)

    def test_preserves_channel_two_payload(self):
        channel, payload = read_tagged(SerialLine("C2:{\"type\":\"SYNC_BEGIN\"}\n"))

        self.assertEqual(channel, 2)
        self.assertEqual(payload, '{"type":"SYNC_BEGIN"}')


class GateAuthorizationTests(unittest.TestCase):
    def setUp(self):
        gate_listener.REQUEST_CACHE.clear()
        self.previous_secret = os.environ.get("VVS_QR_SECRET")
        os.environ["VVS_QR_SECRET"] = "test-only-qr-secret"
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "vvs.db"
        schema_path = Path(__file__).resolve().parents[1] / "database" / "schema.sql"
        connection = sqlite3.connect(self.db_path)
        try:
            connection.executescript(schema_path.read_text(encoding="utf-8"))
            today = datetime.now().date()
            connection.execute("""INSERT INTO vehicles
                (vid, owner_name, registration_no, vehicle_type, qr_token,
                 valid_from, valid_to, is_active)
                VALUES (?, ?, ?, ?, ?, ?, ?, 1)""",
                ("vehicle-1", "Test Owner", "TEST-001", "car", "token-1",
                 today.isoformat(), (today + timedelta(days=1)).isoformat()))
            connection.commit()
        finally:
            connection.close()

        self.previous_db_path = gate_listener.DB_PATH
        self.previous_enqueue = gate_listener.enqueue
        gate_listener.DB_PATH = str(self.db_path)
        gate_listener.enqueue = lambda *args, **kwargs: None
        self.payload = "vehicle-1|token-1|" + QRHandler().generate_signature(
            "vehicle-1", "token-1")

    def tearDown(self):
        gate_listener.DB_PATH = self.previous_db_path
        gate_listener.enqueue = self.previous_enqueue
        self.temp_dir.cleanup()
        if self.previous_secret is None:
            os.environ.pop("VVS_QR_SECRET", None)
        else:
            os.environ["VVS_QR_SECRET"] = self.previous_secret

    def test_rejects_invalid_signature(self):
        result = gate_listener.verify_and_authorize(
            "vehicle-1|token-1|invalid", "GATE_A")

        self.assertFalse(result["granted"])
        self.assertEqual(result["reason"], "invalid QR signature")

    def test_same_sequence_retry_returns_original_decision(self):
        first = gate_listener.verify_and_authorize(self.payload, "GATE_A", "GATE_A:7")
        retry = gate_listener.verify_and_authorize(self.payload, "GATE_A", "GATE_A:7")

        self.assertEqual(first, retry)
        connection = sqlite3.connect(self.db_path)
        try:
            self.assertEqual(connection.execute(
                "SELECT COUNT(*) FROM entry_exit").fetchone()[0], 1)
            self.assertEqual(connection.execute(
                "SELECT COUNT(*) FROM verification_logs").fetchone()[0], 1)
        finally:
            connection.close()

    def test_detects_clone_then_allows_exit_and_reentry(self):
        first = gate_listener.verify_and_authorize(self.payload, "GATE_A")
        clone = gate_listener.verify_and_authorize(self.payload, "GATE_B")
        self.assertTrue(first["granted"])
        self.assertEqual(first["action"], "entry")
        self.assertFalse(clone["granted"])
        self.assertIn("clone", clone["reason"])

        old_time = (datetime.now() - timedelta(seconds=10)).isoformat(sep=" ",
                                                                       timespec="seconds")
        connection = sqlite3.connect(self.db_path)
        try:
            connection.execute("UPDATE verification_logs SET scan_time=? WHERE gate_id='GATE_A'",
                               (old_time,))
            connection.commit()
        finally:
            connection.close()

        exit_result = gate_listener.verify_and_authorize(self.payload, "GATE_B")
        reentry = gate_listener.verify_and_authorize(self.payload, "GATE_C")
        self.assertTrue(exit_result["granted"])
        self.assertEqual(exit_result["action"], "exit")
        self.assertTrue(reentry["granted"])
        self.assertEqual(reentry["action"], "entry")

        connection = sqlite3.connect(self.db_path)
        try:
            row = connection.execute("""SELECT entry_gate, exit_gate, exit_time
                FROM entry_exit ORDER BY record_id LIMIT 1""").fetchone()
        finally:
            connection.close()
        self.assertEqual(row[0:2], ("GATE_A", "GATE_B"))
        self.assertIsNotNone(row[2])


class DatabaseMigrationTests(unittest.TestCase):
    def test_adds_sync_timestamps_to_legacy_database(self):
        with tempfile.TemporaryDirectory() as directory:
            db_path = Path(directory) / "vvs.db"
            connection = sqlite3.connect(db_path)
            try:
                connection.executescript("""
                    CREATE TABLE vehicles (
                        vehicle_id INTEGER PRIMARY KEY, vid TEXT, owner_name TEXT,
                        registration_no TEXT, vehicle_type TEXT, qr_token TEXT,
                        valid_from DATE, valid_to DATE, is_active INTEGER,
                        created_at TIMESTAMP
                    );
                    CREATE TABLE gates (
                        gate_id TEXT PRIMARY KEY, location TEXT, description TEXT,
                        status TEXT, last_seen TIMESTAMP, created_at TIMESTAMP
                    );
                    CREATE TABLE entry_exit (
                        record_id INTEGER PRIMARY KEY, vehicle_id INTEGER,
                        entry_gate TEXT, entry_time TIMESTAMP, exit_gate TEXT,
                        exit_time TIMESTAMP, duration_minutes REAL
                    );
                """)
            finally:
                connection.close()

            previous_db, previous_schema = init_db.DB, init_db.SCHEMA
            init_db.DB = str(db_path)
            init_db.SCHEMA = str(Path(__file__).resolve().parents[1] / "database" / "schema.sql")
            try:
                init_db.init()
            finally:
                init_db.DB, init_db.SCHEMA = previous_db, previous_schema

            connection = sqlite3.connect(db_path)
            try:
                vehicle_columns = {row[1] for row in connection.execute(
                    "PRAGMA table_info(vehicles)")}
                gate_columns = {row[1] for row in connection.execute(
                    "PRAGMA table_info(gates)")}
                entry_exit_columns = {row[1] for row in connection.execute(
                    "PRAGMA table_info(entry_exit)")}
                triggers = {row[0] for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type='trigger'")}
            finally:
                connection.close()

            self.assertIn("updated_at", vehicle_columns)
            self.assertIn("updated_at", gate_columns)
            self.assertIn("updated_at", entry_exit_columns)
            self.assertEqual(len(triggers), 6)


class SyncProtocolTests(unittest.TestCase):
    def test_reassembles_chunks_in_index_order_and_acknowledges(self):
        blob = b'{"vehicles":[]}'
        transfer_id = "transfer-1"
        receiver = lora_sync_client.SyncReceiver()
        begin = {"type": "SYNC_BEGIN", "id": transfer_id, "size": len(blob),
                 "chunks": 2, "sha256": __import__("hashlib").sha256(blob).hexdigest()}
        self.assertIsNone(receiver.handle(begin))
        self.assertIsNone(receiver.handle({"type": "SYNC_CHUNK", "id": transfer_id,
                                           "index": 1, "data": "W119"}))
        self.assertIsNone(receiver.handle({"type": "SYNC_CHUNK", "id": transfer_id,
                                           "index": 0, "data": "eyJ2ZWhpY2xlcyI6"}))

        with patch.object(lora_sync_client, "reconcile_blob",
                          return_value={
                              "vehicles": (1, 0, 0), "gates": (1, 0, 0),
                              "verification_logs": (1, 0, 0), "entry_exit": (1, 0, 0),
                              "lora_metrics": (1, 0, 0),
                              "operational_metrics": (1, 0, 0),
                              "power_metrics": (1, 0, 0), "system_events": (1, 0, 0),
                          }) as reconcile:
            ack = receiver.handle({"type": "SYNC_END", "id": transfer_id})

        self.assertTrue(ack["ok"])
        self.assertLessEqual(len(json.dumps(ack, separators=(",", ":")).encode()), 255)
        reconcile.assert_called_once_with({"vehicles": []})

    def test_rejects_checksum_mismatch(self):
        receiver = lora_sync_client.SyncReceiver()
        receiver.handle({"type": "SYNC_BEGIN", "id": "bad", "size": 2,
                         "chunks": 1, "sha256": "0" * 64})
        receiver.handle({"type": "SYNC_CHUNK", "id": "bad", "index": 0,
                         "data": "e30="})

        ack = receiver.handle({"type": "SYNC_END", "id": "bad"})

        self.assertFalse(ack["ok"])
        self.assertEqual(ack["error"], "checksum mismatch")

    def test_sync_query_uses_each_table_timestamp_column(self):
        with tempfile.TemporaryDirectory() as directory:
            db_path = Path(directory) / "vvs.db"
            schema_path = Path(__file__).resolve().parents[1] / "database" / "schema.sql"
            connection = sqlite3.connect(db_path)
            try:
                connection.executescript(schema_path.read_text(encoding="utf-8"))
                connection.execute("""INSERT INTO verification_logs
                    (gate_id, qr_token, scan_time, verification_result)
                    VALUES ('GATE_A', 'sync-token', CURRENT_TIMESTAMP, 'granted')""")
                connection.execute("""INSERT INTO entry_exit
                    (vehicle_id, entry_gate, entry_time)
                    VALUES (1, 'GATE_A', CURRENT_TIMESTAMP)""")
                connection.commit()
            finally:
                connection.close()

            tables = dump_new("1970-01-01 00:00:00", str(db_path))

        self.assertIn("verification_logs", tables)
        self.assertIn("entry_exit", tables)

    def test_publisher_frames_fit_lora_payload_limit(self):
        with tempfile.TemporaryDirectory() as directory:
            db_path = Path(directory) / "vvs.db"
            schema_path = Path(__file__).resolve().parents[1] / "database" / "schema.sql"
            connection = sqlite3.connect(db_path)
            try:
                connection.executescript(schema_path.read_text(encoding="utf-8"))
                today = datetime.now().date().isoformat()
                connection.execute("""INSERT INTO vehicles
                    (vid, owner_name, registration_no, vehicle_type, qr_token,
                     valid_from, valid_to) VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    ("v" * 32, "Test Owner", "FRAME-001", "car", "t" * 22,
                     today, today))
                connection.commit()
            finally:
                connection.close()

            publisher = lora_sync_server.SyncPublisher(str(db_path))
            with patch.object(lora_sync_server, "pending", return_value=[]):
                publisher._build_transfer(0)

        frame_sizes = [len(json.dumps(frame, separators=(",", ":")).encode())
                       for frame in publisher.frames]
        self.assertTrue(frame_sizes)
        self.assertLessEqual(max(frame_sizes), 255)


if __name__ == "__main__":
    unittest.main()