import json
import base64
import hashlib
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
import offline_queue
from scripts import reconciliation
from qr_handler import QRHandler


class SerialLine:
    def __init__(self, line):
        self.line = line.encode()

    def readline(self):
        return self.line


class GateListenerTransportTests(unittest.TestCase):
    def test_response_echoes_request_identity(self):
        response = {"type": "VERIFY_RESP", "granted": True}
        request = {"boot": 1234, "seq": 7}

        correlated = gate_listener.attach_request_identity(response, request)

        self.assertEqual(correlated["boot"], 1234)
        self.assertEqual(correlated["seq"], 7)
        self.assertNotIn("boot", response)

    def test_reads_channel_one_payload_with_rssi_and_snr(self):
        expected = {"type": "VERIFY_REQ", "gate": "GATE_A", "token": "abc"}
        line = "C1:-112,7.5:" + json.dumps(expected) + "\n"

        channel, payload, metrics = read_tagged(SerialLine(line))

        self.assertEqual(channel, 1)
        self.assertEqual(json.loads(payload), expected)
        self.assertEqual(metrics, {"rssi_dbm": -112.0, "snr_db": 7.5})

    def test_reads_documented_channel_one_payload_without_metrics(self):
        expected = {"type": "VERIFY_REQ", "gate": "GATE_A", "token": "abc"}
        line = "C1:" + json.dumps(expected) + "\n"

        channel, payload, metrics = read_tagged(SerialLine(line))

        self.assertEqual(channel, 1)
        self.assertEqual(json.loads(payload), expected)
        self.assertIsNone(metrics)

    def test_preserves_channel_two_payload(self):
        channel, payload, metrics = read_tagged(SerialLine("C2:{\"type\":\"SYNC_BEGIN\"}\n"))

        self.assertEqual(channel, 2)
        self.assertEqual(payload, '{"type":"SYNC_BEGIN"}')
        self.assertIsNone(metrics)


class ReconciliationAtomicityTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "vvs.db"
        schema_path = Path(__file__).resolve().parents[1] / "database" / "schema.sql"
        connection = sqlite3.connect(self.db_path)
        try:
            connection.executescript(schema_path.read_text(encoding="utf-8"))
            connection.commit()
        finally:
            connection.close()
        self.previous_db = reconciliation.DB
        self.previous_queue_db = offline_queue.DB
        reconciliation.DB = str(self.db_path)
        offline_queue.DB = str(self.db_path)

    def tearDown(self):
        reconciliation.DB = self.previous_db
        offline_queue.DB = self.previous_queue_db
        self.temp_dir.cleanup()

    @staticmethod
    def vehicle_row(vehicle_id=501):
        return {
            "vehicle_id": vehicle_id,
            "vid": f"sync-vehicle-{vehicle_id}",
            "owner_name": "Sync Test",
            "registration_no": f"SYNC-{vehicle_id}",
            "vehicle_type": "car",
            "qr_token": f"sync-token-{vehicle_id}",
            "valid_from": "2026-01-01",
            "valid_to": "2027-01-01",
            "is_active": 1,
            "created_at": "2026-11-07 10:00:00",
            "updated_at": "2026-11-07 10:00:00",
        }

    @staticmethod
    def send_to_receiver(receiver, data):
        blob = json.dumps(data, separators=(",", ":")).encode("utf-8")
        chunks = [blob[offset:offset + 100] for offset in range(0, len(blob), 100)]
        transfer_id = "atomicity-test"
        receiver.handle({
            "type": "SYNC_BEGIN", "id": transfer_id, "size": len(blob),
            "chunks": len(chunks), "sha256": hashlib.sha256(blob).hexdigest(),
        })
        for index, chunk in enumerate(chunks):
            receiver.handle({
                "type": "SYNC_CHUNK", "id": transfer_id, "index": index,
                "data": base64.b64encode(chunk).decode("ascii"),
            })
        return receiver.handle({"type": "SYNC_END", "id": transfer_id})

    def test_failed_later_table_rolls_back_earlier_insert_and_nacks(self):
        data = {
            "vehicles": [self.vehicle_row()],
            "entry_exit": [{
                "record_id": 900, "vehicle_id": 999, "entry_gate": "GATE_A",
                "entry_time": "2026-11-07 10:00:00", "exit_gate": None,
                "exit_time": None, "duration_minutes": None,
                "updated_at": "2026-11-07 10:00:00",
            }],
        }

        ack = self.send_to_receiver(lora_sync_client.SyncReceiver(), data)

        self.assertFalse(ack["ok"])
        connection = sqlite3.connect(self.db_path)
        try:
            self.assertEqual(connection.execute(
                "SELECT COUNT(*) FROM vehicles WHERE vehicle_id=501").fetchone()[0], 0)
            self.assertEqual(connection.execute(
                "SELECT COUNT(*) FROM entry_exit WHERE record_id=900").fetchone()[0], 0)
        finally:
            connection.close()

    def test_successful_reconciliation_commits_and_replay_is_idempotent(self):
        data = {"vehicles": [self.vehicle_row()]}
        receiver = lora_sync_client.SyncReceiver()

        first_ack = self.send_to_receiver(receiver, data)
        second_ack = self.send_to_receiver(receiver, data)

        self.assertTrue(first_ack["ok"])
        self.assertTrue(second_ack["ok"])
        connection = sqlite3.connect(self.db_path)
        try:
            self.assertEqual(connection.execute(
                "SELECT COUNT(*) FROM vehicles WHERE vehicle_id=501").fetchone()[0], 1)
        finally:
            connection.close()

    def test_invalid_payload_is_rejected_before_any_write(self):
        with self.assertRaisesRegex(ValueError, "unknown table"):
            reconciliation.reconcile_blob({
                "vehicles": [self.vehicle_row()],
                "not_a_table": [],
            })
        connection = sqlite3.connect(self.db_path)
        try:
            self.assertEqual(connection.execute(
                "SELECT COUNT(*) FROM vehicles").fetchone()[0], 0)
        finally:
            connection.close()

    def test_dry_run_does_not_escalate_uncommitted_clone_alerts(self):
        alerts = []
        reconciliation.reconcile_blob({
            "system_events": [{
                "event_id": 88, "timestamp": "2026-11-07 10:00:00",
                "event_type": "clone_alert", "source": "GATE_A",
                "description": "test alert",
            }],
        }, dry_run=True, alert_callback=alerts.append)

        self.assertEqual(alerts, [])
        connection = sqlite3.connect(self.db_path)
        try:
            self.assertEqual(connection.execute(
                "SELECT COUNT(*) FROM system_events WHERE event_id=88"
            ).fetchone()[0], 0)
        finally:
            connection.close()

    def test_offline_queue_records_local_completion_without_sync_ack(self):
        offline_queue.enqueue(
            "verify", vehicle_id=1, gate_id="GATE_A", qr_token="local-token",
            payload={"result": "granted"})

        connection = sqlite3.connect(self.db_path)
        try:
            status = connection.execute(
                "SELECT status FROM offline_queue WHERE qr_token='local-token'"
            ).fetchone()[0]
            self.assertEqual(status, "done")
        finally:
            connection.close()

    def test_serial_receiver_retries_open_and_read_failures(self):
        class StopRetry(Exception):
            pass

        class DisconnectedSerial:
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def readline(self):
                raise OSError("USB disconnected")

        with patch.object(lora_sync_client, "SERIAL_PORT", "/dev/serial/by-id/test"):
            with patch.object(
                    lora_sync_client.serial, "Serial",
                    side_effect=[OSError("device absent"), DisconnectedSerial()],
                    create=True) as serial_open:
                with patch.object(
                        lora_sync_client.time, "sleep",
                        side_effect=[None, StopRetry]) as sleep:
                    with self.assertRaises(StopRetry):
                        lora_sync_client.main()

        self.assertEqual(serial_open.call_count, 2)
        self.assertEqual(sleep.call_args.args, (2,))

    def test_serial_receiver_requires_explicit_port_configuration(self):
        with patch.object(lora_sync_client, "SERIAL_PORT", ""):
            with self.assertRaisesRegex(SystemExit, "VVS_SERIAL_PORT"):
                lora_sync_client.main()


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

    def test_verification_persists_radio_and_operational_metrics(self):
        result = gate_listener.verify_and_authorize(
            self.payload, "GATE_A", "GATE_A:telemetry",
            {"rssi_dbm": -85.0, "snr_db": 8.5})

        self.assertTrue(result["granted"])
        connection = sqlite3.connect(self.db_path)
        try:
            radio = connection.execute("""SELECT gate_id, channel, rssi_dbm,
                snr_db, latency_ms FROM lora_metrics""").fetchone()
            operational = connection.execute("""SELECT gate_id, response_time_ms,
                db_query_ms FROM operational_metrics""").fetchone()
        finally:
            connection.close()
        self.assertEqual(radio[:4], ("GATE_A", 1, -85.0, 8.5))
        self.assertGreaterEqual(radio[4], 0)
        self.assertEqual(operational[0], "GATE_A")
        self.assertGreaterEqual(operational[1], 0)
        self.assertGreaterEqual(operational[2], 0)

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
    def test_cms2_mirror_initialization_does_not_create_source_journal(self):
        with tempfile.TemporaryDirectory() as directory:
            previous_db, previous_schema = init_db.DB, init_db.SCHEMA
            init_db.DB = str(Path(directory) / "mirror.db")
            init_db.SCHEMA = str(
                Path(__file__).resolve().parents[1] / "database" / "schema.sql")
            try:
                init_db.init(include_sync_tracking=False)
            finally:
                init_db.DB, init_db.SCHEMA = previous_db, previous_schema

            connection = sqlite3.connect(Path(directory) / "mirror.db")
            try:
                self.assertIsNone(connection.execute("""
                    SELECT 1 FROM sqlite_master
                    WHERE type='table' AND name='sync_change_log'
                """).fetchone())
            finally:
                connection.close()

    def test_new_schema_declares_foreign_keys_and_indexes(self):
        with tempfile.TemporaryDirectory() as directory:
            db_path = Path(directory) / "vvs.db"
            schema_path = Path(__file__).resolve().parents[1] / "database" / "schema.sql"
            connection = sqlite3.connect(db_path)
            try:
                connection.executescript(schema_path.read_text(encoding="utf-8"))
                entry_exit_fks = {(row[2], row[3], row[4]) for row in
                                  connection.execute("PRAGMA foreign_key_list(entry_exit)")}
                log_fks = {(row[2], row[3], row[4]) for row in
                           connection.execute("PRAGMA foreign_key_list(verification_logs)")}
                entry_indexes = {row[1] for row in
                                 connection.execute("PRAGMA index_list(entry_exit)")}
                log_indexes = {row[1] for row in
                               connection.execute("PRAGMA index_list(verification_logs)")}
            finally:
                connection.close()

        self.assertEqual(entry_exit_fks, {
            ("vehicles", "vehicle_id", "vehicle_id"),
            ("gates", "entry_gate", "gate_id"),
            ("gates", "exit_gate", "gate_id"),
        })
        self.assertEqual(log_fks, {
            ("vehicles", "vehicle_id", "vehicle_id"),
            ("gates", "gate_id", "gate_id"),
        })
        self.assertIn("idx_entry_vehicle", entry_indexes)
        self.assertIn("idx_entry_gate", entry_indexes)
        self.assertIn("idx_exit_gate", entry_indexes)
        self.assertIn("idx_verif_vehicle", log_indexes)
        self.assertIn("idx_verif_gate", log_indexes)
        self.assertIn("idx_verif_time", log_indexes)

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
                connection.execute("""INSERT INTO vehicles
                    (vehicle_id, vid, owner_name, registration_no, vehicle_type,
                     qr_token, valid_from, valid_to, is_active)
                    VALUES (1, 'legacy-vid', 'Legacy User', 'LEGACY-001', 'bike',
                            'legacy-token', '2026-01-01', '2027-01-01', 1)""")
                with self.assertRaises(sqlite3.IntegrityError):
                    connection.execute("""INSERT INTO entry_exit
                        (vehicle_id, entry_gate, entry_time)
                        VALUES (999, 'GATE_A', CURRENT_TIMESTAMP)""")
                with self.assertRaises(sqlite3.IntegrityError):
                    connection.execute("""INSERT INTO entry_exit
                        (vehicle_id, entry_gate, entry_time)
                        VALUES (1, 'UNKNOWN_GATE', CURRENT_TIMESTAMP)""")
                connection.execute("""INSERT INTO entry_exit
                    (vehicle_id, entry_gate, entry_time)
                    VALUES (1, 'GATE_A', CURRENT_TIMESTAMP)""")
                connection.commit()
            finally:
                connection.close()

            self.assertIn("updated_at", vehicle_columns)
            self.assertIn("updated_at", gate_columns)
            self.assertIn("updated_at", entry_exit_columns)
            self.assertTrue({
                "entry_exit_integrity_insert", "entry_exit_integrity_update",
                "verification_logs_integrity_insert", "verification_logs_integrity_update",
                "vehicles_restrict_delete", "gates_restrict_delete",
            }.issubset(triggers))


class SyncProtocolTests(unittest.TestCase):
    def test_shared_firmware_python_ack_vectors(self):
        vectors_path = (Path(__file__).resolve().parents[3]
                        / "cms1_gateway" / "test" / "ack_protocol_vectors.json")
        vectors = json.loads(vectors_path.read_text(encoding="utf-8"))

        for vector in vectors:
            with self.subTest(vector=vector["name"]):
                self.assertEqual(
                    lora_sync_server.parse_sync_ack(
                        vector["message"], "transfer-1"),
                    vector["result"])

    def test_reassembles_chunks_in_index_order_and_acknowledges(self):
        data = {"vehicles": [], "filler": "x" * 150}
        blob = json.dumps(data, separators=(",", ":")).encode("utf-8")
        chunks = [blob[offset:offset + 100] for offset in range(0, len(blob), 100)]
        transfer_id = "transfer-1"
        receiver = lora_sync_client.SyncReceiver()
        begin = {"type": "SYNC_BEGIN", "id": transfer_id, "size": len(blob),
                 "chunks": len(chunks),
                 "sha256": __import__("hashlib").sha256(blob).hexdigest()}
        self.assertIsNone(receiver.handle(begin))
        self.assertIsNone(receiver.handle({"type": "SYNC_CHUNK", "id": transfer_id,
                                           "index": 1,
                                           "data": base64.b64encode(chunks[1]).decode()}))
        self.assertIsNone(receiver.handle({"type": "SYNC_CHUNK", "id": transfer_id,
                                           "index": 1,
                                           "data": base64.b64encode(chunks[1]).decode()}))
        self.assertIsNone(receiver.handle({"type": "SYNC_CHUNK", "id": transfer_id,
                                           "index": 0,
                                           "data": base64.b64encode(chunks[0]).decode()}))
        for index, chunk in enumerate(chunks[2:], start=2):
            self.assertIsNone(receiver.handle({
                "type": "SYNC_CHUNK", "id": transfer_id, "index": index,
                "data": base64.b64encode(chunk).decode()}))

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
        reconcile.assert_called_once_with(data)

    def test_rejects_checksum_mismatch(self):
        receiver = lora_sync_client.SyncReceiver()
        receiver.handle({"type": "SYNC_BEGIN", "id": "bad", "size": 2,
                         "chunks": 1, "sha256": "0" * 64})
        receiver.handle({"type": "SYNC_CHUNK", "id": "bad", "index": 0,
                         "data": "e30="})

        ack = receiver.handle({"type": "SYNC_END", "id": "bad"})

        self.assertFalse(ack["ok"])
        self.assertEqual(ack["error"], "checksum mismatch")

    def test_incomplete_transfer_is_not_acknowledged_as_success(self):
        receiver = lora_sync_client.SyncReceiver()
        receiver.handle({"type": "SYNC_BEGIN", "id": "incomplete", "size": 101,
                         "chunks": 2, "sha256": "0" * 64})
        receiver.handle({"type": "SYNC_CHUNK", "id": "incomplete", "index": 0,
                         "data": "eA=="})

        ack = receiver.handle({"type": "SYNC_END", "id": "incomplete"})

        self.assertFalse(ack["ok"])
        self.assertEqual(ack["error"], "incomplete transfer")

    def test_publisher_matches_ack_to_active_transfer(self):
        with tempfile.TemporaryDirectory() as directory:
            db_path = Path(directory) / "vvs.db"
            schema_path = Path(__file__).resolve().parents[1] / "database" / "schema.sql"
            connection = sqlite3.connect(db_path)
            connection.executescript(schema_path.read_text(encoding="utf-8"))
            connection.close()
            init_db.init_sync_tracking(str(db_path))
            publisher = lora_sync_server.SyncPublisher(str(db_path))
            publisher.transfer_id = "expected-transfer"
            publisher.candidate_seq = 0
            publisher.frames = [{"type": "SYNC_END"}]
            publisher.awaiting_ack = True

            self.assertFalse(publisher.handle_ack(
                '{"type":"SYNC_ACK","id":"different-transfer","ok":true}'))
            self.assertTrue(publisher.handle_ack(
                '{"type":"SYNC_ACK","id":"expected-transfer","ok":true}'))
            self.assertEqual(publisher.watermark, 0)
            self.assertIsNone(publisher.transfer_id)

    def test_publisher_ack_deadline_matches_bounded_gateway_window(self):
        self.assertEqual(lora_sync_server.FIRMWARE_ACK_WINDOW_SEC, 1)
        self.assertLessEqual(lora_sync_server.FIRMWARE_ACK_WINDOW_SEC, 1)

    def test_ack_requires_end_phase_and_rejects_late_duplicate_and_stale_ack(self):
        publisher = lora_sync_server.SyncPublisher()
        publisher.transfer_id = "transfer-1"
        publisher.candidate_seq = 1
        ack = '{"type":"SYNC_ACK","id":"transfer-1","ok":true}'

        self.assertFalse(publisher.handle_ack(ack))
        publisher.frame_index = 1
        self.assertFalse(publisher.handle_ack(ack))
        publisher.awaiting_ack = True
        publisher.ack_deadline = 1
        publisher.retries = 3
        publisher.poll(type("Writer", (), {"write": lambda *_: None})())
        self.assertIsNone(publisher.transfer_id)
        self.assertFalse(publisher.handle_ack(ack))
        publisher.transfer_id = "transfer-2"
        publisher.awaiting_ack = True
        self.assertFalse(publisher.handle_ack(ack))

    def test_matching_nack_does_not_advance_checkpoint(self):
        with tempfile.TemporaryDirectory() as directory:
            db_path = Path(directory) / "vvs.db"
            schema_path = Path(__file__).resolve().parents[1] / "database" / "schema.sql"
            connection = sqlite3.connect(db_path)
            connection.executescript(schema_path.read_text(encoding="utf-8"))
            connection.close()
            init_db.init_sync_tracking(str(db_path))
            publisher = lora_sync_server.SyncPublisher(str(db_path))
            publisher.transfer_id = "nack-transfer"
            publisher.candidate_seq = 1
            publisher.awaiting_ack = True

            self.assertTrue(publisher.handle_ack(
                '{"type":"SYNC_ACK","id":"nack-transfer","ok":false}'))
            self.assertEqual(publisher.watermark, 0)
            self.assertEqual(publisher.transfer_id, "nack-transfer")
            self.assertTrue(publisher.awaiting_ack)
            restarted = lora_sync_server.SyncPublisher(str(db_path))
            self.assertEqual(restarted.watermark, 0)

    def test_duplicate_keys_and_invalid_ack_do_not_change_checkpoint(self):
        publisher = lora_sync_server.SyncPublisher()
        publisher.transfer_id = "transfer-1"
        publisher.candidate_seq = 9
        publisher.awaiting_ack = True

        for ack in (
                '{"type":"SYNC_ACK","id":"transfer-1","ok":true,"ok":false}',
                '{"type":"SYNC_ACK","id":"transfer-1","id":"other","ok":true}',
                '{"type":"SYNC_ACK","id":"transfer-1","ok":"true"}',
                '{"type":"SYNC_ACK","id":"transfer-1","ok":true'):
            with self.subTest(ack=ack):
                self.assertFalse(publisher.handle_ack(ack))
                self.assertEqual(publisher.watermark, 0)

    def test_publisher_retries_transfer_after_ack_timeout(self):
        class SerialWriter:
            def __init__(self):
                self.writes = []

            def write(self, data):
                self.writes.append(data)

        publisher = lora_sync_server.SyncPublisher()
        publisher.transfer_id = "transfer-1"
        publisher.frames = [
            {"type": "SYNC_BEGIN", "id": "transfer-1"},
            {"type": "SYNC_END", "id": "transfer-1"},
        ]
        publisher.awaiting_ack = True
        publisher.ack_deadline = 99
        publisher.next_frame_at = 0
        writer = SerialWriter()

        with patch.object(lora_sync_server.time, "monotonic", return_value=100):
            publisher.poll(writer)

        self.assertEqual(publisher.retries, 1)
        self.assertEqual(publisher.frame_index, 1)
        self.assertIn(b'"type":"SYNC_BEGIN"', writer.writes[0])

    def test_gateway_radio_errors_retry_active_transfer_identity(self):
        class SerialWriter:
            def __init__(self):
                self.writes = []

            def write(self, data):
                self.writes.append(data)

        publisher = lora_sync_server.SyncPublisher()
        publisher.transfer_id = "transfer-1"
        publisher.frames = [{"type": "SYNC_BEGIN", "id": "transfer-1"}]
        writer = SerialWriter()

        for error_type in ("SYNC_TX_ERROR", "SYNC_TIMEOUT"):
            with self.subTest(error_type=error_type):
                publisher.transfer_id = "transfer-1"
                publisher.frames = [
                    {"type": "SYNC_BEGIN", "id": "transfer-1"},
                    {"type": "SYNC_END", "id": "transfer-1"},
                ]
                publisher.frame_index = 2
                publisher.awaiting_ack = True
                publisher.retries = 0
                publisher.ack_deadline = 999
                publisher.next_frame_at = 0
                writer.writes.clear()
                with patch.object(lora_sync_server.time, "monotonic",
                                  return_value=123):
                    self.assertTrue(publisher.handle_ack({
                        "type": error_type, "id": "transfer-1"}))
                    publisher.poll(writer)

                self.assertEqual(publisher.transfer_id, "transfer-1")
                self.assertEqual(publisher.retries, 1)
                self.assertEqual(publisher.frame_index, 1)
                self.assertIn(b'"id":"transfer-1"', writer.writes[0])

    def test_receiver_rejects_transfer_above_declared_limits(self):
        receiver = lora_sync_client.SyncReceiver()
        ack = receiver.handle({
            "type": "SYNC_BEGIN", "id": "too-large",
            "size": lora_sync_client.MAX_TRANSFER_BYTES + 1,
            "chunks": 1, "sha256": "0" * 64,
        })

        self.assertFalse(ack["ok"])
        self.assertEqual(ack["error"], "invalid transfer bounds")

    def test_sync_query_uses_each_table_timestamp_column(self):
        with tempfile.TemporaryDirectory() as directory:
            db_path = Path(directory) / "vvs.db"
            schema_path = Path(__file__).resolve().parents[1] / "database" / "schema.sql"
            connection = sqlite3.connect(db_path)
            try:
                connection.executescript(schema_path.read_text(encoding="utf-8"))
                today = datetime.now().date()
                vehicle_cursor = connection.execute("""INSERT INTO vehicles
                    (vid, owner_name, registration_no, vehicle_type, qr_token,
                     valid_from, valid_to)
                    VALUES ('sync-vid', 'Sync Owner', 'SYNC-001', 'bike',
                            'sync-token', ?, ?)""",
                    (today.isoformat(), (today + timedelta(days=1)).isoformat()))
                vehicle_id = vehicle_cursor.lastrowid
                connection.execute("""INSERT INTO verification_logs
                    (vehicle_id, gate_id, qr_token, scan_time, verification_result)
                    VALUES (?, 'GATE_A', 'sync-token', CURRENT_TIMESTAMP, 'granted')""",
                    (vehicle_id,))
                connection.execute("""INSERT INTO entry_exit
                    (vehicle_id, entry_gate, entry_time)
                    VALUES (?, 'GATE_A', CURRENT_TIMESTAMP)""", (vehicle_id,))
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
                today = datetime.now().date()
                connection.execute("""INSERT INTO vehicles
                    (vid, owner_name, registration_no, vehicle_type, qr_token,
                     valid_from, valid_to) VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    ("v" * 32, "Test Owner", "FRAME-001", "car", "t" * 22,
                     today.isoformat(), (today + timedelta(days=1)).isoformat()))
                connection.commit()
            finally:
                connection.close()

            publisher = lora_sync_server.SyncPublisher(str(db_path))
            publisher._build_transfer(0)

        frame_sizes = [len(json.dumps(frame, separators=(",", ":")).encode())
                       for frame in publisher.frames]
        self.assertTrue(frame_sizes)
        self.assertLessEqual(max(frame_sizes), 255)

    def test_sx127x_airtime_matches_sf7_profile_estimate(self):
        self.assertAlmostEqual(
            lora_sync_server.lora_airtime_seconds(217), 0.343296, places=6)
        self.assertAlmostEqual(
            lora_sync_server.lora_airtime_seconds(218), 0.343296, places=6)

    def test_publisher_paces_frames_by_airtime(self):
        class SerialWriter:
            def __init__(self):
                self.writes = []

            def write(self, data):
                self.writes.append(data)

        publisher = lora_sync_server.SyncPublisher()
        publisher.transfer_id = "a" * 32
        publisher.frames = [
            {"type": "SYNC_CHUNK", "id": "a" * 32, "index": 0,
             "data": "A" * 136},
            {"type": "SYNC_END", "id": "a" * 32},
        ]
        writer = SerialWriter()

        with patch.object(lora_sync_server.time, "monotonic", return_value=100):
            publisher.poll(writer)
        first_frame_ready = publisher.next_frame_at

        with patch.object(
                lora_sync_server.time, "monotonic",
                return_value=first_frame_ready - 0.01):
            publisher.poll(writer)
        self.assertEqual(len(writer.writes), 1)

        with patch.object(
                lora_sync_server.time, "monotonic",
                return_value=first_frame_ready):
            publisher.poll(writer)
        self.assertEqual(len(writer.writes), 2)


class SyncBatchingTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.source_db = Path(self.temp_dir.name) / "source.db"
        self.destination_db = Path(self.temp_dir.name) / "destination.db"
        schema_path = Path(__file__).resolve().parents[1] / "database" / "schema.sql"
        for db_path in (self.source_db, self.destination_db):
            connection = sqlite3.connect(db_path)
            connection.executescript(schema_path.read_text(encoding="utf-8"))
            connection.close()
        init_db.init_sync_tracking(str(self.source_db))
        self.previous_reconciliation_db = reconciliation.DB
        reconciliation.DB = str(self.destination_db)

    def tearDown(self):
        reconciliation.DB = self.previous_reconciliation_db
        self.temp_dir.cleanup()

    def add_vehicle(self, vehicle_id, owner_name, db_path=None):
        connection = sqlite3.connect(db_path or self.source_db)
        try:
            connection.execute("""
                INSERT INTO vehicles
                    (vehicle_id, vid, owner_name, registration_no, vehicle_type,
                     qr_token, valid_from, valid_to, is_active, created_at, updated_at)
                VALUES (?, ?, ?, ?, 'car', ?, '2026-01-01', '2027-01-01',
                        1, '2026-11-07 10:00:00', '2026-11-07 10:00:00')
            """, (vehicle_id, f"vid-{vehicle_id}", owner_name,
                  f"SYNC-{vehicle_id}", f"token-{vehicle_id}"))
            connection.commit()
        finally:
            connection.close()

    @staticmethod
    def payload_from(publisher):
        begin = publisher.frames[0]
        chunks = [base64.b64decode(frame["data"])
                  for frame in publisher.frames[1:-1]]
        blob = b"".join(chunks)
        assert len(blob) == begin["size"]
        return json.loads(blob.decode("utf-8"))

    @staticmethod
    def acknowledge(publisher, ok):
        publisher.awaiting_ack = True
        return publisher.handle_ack(json.dumps({
            "type": "SYNC_ACK", "id": publisher.transfer_id, "ok": ok,
        }))

    @staticmethod
    def deliver(receiver, publisher):
        for frame in publisher.frames:
            ack = receiver.handle(frame)
            if ack:
                return ack
        return None

    def test_multiple_bounded_batches_resume_after_durable_ack(self):
        for vehicle_id in range(1, 4):
            self.add_vehicle(vehicle_id, "owner-" + "x" * 1500)

        publisher = lora_sync_server.SyncPublisher(str(self.source_db))
        publisher._build_transfer(0)
        first_payload = self.payload_from(publisher)
        first_seq = publisher.candidate_seq
        connection = sqlite3.connect(self.source_db)
        try:
            maximum_seq = connection.execute(
                "SELECT MAX(seq) FROM sync_change_log").fetchone()[0]
        finally:
            connection.close()
        self.assertLessEqual(
            len(json.dumps(first_payload, separators=(",", ":")).encode()),
            lora_sync_server.MAX_BATCH_BYTES)
        self.assertLess(first_seq, maximum_seq)
        first_ack = self.deliver(lora_sync_client.SyncReceiver(), publisher)
        self.assertTrue(first_ack["ok"])
        self.assertTrue(self.acknowledge(publisher, first_ack["ok"]))

        restarted = lora_sync_server.SyncPublisher(str(self.source_db))
        self.assertEqual(restarted.watermark, first_seq)
        restarted._build_transfer(100)
        next_payload = self.payload_from(restarted)

        self.assertGreater(restarted.candidate_seq, first_seq)
        self.assertEqual(next_payload["_sync"]["after_seq"], first_seq)

    def test_failure_in_later_batch_does_not_advance_past_last_ack(self):
        for vehicle_id in range(1, 4):
            self.add_vehicle(vehicle_id, "owner-" + "y" * 1500)
        publisher = lora_sync_server.SyncPublisher(str(self.source_db))
        publisher._build_transfer(0)
        first_seq = publisher.candidate_seq
        receiver = lora_sync_client.SyncReceiver()
        first_ack = self.deliver(receiver, publisher)
        self.assertTrue(first_ack["ok"])
        self.assertTrue(self.acknowledge(publisher, first_ack["ok"]))

        publisher._build_transfer(100)
        failed_seq = publisher.candidate_seq
        with patch.object(lora_sync_client, "reconcile_blob",
                          side_effect=sqlite3.IntegrityError("injected failure")):
            nack = self.deliver(receiver, publisher)
        self.assertFalse(nack["ok"])
        publisher.awaiting_ack = True
        self.assertTrue(publisher.handle_ack(json.dumps(nack)))

        restarted = lora_sync_server.SyncPublisher(str(self.source_db))
        self.assertEqual(restarted.watermark, first_seq)
        restarted._build_transfer(200)
        self.assertEqual(restarted.candidate_seq, failed_seq)

    def test_lost_ack_replays_committed_batch_idempotently(self):
        self.add_vehicle(21, "Replay Owner")
        original = lora_sync_server.SyncPublisher(str(self.source_db))
        original._build_transfer(0)
        receiver = lora_sync_client.SyncReceiver()

        committed_ack = self.deliver(receiver, original)
        self.assertTrue(committed_ack["ok"])
        self.assertEqual(original.watermark, 0)

        restarted = lora_sync_server.SyncPublisher(str(self.source_db))
        restarted._build_transfer(100)
        committed_seq = restarted.candidate_seq
        duplicate_ack = self.deliver(receiver, restarted)

        self.assertTrue(duplicate_ack["ok"])
        self.assertTrue(self.acknowledge(restarted, duplicate_ack["ok"]))
        connection = sqlite3.connect(self.destination_db)
        try:
            self.assertEqual(connection.execute(
                "SELECT COUNT(*) FROM vehicles WHERE vehicle_id=21"
            ).fetchone()[0], 1)
        finally:
            connection.close()
        self.assertEqual(
            lora_sync_server.SyncPublisher(str(self.source_db)).watermark,
            committed_seq)

    def test_oversized_record_persists_diagnostic_without_skipping(self):
        self.add_vehicle(31, "oversized-" + "z" * 6000)
        publisher = lora_sync_server.SyncPublisher(str(self.source_db))

        publisher._build_transfer(0)

        self.assertTrue(publisher.frames)
        receiver = lora_sync_client.SyncReceiver()
        ack = self.deliver(receiver, publisher)
        self.assertTrue(ack["ok"])
        self.assertTrue(self.acknowledge(publisher, ack["ok"]))
        last_acknowledged = publisher.watermark
        publisher._build_transfer(100)

        self.assertFalse(publisher.frames)
        self.assertEqual(publisher.watermark, last_acknowledged)
        connection = sqlite3.connect(self.source_db)
        try:
            diagnostic = connection.execute("""
                SELECT table_name, row_key, message, resolved_at
                FROM sync_errors WHERE table_name='vehicles' AND row_key='31'
            """).fetchone()
        finally:
            connection.close()
        self.assertEqual(diagnostic[0:2], ("vehicles", "31"))
        self.assertIn("exceeds", diagnostic[2])
        self.assertIsNone(diagnostic[3])

    def test_change_journal_captures_same_timestamp_update(self):
        self.add_vehicle(41, "Before")
        self.add_vehicle(41, "Before", self.destination_db)
        connection = sqlite3.connect(self.source_db)
        try:
            connection.execute("""
                UPDATE vehicles SET owner_name='After',
                    updated_at='2026-11-07 10:00:00' WHERE vehicle_id=41
            """)
            connection.commit()
        finally:
            connection.close()

        publisher = lora_sync_server.SyncPublisher(str(self.source_db))
        publisher._build_transfer(0)
        payload = self.payload_from(publisher)

        self.assertEqual(payload["vehicles"][0]["owner_name"], "After")
        self.assertGreater(publisher.candidate_seq, 1)
        receiver = lora_sync_client.SyncReceiver()
        ack = self.deliver(receiver, publisher)
        self.assertTrue(ack["ok"])
        connection = sqlite3.connect(self.destination_db)
        try:
            owner_name = connection.execute(
                "SELECT owner_name FROM vehicles WHERE vehicle_id=41"
            ).fetchone()[0]
        finally:
            connection.close()
        self.assertEqual(owner_name, "After")

    def test_corrected_oversized_record_is_sent_and_diagnostic_resolved(self):
        self.add_vehicle(32, "oversized-" + "z" * 6000)
        publisher = lora_sync_server.SyncPublisher(str(self.source_db))
        publisher._build_transfer(0)
        receiver = lora_sync_client.SyncReceiver()
        first_ack = self.deliver(receiver, publisher)
        self.assertTrue(first_ack["ok"])
        self.assertTrue(self.acknowledge(publisher, first_ack["ok"]))
        publisher._build_transfer(100)
        self.assertFalse(publisher.frames)

        connection = sqlite3.connect(self.source_db)
        try:
            connection.execute(
                "UPDATE vehicles SET owner_name='Recovered' WHERE vehicle_id=32")
            connection.commit()
        finally:
            connection.close()
        publisher._build_transfer(200)
        payload = self.payload_from(publisher)

        self.assertEqual(payload["vehicles"][0]["owner_name"], "Recovered")
        ack = self.deliver(receiver, publisher)
        self.assertTrue(ack["ok"])
        self.assertTrue(self.acknowledge(publisher, ack["ok"]))
        connection = sqlite3.connect(self.source_db)
        try:
            resolved_at = connection.execute("""
                SELECT resolved_at FROM sync_errors
                WHERE table_name='vehicles' AND row_key='32'
            """).fetchone()[0]
        finally:
            connection.close()
        self.assertIsNotNone(resolved_at)

    def test_change_journal_replicates_deletion_idempotently(self):
        self.add_vehicle(51, "Delete Me")
        self.add_vehicle(51, "Delete Me", self.destination_db)
        connection = sqlite3.connect(self.source_db)
        try:
            connection.execute("DELETE FROM vehicles WHERE vehicle_id=51")
            connection.commit()
        finally:
            connection.close()

        publisher = lora_sync_server.SyncPublisher(str(self.source_db))
        publisher._build_transfer(0)
        payload = self.payload_from(publisher)
        self.assertIn(51, payload["_deletes"]["vehicles"])
        receiver = lora_sync_client.SyncReceiver()
        ack = self.deliver(receiver, publisher)

        self.assertTrue(ack["ok"])
        duplicate_ack = self.deliver(receiver, publisher)
        self.assertTrue(duplicate_ack["ok"])
        connection = sqlite3.connect(self.destination_db)
        try:
            count = connection.execute(
                "SELECT COUNT(*) FROM vehicles WHERE vehicle_id=51"
            ).fetchone()[0]
        finally:
            connection.close()
        self.assertEqual(count, 0)


if __name__ == "__main__":
    unittest.main()