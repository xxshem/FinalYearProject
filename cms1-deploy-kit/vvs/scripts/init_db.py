#!/usr/bin/env python3
import sqlite3, os

DB = os.path.expanduser("~/vvs/database/vvs.db")
SCHEMA = os.path.expanduser("~/vvs/database/schema.sql")

def init():
    os.makedirs(os.path.dirname(DB), exist_ok=True)
    conn = sqlite3.connect(DB)
    try:
        with open(SCHEMA) as f:
            conn.executescript(f.read())
        timestamp_columns = {
            "vehicles": "created_at",
            "gates": "created_at",
            "entry_exit": "entry_time",
        }
        for table, timestamp_column in timestamp_columns.items():
            columns = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
            if "updated_at" not in columns:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN updated_at TIMESTAMP")
                conn.execute(f"UPDATE {table} SET updated_at={timestamp_column} "
                             "WHERE updated_at IS NULL")
        conn.executescript("""
            CREATE TRIGGER IF NOT EXISTS vehicles_insert_updated_at
            AFTER INSERT ON vehicles
            FOR EACH ROW WHEN NEW.updated_at IS NULL
            BEGIN
                UPDATE vehicles SET updated_at=COALESCE(NEW.created_at, CURRENT_TIMESTAMP)
                WHERE vehicle_id=NEW.vehicle_id;
            END;
            CREATE TRIGGER IF NOT EXISTS gates_insert_updated_at
            AFTER INSERT ON gates
            FOR EACH ROW WHEN NEW.updated_at IS NULL
            BEGIN
                UPDATE gates SET updated_at=COALESCE(NEW.created_at, CURRENT_TIMESTAMP)
                WHERE gate_id=NEW.gate_id;
            END;
            CREATE TRIGGER IF NOT EXISTS entry_exit_insert_updated_at
            AFTER INSERT ON entry_exit
            FOR EACH ROW WHEN NEW.updated_at IS NULL
            BEGIN
                UPDATE entry_exit SET updated_at=COALESCE(NEW.entry_time, CURRENT_TIMESTAMP)
                WHERE record_id=NEW.record_id;
            END;
            CREATE TRIGGER IF NOT EXISTS vehicles_updated_at
            AFTER UPDATE ON vehicles
            FOR EACH ROW WHEN NEW.updated_at = OLD.updated_at
            BEGIN
                UPDATE vehicles SET updated_at=CURRENT_TIMESTAMP
                WHERE vehicle_id=NEW.vehicle_id;
            END;
            CREATE TRIGGER IF NOT EXISTS gates_updated_at
            AFTER UPDATE ON gates
            FOR EACH ROW WHEN NEW.updated_at = OLD.updated_at
            BEGIN
                UPDATE gates SET updated_at=CURRENT_TIMESTAMP
                WHERE gate_id=NEW.gate_id;
            END;
            CREATE TRIGGER IF NOT EXISTS entry_exit_updated_at
            AFTER UPDATE ON entry_exit
            FOR EACH ROW WHEN NEW.updated_at = OLD.updated_at
            BEGIN
                UPDATE entry_exit SET updated_at=CURRENT_TIMESTAMP
                WHERE record_id=NEW.record_id;
            END;
        """)
        conn.commit()
    finally:
        conn.close()
    print(f"[OK] Database created at {DB}")

if __name__ == "__main__":
    init()