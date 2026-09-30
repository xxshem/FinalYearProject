#!/usr/bin/env python3
import sqlite3, os, json
from datetime import datetime

try:
    from vvs.scripts.config import DB_PATH as DB
except ImportError:
    from config import DB_PATH as DB

def enqueue(op_type, vehicle_id=None, gate_id=None, qr_token=None, payload=None):
    c = sqlite3.connect(DB)
    c.execute("""INSERT OR IGNORE INTO offline_queue
        (operation_type, vehicle_id, gate_id, qr_token, payload, status)
        VALUES (?,?,?,?,?,'pending')""",
        (op_type, vehicle_id, gate_id, qr_token,
         json.dumps(payload) if payload else None))
    c.commit(); c.close()

def pending(limit=50):
    c = sqlite3.connect(DB); c.row_factory = sqlite3.Row
    rows = [dict(r) for r in c.execute(
        "SELECT * FROM offline_queue WHERE status='pending' "
        "ORDER BY created_at ASC LIMIT ?", (limit,))]
    c.close(); return rows

def mark_sent(qid):
    c = sqlite3.connect(DB)
    c.execute("""UPDATE offline_queue SET status='sent',
                 attempt_count=attempt_count+1, last_attempt=?
                 WHERE queue_id=?""", (datetime.now(), qid))
    c.commit(); c.close()

def mark_failed(qid):
    c = sqlite3.connect(DB)
    c.execute("""UPDATE offline_queue SET status='failed',
                 attempt_count=attempt_count+1, last_attempt=?
                 WHERE queue_id=?""", (datetime.now(), qid))
    c.commit(); c.close()

def stats():
    c = sqlite3.connect(DB)
    out = {}
    for s in ('pending','sent','failed','done'):
        n = c.execute("SELECT COUNT(*) FROM offline_queue WHERE status=?",
                      (s,)).fetchone()[0]
        out[s] = n
    c.close(); return out