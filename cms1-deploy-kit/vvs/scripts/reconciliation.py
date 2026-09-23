#!/usr/bin/env python3
"""
Reconciliation engine (LWW) with clone-alert escalation.
CMS2 uses this to merge incoming CMS1 sync payloads.
"""
import sqlite3, os
from datetime import datetime, timedelta

DB = os.path.expanduser("~/vvs/database/vvs.db")

TIMESTAMP_COL = {
    "vehicles":            "created_at",
    "gates":               "created_at",
    "verification_logs":   "scan_time",
    "entry_exit":          "entry_time",
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


def lww_upsert(table, rows, dry_run=False, on_insert=None):
    """
    Last-Write-Wins merge.
    Returns (inserted, updated, kept_local).
    If on_insert is given, called as on_insert(table, pk, row_dict) for each new row.
    """
    if not rows:
        return 0, 0, 0

    ts_col = TIMESTAMP_COL.get(table)
    pk_col = PK_COL.get(table)
    if not ts_col or not pk_col:
        return 0, 0, 0

    c = sqlite3.connect(DB)
    c.row_factory = sqlite3.Row
    cur = c.cursor()

    ins = upd = kept = 0

    for r in rows:
        pk = r.get(pk_col)
        if pk is None:
            continue

        local = cur.execute(
            f"SELECT * FROM {table} WHERE {pk_col}=?", (pk,)
        ).fetchone()

        # ── New row ─────────────────────────────────
        if local is None:
            cols = list(r.keys())
            qs = ",".join("?" * len(cols))
            cl = ",".join(cols)
            if not dry_run:
                cur.execute(
                    f"INSERT INTO {table} ({cl}) VALUES ({qs})",
                    [r[c_] for c_ in cols])
            ins += 1
            if on_insert:
                try:
                    on_insert(table, pk, r)
                except Exception as e:
                    print(f"[recon] on_insert callback error: {e}")
            continue

        # ── Existing row → LWW ──────────────────────
        local_ts = _parse_ts(local[ts_col])
        inc_ts   = _parse_ts(r.get(ts_col))

        if inc_ts and local_ts and inc_ts > local_ts:
            cols = list(r.keys())
            set_clause = ",".join(f"{c_}=?" for c_ in cols if c_ != pk_col)
            values = [r[c_] for c_ in cols if c_ != pk_col] + [pk]
            if not dry_run:
                cur.execute(
                    f"UPDATE {table} SET {set_clause} WHERE {pk_col}=?",
                    values)
            upd += 1
        else:
            kept += 1

    if not dry_run:
        c.commit()
    c.close()
    return ins, upd, kept


def reconcile_blob(blob, dry_run=False, alert_callback=None):
    """
    Apply LWW to every table in the sync payload.
    Detects newly-inserted system_events rows of type 'clone_alert'
    and logs them; optionally invokes alert_callback(alert_dict).
    Returns {table: (ins, upd, kept)}.
    """
    new_alerts = []

    def on_insert(table, pk, row):
        if table == "system_events" and row.get("event_type") == "clone_alert":
            new_alerts.append(row)

    report = {}
    for table, rows in blob.items():
        report[table] = lww_upsert(table, rows, dry_run, on_insert=on_insert)

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