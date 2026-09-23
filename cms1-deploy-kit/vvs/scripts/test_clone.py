#!/usr/bin/env python3
"""
Clone-detection test.
Simulates: entry at GATE_A → immediate scan at GATE_B (same token).
Expects: 2nd scan DENIED, system_events row with type='clone_alert'.

Run on CMS1 (or dev PC with the ~/vvs mirror and gate_listener importable).
"""
import sys, os, sqlite3, time, hashlib, secrets, base64
from datetime import datetime, timedelta

sys.path.append(os.path.expanduser("~/vvs/scripts"))
sys.path.append(os.path.expanduser("~/vvs/lora"))

from config import DB_PATH
from qr_handler import QRHandler
from lora.gate_listener import verify_and_authorize, CLONE_WINDOW_SEC

TEST_REG = "TEST-CLONE-001"


def setup_test_vehicle():
    c = sqlite3.connect(DB_PATH)
    # Idempotent cleanup
    c.execute("DELETE FROM vehicles WHERE registration_no=?", (TEST_REG,))
    c.commit()

    vid = hashlib.sha256(secrets.token_bytes(32)).hexdigest()[:32]
    tok = (base64.b64encode(secrets.token_bytes(16)).decode()
           .replace("=", "").replace("+", "").replace("/", ""))
    today = datetime.now().date()
    c.execute("""INSERT INTO vehicles
        (vid, owner_name, registration_no, vehicle_type,
         qr_token, valid_from, valid_to, is_active)
        VALUES (?,?,?,?,?,?,?,1)""",
        (vid, "Clone Test", TEST_REG, "car", tok,
         today.isoformat(),
         (today + timedelta(days=1)).isoformat()))
    c.commit()
    c.close()

    sig = QRHandler().generate_signature(vid, tok)
    return f"{vid}|{tok}|{sig}", tok, vid


def cleanup(vid, tok):
    c = sqlite3.connect(DB_PATH)
    c.execute("DELETE FROM verification_logs WHERE qr_token=?", (tok,))
    c.execute("""DELETE FROM entry_exit
                 WHERE vehicle_id=(SELECT vehicle_id FROM vehicles
                                   WHERE registration_no=?)""", (TEST_REG,))
    c.execute("DELETE FROM system_events WHERE description LIKE ?",
              (f"%{TEST_REG}%",))
    c.execute("DELETE FROM vehicles WHERE registration_no=?", (TEST_REG,))
    c.execute("DELETE FROM offline_queue WHERE qr_token=?", (tok,))
    c.commit()
    c.close()


def main():
    print("=" * 62)
    print("  CLONE-DETECTION TEST")
    print("=" * 62)

    payload, tok, vid = setup_test_vehicle()
    print(f"[setup] registered {TEST_REG}  vid={vid[:12]}...")

    # ── Scan 1 ────────────────────────────────────────
    print("\n[scan 1] GATE_A")
    r1 = verify_and_authorize(payload, "GATE_A")
    print(f"  granted={r1['granted']}  action={r1.get('action')}  reason={r1['reason']}")
    assert r1["granted"], "Scan 1 should be granted"
    assert r1.get("action") == "entry", "Scan 1 should register an entry"

    # ── Scan 2 (immediate, different gate) ────────────
    print(f"\n[scan 2] GATE_B  (immediate, within {CLONE_WINDOW_SEC}s)")
    time.sleep(1)
    r2 = verify_and_authorize(payload, "GATE_B")
    print(f"  granted={r2['granted']}  reason={r2['reason']}")
    assert not r2["granted"], "Scan 2 must be DENIED"
    assert "clone" in r2["reason"].lower(), "Denial reason must mention clone"

    # ── Inspect DB ────────────────────────────────────
    print("\n[verify] database state")
    c = sqlite3.connect(DB_PATH)
    c.row_factory = sqlite3.Row

    logs = c.execute("""SELECT gate_id, verification_result, denial_reason
                        FROM verification_logs WHERE qr_token=?
                        ORDER BY log_id""", (tok,)).fetchall()
    print(f"  verification_logs: {len(logs)} row(s)")
    for r in logs:
        print(f"    [{r['gate_id']}] {r['verification_result']:<8}  {r['denial_reason'] or ''}")

    alerts = c.execute("""SELECT timestamp, source, description
                          FROM system_events
                          WHERE event_type='clone_alert'
                          ORDER BY timestamp DESC LIMIT 5""").fetchall()
    print(f"  clone_alert events: {len(alerts)}")
    for a in alerts:
        print(f"    [{a['timestamp']}] {a['description']}")

    entries = c.execute("""SELECT entry_gate, exit_gate, exit_time
                           FROM entry_exit
                           WHERE vehicle_id=(SELECT vehicle_id FROM vehicles
                                             WHERE registration_no=?)""",
                        (TEST_REG,)).fetchall()
    print(f"  entry_exit rows: {len(entries)}")
    for e in entries:
        print(f"    {e['entry_gate']} -> {e['exit_gate'] or 'OPEN'}")
    c.close()

    # ── Asserts ───────────────────────────────────────
    assert any(r["verification_result"] == "granted" for r in logs), \
        "Missing granted log"
    assert any(r["verification_result"] == "denied" for r in logs), \
        "Missing denied log"
    assert len(alerts) >= 1, "No clone_alert event was recorded"
    assert len(entries) == 1, f"Expected exactly 1 entry_exit, got {len(entries)}"
    assert entries[0]["exit_time"] is None, \
        "Entry should still be open (exit was blocked by clone detection)"

    print("\n[cleanup] removing test data")
    cleanup(vid, tok)

    print("\n" + "=" * 62)
    print("  ✅  CLONE TEST PASSED")
    print("=" * 62)


if __name__ == "__main__":
    main()