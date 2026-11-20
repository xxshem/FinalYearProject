import os
import sys
import unittest
from unittest.mock import patch

VVS_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, VVS_DIR)

from lora.lora_sync_server import SyncPublisher


class SyncPublisherBackoffTests(unittest.TestCase):
    def setUp(self):
        self.publisher = SyncPublisher.__new__(SyncPublisher)
        self.publisher.consecutive_failures = 0
        self.publisher.next_poll = 0
        self.publisher.frames = []
        self.publisher.frame_index = 0
        self.publisher.transfer_id = None
        self.publisher.candidate_seq = None
        self.publisher.candidate_keys = []
        self.publisher.awaiting_ack = False
        self.publisher.ack_deadline = 0
        self.publisher.retries = 0

    def arm_exhausted_transfer(self, transfer_id):
        self.publisher.frames = [{"type": "SYNC_END"}]
        self.publisher.frame_index = 1
        self.publisher.transfer_id = transfer_id
        self.publisher.candidate_seq = 1
        self.publisher.candidate_keys = []
        self.publisher.awaiting_ack = True
        self.publisher.ack_deadline = 0
        self.publisher.retries = 3

    def test_failed_transfers_back_off_and_cap_at_five_minutes(self):
        now = 100
        for index, delay in enumerate((30, 60, 120, 300, 300)):
            self.arm_exhausted_transfer(f"transfer-{index}")
            with patch("lora.lora_sync_server.time.monotonic",
                       return_value=now):
                self.publisher.poll(None)

            self.assertEqual(self.publisher.next_poll, now + delay)
            self.assertIsNone(self.publisher.transfer_id)
            now += delay

    def test_positive_ack_resets_failure_backoff(self):
        self.publisher.transfer_id = "transfer-ok"
        self.publisher.awaiting_ack = True
        self.publisher.candidate_seq = 5
        self.publisher.candidate_keys = []
        self.publisher.consecutive_failures = 3
        self.publisher._persist_checkpoint = lambda *_: None

        with patch("lora.lora_sync_server.time.monotonic", return_value=200):
            accepted = self.publisher.handle_ack(
                '{"type":"SYNC_ACK","id":"transfer-ok","ok":true}')

        self.assertTrue(accepted)
        self.assertEqual(self.publisher.consecutive_failures, 0)
        self.assertEqual(self.publisher.next_poll, 230)


if __name__ == "__main__":
    unittest.main()
