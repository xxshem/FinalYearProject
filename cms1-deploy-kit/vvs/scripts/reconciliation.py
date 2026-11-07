#!/usr/bin/env python3
"""
Reconciliation engine (LWW) with clone-alert escalation.
CMS2 uses this to merge incoming CMS1 sync payloads.
"""
import sqlite3
from datetime import datetime, timedelta

try:
    from .config import DB_PATH as DB
except ImportError:
    from config import DB_PATH as DB

TIMESTAMP_COL = {
    "vehicles":            "updated_at",
    "gates":               "updated_at",
    "verification_logs":   "scan_time",
    "entry_exit":          "updated_at",
    "lora_metrics":        "timestamp",
    "operational_metrics": "timestamp",
    "power_metrics":       "timestamp",
    "system_events":       "timestamp",
}

PK_COL = {
    "vehicles":            "vehicle_id",
    "gates":               "gate_id",
    "verification_logs":   "log_id",
    "entry_exit":          "record_id",
    "lora_metrics":        "metric_id",
    "operational_metrics": "metric_id",
    "power_metrics":       "metric_id",
    "system_events":       "event_id",
}

PK_TYPE = {
    "vehicles": int,
    "gates": str,
    "verification_logs": int,
    "entry_exit": int,
    "lora_metrics": int,
    "operational_metrics": int,
    "power_metrics": int,
    "system_events": int,
}

TABLES = tuple(TIMESTAMP_COL)
DELETE_ORDER = (
    "verification_logs", "entry_exit", "lora_metrics",
    "operational_metrics", "power_metrics", "system_events",
    "vehicles", "gates",
)


def _parse_ts(v):
    if v is None:
        return None
    if isinstance(v, datetime):
        return v
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M:%S.%f",
                "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(str(v)[:26], fmt)
        except ValueError:
            continue
    return None


def _validate_blob(blob, connection):
    if not isinstance(blob, dict):
        raise ValueError("transfer root must be an object")

    metadata = blob.get("_sync")
    if metadata is not None:
        if (not isinstance(metadata, dict)
                or set(metadata) != {"schema", "after_seq", "through_seq"}
                or type(metadata["schema"]) is not int
                or metadata["schema"] != 1
                or type(metadata["after_seq"]) is not int
                or type(metadata["through_seq"]) is not int
                or metadata["after_seq"] < 0
                or metadata["through_seq"] <= metadata["after_seq"]):
            raise ValueError("invalid synchronization batch metadata")

    validated = {}
    deletes_blob = blob.get("_deletes", {})
    if not isinstance(deletes_blob, dict):
        raise ValueError("_deletes must be an object")
    for table, rows in blob.items():
        if table in ("_deletes", "_sync"):
            continue
        if table not in TABLES:
            raise ValueError(f"unknown table: {table}")
        if not isinstance(rows, list):
            raise ValueError(f"{table} rows must be a list")

        columns = {row[1] for row in connection.execute(
            f'PRAGMA table_info("{table}")')}
        pk_col = PK_COL[table]
        ts_col = TIMESTAMP_COL[table]
        if not {pk_col, ts_col}.issubset(columns):
            raise ValueError(f"{table} schema is missing required columns")

        seen_pks = set()
        for row in rows:
            if not isinstance(row, dict):
                raise ValueError(f"{table} rows must be objects")
            if not row or not set(row).issubset(columns):
                raise ValueError(f"{table} row contains unknown or no columns")
            if pk_col not in row or row[pk_col] is None or ts_col not in row:
                raise ValueError(f"{table} row is missing its key or timestamp")
            if type(row[pk_col]) is not PK_TYPE[table]:
                raise ValueError(f"{table} primary key has an invalid type")
            if _parse_ts(row[ts_col]) is None:
                raise ValueError(f"{table} row has an invalid timestamp")
            if row[pk_col] in seen_pks:
                raise ValueError(f"{table} contains duplicate {pk_col}")
            seen_pks.add(row[pk_col])
        validated[table] = rows

    validated_deletes = {}
    for table, keys in deletes_blob.items():
        if table not in TABLES:
            raise ValueError(f"unknown delete table: {table}")
        if not isinstance(keys, list):
            raise ValueError(f"{table} deletes must be a list")
        pk_col = PK_COL[table]
        seen_pks = set()
        row_pks = {row[pk_col] for row in validated.get(table, [])}
        for key in keys:
            if type(key) is not PK_TYPE[table]:
                raise ValueError(f"{table} delete key has an invalid type")
            if key in seen_pks:
                raise ValueError(f"{table} contains duplicate delete keys")
            if key in row_pks:
                raise ValueError(f"{table} key cannot be upserted and deleted")
            seen_pks.add(key)
        if keys:
            validated_deletes[table] = keys
    return validated, validated_deletes


def _lww_upsert(connection, table, rows, dry_run=False, on_insert=None,
                prefer_equal_timestamp=False):
    if not rows:
        return 0, 0, 0

    ts_col = TIMESTAMP_COL.get(table)
    pk_col = PK_COL.get(table)
    if not ts_col or not pk_col:
        return 0, 0, 0

    cur = connection.cursor()
    ins = upd = kept = 0

    for row in rows:
        pk = row[pk_col]
        local = cur.execute(
            f'SELECT * FROM "{table}" WHERE "{pk_col}"=?', (pk,)
        ).fetchone()

        if local is None:
            cols = list(row)
            quoted_cols = ",".join(f'"{column}"' for column in cols)
            placeholders = ",".join("?" for _ in cols)
            if not dry_run:
                cur.execute(
                    f'INSERT INTO "{table}" ({quoted_cols}) VALUES ({placeholders})',
                    [row[column] for column in cols])
            ins += 1
            if on_insert:
                try:
                    on_insert(table, pk, row)
                except Exception as error:
                    print(f"[recon] on_insert callback error: {error}")
            continue

        local_ts = _parse_ts(local[ts_col])
        incoming_ts = _parse_ts(row.get(ts_col))
        if (incoming_ts and
                ((local_ts and incoming_ts > local_ts)
                 or (prefer_equal_timestamp and
                     (local_ts is None or incoming_ts == local_ts)))):
            cols = [column for column in row if column != pk_col]
            set_clause = ",".join(f'"{column}"=?' for column in cols)
            values = [row[column] for column in cols] + [pk]
            if not dry_run:
                cur.execute(
                    f'UPDATE "{table}" SET {set_clause} WHERE "{pk_col}"=?',
                    values)
            upd += 1
        else:
            kept += 1

    return ins, upd, kept


def lww_upsert(table, rows, dry_run=False, on_insert=None):
    """Apply a last-write-wins merge for one table in its own transaction."""
    if table not in TABLES:
        return 0, 0, 0

    c = sqlite3.connect(DB)
    c.row_factory = sqlite3.Row
    try:
        c.execute("BEGIN IMMEDIATE")
        result = _lww_upsert(c, table, rows, dry_run, on_insert)
        if dry_run:
            c.rollback()
        else:
            c.commit()
        return result
    except Exception:
        c.rollback()
        raise
    finally:
        c.close()


def reconcile_blob(blob, dry_run=False, alert_callback=None):
    """
    Apply LWW to every table in the sync payload.
    Detects newly-inserted system_events rows of type 'clone_alert'
    and logs them; optionally invokes alert_callback(alert_dict).
    Returns {table: (ins, upd, kept)}.
    """
    new_alerts = []
    prefer_equal_timestamp = (
        isinstance(blob, dict) and blob.get("_sync") is not None)

    def on_insert(table, pk, row):
        if (not dry_run and table == "system_events"
                and row.get("event_type") == "clone_alert"):
            new_alerts.append(row)

    connection = sqlite3.connect(DB)
    connection.row_factory = sqlite3.Row
    try:
        validated, validated_deletes = _validate_blob(blob, connection)
        connection.execute("BEGIN IMMEDIATE")
        report = {}
        for table in TABLES:
            if table in validated:
                report[table] = _lww_upsert(
                    connection, table, validated[table], dry_run,
                    on_insert=on_insert,
                    prefer_equal_timestamp=prefer_equal_timestamp)
        for table in DELETE_ORDER:
            for key in validated_deletes.get(table, []):
                if not dry_run:
                    connection.execute(
                        f'DELETE FROM "{table}" WHERE "{PK_COL[table]}"=?',
                        (key,))
        if dry_run:
            connection.rollback()
        else:
            connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()

    # Escalate
    for a in new_alerts:
        ts   = a.get("timestamp", "?")
        src  = a.get("source", "?")
        desc = a.get("description", "")
        print(f"[CLONE ALERT] {ts} src={src} :: {desc}")

    if alert_callback:
        for a in new_alerts:
            try:
                alert_callback(a)
            except Exception as e:
                print(f"[recon] alert_callback error: {e}")

    return report


# ─────────────────────────────────────────────────────
#  Dashboard helper — recent clone alerts
# ─────────────────────────────────────────────────────
def get_recent_clone_alerts(hours=24, limit=50):
    """Return recent clone_alert events, newest first."""
    since = datetime.now() - timedelta(hours=hours)
    c = sqlite3.connect(DB)
    c.row_factory = sqlite3.Row
    rows = [dict(r) for r in c.execute("""
        SELECT event_id, timestamp, source, description
        FROM system_events
        WHERE event_type='clone_alert' AND timestamp >= ?
        ORDER BY timestamp DESC LIMIT ?
    """, (since.strftime("%Y-%m-%d %H:%M:%S"), limit)).fetchall()]
    c.close()
    return rows


def clone_alert_count(hours=24):
    """For dashboard badge counters."""
    since = datetime.now() - timedelta(hours=hours)
    c = sqlite3.connect(DB)
    n = c.execute("""
        SELECT COUNT(*) FROM system_events
        WHERE event_type='clone_alert' AND timestamp >= ?
    """, (since.strftime("%Y-%m-%d %H:%M:%S"),)).fetchone()[0]
    c.close()
    return n