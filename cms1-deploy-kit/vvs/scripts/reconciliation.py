#!/usr/bin/env python3
import sqlite3, os
from datetime import datetime

DB = os.path.expanduser("~/vvs/database/vvs.db")

TIMESTAMP_COL = {
    "vehicles": "created_at", "gates": "created_at",
    "verification_logs": "scan_time", "entry_exit": "entry_time",
    "lora_metrics": "timestamp", "operational_metrics": "timestamp",
    "power_metrics": "timestamp", "system_events": "timestamp",
}

PK_COL = {
    "vehicles": "vehicle_id", "gates": "gate_id",
    "verification_logs": "log_id", "entry_exit": "record_id",
    "lora_metrics": "metric_id", "operational_metrics": "metric_id",
    "power_metrics": "metric_id", "system_events": "event_id",
}

def _parse_ts(v):
    if v is None: return None
    if isinstance(v, datetime): return v
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M:%S.%f",
                "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"):
        try: return datetime.strptime(str(v)[:26], fmt)
        except ValueError: continue
    return None

def lww_upsert(table, rows, dry_run=False):
    if not rows: return 0, 0, 0
    ts_col = TIMESTAMP_COL.get(table)
    pk_col = PK_COL.get(table)
    if not ts_col or not pk_col: return 0, 0, 0

    c = sqlite3.connect(DB); c.row_factory = sqlite3.Row
    cur = c.cursor()
    ins = upd = kept = 0

    for r in rows:
        pk = r.get(pk_col)
        if pk is None: continue
        local = cur.execute(f"SELECT * FROM {table} WHERE {pk_col}=?",
                            (pk,)).fetchone()
        if local is None:
            cols = list(r.keys())
            qs = ",".join("?" * len(cols)); cl = ",".join(cols)
            if not dry_run:
                cur.execute(f"INSERT INTO {table} ({cl}) VALUES ({qs})",
                            [r[x] for x in cols])
            ins += 1
            continue
        lt = _parse_ts(local[ts_col]); it = _parse_ts(r.get(ts_col))
        if it and lt and it > lt:
            cols = list(r.keys())
            sc = ",".join(f"{x}=?" for x in cols if x != pk_col)
            vals = [r[x] for x in cols if x != pk_col] + [pk]
            if not dry_run:
                cur.execute(f"UPDATE {table} SET {sc} WHERE {pk_col}=?", vals)
            upd += 1
        else:
            kept += 1

    if not dry_run: c.commit()
    c.close()
    return ins, upd, kept

def reconcile_blob(blob, dry_run=False):
    return {t: lww_upsert(t, rows, dry_run) for t, rows in blob.items()}