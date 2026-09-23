#!/usr/bin/env python3
import sqlite3, os

DB = os.path.expanduser("~/vvs/database/vvs.db")
SCHEMA = os.path.expanduser("~/vvs/database/schema.sql")

def init():
    os.makedirs(os.path.dirname(DB), exist_ok=True)
    conn = sqlite3.connect(DB)
    with open(SCHEMA) as f:
        conn.executescript(f.read())
    conn.commit()
    conn.close()
    print(f"[OK] Database created at {DB}")

if __name__ == "__main__":
    init()