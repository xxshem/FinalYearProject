#!/usr/bin/env python3
import os
import sqlite3
import sys

try:
    from .sync_limits import MAX_SYNC_RECORD_BYTES
except ImportError:
    from sync_limits import MAX_SYNC_RECORD_BYTES

try:
    from vvs.scripts.config import DB_PATH
except ImportError:
    from config import DB_PATH

DB = DB_PATH
SCHEMA = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "database", "schema.sql"))

SYNC_TABLES = {
    "vehicles": "vehicle_id",
    "gates": "gate_id",
    "verification_logs": "log_id",
    "entry_exit": "record_id",
    "lora_metrics": "metric_id",
    "operational_metrics": "metric_id",
    "power_metrics": "metric_id",
    "system_events": "event_id",
}


def init_sync_tracking(db_path=DB):
    """Create the local change journal and seed pre-existing rows once."""
    conn = sqlite3.connect(db_path)
    try:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute("""
            CREATE TABLE IF NOT EXISTS sync_change_log (
                seq INTEGER PRIMARY KEY AUTOINCREMENT,
                table_name TEXT NOT NULL,
                row_key TEXT NOT NULL,
                operation TEXT NOT NULL CHECK(operation IN ('upsert', 'delete'))
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS sync_state (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS sync_errors (
                table_name TEXT NOT NULL,
                row_key TEXT NOT NULL,
                message TEXT NOT NULL,
                serialized_bytes INTEGER,
                record_json TEXT,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                resolved_at TEXT,
                PRIMARY KEY (table_name, row_key)
            )
        """)
        error_columns = {
            row[1] for row in conn.execute("PRAGMA table_info(sync_errors)")
        }
        for column, declaration in (
                ("serialized_bytes", "INTEGER"),
                ("record_json", "TEXT")):
            if column not in error_columns:
                conn.execute(
                    f"ALTER TABLE sync_errors ADD COLUMN {column} {declaration}")

        def record_json_sql(table, row_alias):
            columns = [row[1] for row in conn.execute(
                f'PRAGMA table_info("{table}")')]
            fields = ", ".join(
                f"'{column.replace(chr(39), chr(39) * 2)}', "
                f'{row_alias}."{column.replace(chr(34), chr(34) * 2)}"'
                for column in columns)
            return f"json_object({fields})"

        for table, primary_key in SYNC_TABLES.items():
            record_json_new = record_json_sql(table, "NEW")
            record_json_table = record_json_sql(table, table)
            record_size_new = f"length(CAST({record_json_new} AS BLOB))"
            record_size_table = f"length(CAST({record_json_table} AS BLOB))"
            for operation in ("insert", "update", "delete"):
                conn.execute(
                    f'DROP TRIGGER IF EXISTS "sync_{table}_{operation}"')
            conn.execute(f"""
                CREATE TRIGGER "sync_{table}_insert"
                AFTER INSERT ON "{table}"
                BEGIN
                    INSERT INTO sync_errors
                        (table_name, row_key, message, serialized_bytes,
                         record_json, created_at, resolved_at)
                    SELECT '{table}', CAST(NEW."{primary_key}" AS TEXT),
                           'serialized record exceeds {MAX_SYNC_RECORD_BYTES} bytes ('
                               || {record_size_new} || ' bytes)',
                           {record_size_new}, {record_json_new},
                           CURRENT_TIMESTAMP, NULL
                    WHERE {record_size_new} > {MAX_SYNC_RECORD_BYTES}
                    ON CONFLICT(table_name, row_key) DO UPDATE SET
                        message=excluded.message,
                        serialized_bytes=excluded.serialized_bytes,
                        record_json=excluded.record_json,
                        created_at=CURRENT_TIMESTAMP,
                        resolved_at=NULL;
                    INSERT INTO sync_change_log (table_name, row_key, operation)
                    SELECT '{table}', CAST(NEW."{primary_key}" AS TEXT), 'upsert'
                    WHERE {record_size_new} <= {MAX_SYNC_RECORD_BYTES};
                END
            """)
            conn.execute(f"""
                CREATE TRIGGER "sync_{table}_update"
                AFTER UPDATE ON "{table}"
                BEGIN
                    INSERT INTO sync_change_log (table_name, row_key, operation)
                    SELECT '{table}', CAST(OLD."{primary_key}" AS TEXT), 'delete'
                    WHERE OLD."{primary_key}" IS NOT NEW."{primary_key}";
                    INSERT INTO sync_errors
                        (table_name, row_key, message, serialized_bytes,
                         record_json, created_at, resolved_at)
                    SELECT '{table}', CAST(NEW."{primary_key}" AS TEXT),
                           'serialized record exceeds {MAX_SYNC_RECORD_BYTES} bytes ('
                               || {record_size_new} || ' bytes)',
                           {record_size_new}, {record_json_new},
                           CURRENT_TIMESTAMP, NULL
                    WHERE {record_size_new} > {MAX_SYNC_RECORD_BYTES}
                    ON CONFLICT(table_name, row_key) DO UPDATE SET
                        message=excluded.message,
                        serialized_bytes=excluded.serialized_bytes,
                        record_json=excluded.record_json,
                        created_at=CURRENT_TIMESTAMP,
                        resolved_at=NULL;
                    INSERT INTO sync_change_log (table_name, row_key, operation)
                    SELECT '{table}', CAST(NEW."{primary_key}" AS TEXT), 'upsert'
                    WHERE {record_size_new} <= {MAX_SYNC_RECORD_BYTES};
                END
            """)
            conn.execute(f"""
                CREATE TRIGGER "sync_{table}_delete"
                AFTER DELETE ON "{table}"
                BEGIN
                    INSERT INTO sync_change_log (table_name, row_key, operation)
                    VALUES ('{table}', CAST(OLD."{primary_key}" AS TEXT), 'delete');
                END
            """)

        conn.execute("""
            INSERT OR IGNORE INTO sync_state (key, value)
            VALUES ('acked_seq', '0')
        """)
        bootstrapped = conn.execute(
            "SELECT 1 FROM sync_state WHERE key='bootstrap_complete'"
        ).fetchone()
        if bootstrapped is None:
            for table, primary_key in SYNC_TABLES.items():
                record_json_table = record_json_sql(table, table)
                record_size_table = f"length(CAST({record_json_table} AS BLOB))"
                conn.execute(f"""
                    INSERT INTO sync_errors
                        (table_name, row_key, message, serialized_bytes,
                         record_json, created_at, resolved_at)
                    SELECT '{table}', CAST("{primary_key}" AS TEXT),
                           'serialized record exceeds {MAX_SYNC_RECORD_BYTES} bytes ('
                               || {record_size_table} || ' bytes)',
                           {record_size_table}, {record_json_table},
                           CURRENT_TIMESTAMP, NULL
                    FROM "{table}"
                    WHERE {record_size_table} > {MAX_SYNC_RECORD_BYTES}
                    ON CONFLICT(table_name, row_key) DO UPDATE SET
                        message=excluded.message,
                        serialized_bytes=excluded.serialized_bytes,
                        record_json=excluded.record_json,
                        created_at=CURRENT_TIMESTAMP,
                        resolved_at=NULL
                """)
                conn.execute(f"""
                    INSERT INTO sync_change_log (table_name, row_key, operation)
                    SELECT ?, CAST("{primary_key}" AS TEXT), 'upsert'
                    FROM "{table}"
                    WHERE {record_size_table} <= {MAX_SYNC_RECORD_BYTES}
                    ORDER BY "{primary_key}"
                """, (table,))
            conn.execute("""
                INSERT INTO sync_state (key, value)
                VALUES ('bootstrap_complete', '1')
            """)
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init(include_sync_tracking=True):
    os.makedirs(os.path.dirname(DB), exist_ok=True)
    conn = sqlite3.connect(DB)
    try:
        with open(SCHEMA, encoding="utf-8") as f:
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
    if include_sync_tracking:
        init_sync_tracking(DB)
    print(f"[OK] Database created at {DB}")

if __name__ == "__main__":
    unknown_args = set(sys.argv[1:]) - {"--mirror-only"}
    if unknown_args:
        raise SystemExit(f"unknown arguments: {', '.join(sorted(unknown_args))}")
    init(include_sync_tracking="--mirror-only" not in sys.argv[1:])