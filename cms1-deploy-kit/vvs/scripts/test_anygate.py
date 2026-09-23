#!/usr/bin/env python3
"""
Any-gate exit test.
Simulates: entry at GATE_A → exit at GATE_B → re-entry at GATE_C.
Verifies entry_exit rows and durations.

Waits CLONE_WINDOW_SEC + 2 s between entry and exit so the exit
is treated as a legitimate departure (not a clone).
"""
import sys, os, sqlite3, time, hashlib, secrets, base64
from datetime import datetime, timedelta

sys.path.append(os.path.expanduser("~/vvs/scripts"))
sys.path.append(os.path.expanduser("~/vvs/lora"))

from config import DB_PATH
from qr_handler import QRHandler
from lora.gate_listener import verify_and_authorize, CLONE_WINDOW_SEC

TEST_REG = "TEST-ANYGATE-001"


def setup_test_vehicle():
    c = sqlite3.connect(DB_PATH)
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
        (vid, "AnyGate Test", TEST_REG, "car", tok,
         today.isoformat(),
         (today + timedelta(days=1)).isoformat()))
    c.commit()
    c.close()

    sig = QRHandler().generate_signature(vid, tok)
    return f"{vid}|{tok}|{sig}", tok, vid


def get_vehicle_id():
    c = sqlite3.connect(DB_PATH)
    r = c.execute("SELECT vehicle_id FROM vehicles WHERE registration_no=?",
                  (TEST_REG,)).fetchone()
    c.close()
    return r[0] if r else None


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
    print("  ANY-GATE EXIT TEST")
    print("=" * 62)

    payload, tok, vid = setup_test_vehicle()
    vehicle_id = get_vehicle_id()
    print(f"[setup] registered {TEST_REG}  vehicle_id={vehicle_id}")

    # ── Step 1 — Entry at GATE_A ──────────────────────
    print("\n[step 1] entry at GATE_A")
    r = verify_and_authorize(payload, "GATE_A")
    print(f"  granted={r['granted']}  action={r.get('action')}")
    assert r["granted"] and r.get("action") == "entry"

    # Wait past clone window
    wait = CLONE_WINDOW_SEC + 2
    print(f"\n  ... waiting {wait}s (past clone window of {CLONE_WINDOW_SEC}s)")
    time.sleep(wait)

    # ── Step 2 — Exit at GATE_B ───────────────────────
    print("\n[step 2] exit at GATE_B  (different gate!)")
    r = verify_and_authorize(payload, "GATE_B")
    print(f"  granted={r['granted']}  action={r.get('action')}")
    print(f"  reason={r['reason']}")
    assert r["granted"], "Exit should be granted"
    assert r.get("action") == "exit", f"Expected exit, got {r.get('action')}"

    # ── Step 3 — Re-entry at GATE_C ───────────────────
    print("\n[step 3] re-entry at GATE_C")
    time.sleep(1)
    r = verify_and_authorize(payload, "GATE_C")
    print(f"  granted={r['granted']}  action={r.get('action')}")
    assert r["granted"] and r.get("action") == "entry"

    # ── Verify DB ─────────────────────────────────────
    print("\n[verify] entry_exit rows for this vehicle")
    c = sqlite3.connect(DB_PATH)
    c.row_factory = sqlite3.Row
    rows = c.execute("""SELECT record_id, entry_gate, entry_time,
                               exit_gate, exit_time, duration_minutes
                        FROM entry_exit
                        WHERE vehicle_id=? ORDER BY record_id""",
                     (vehicle_id,)).fetchall()
    for r in rows:
        print(f"  #{r['record_id']}: {r['entry_gate']} -> "
              f"{r['exit_gate'] or 'OPEN':<7}  "
              f"duration={r['duration_minutes'] or '-'} min")
    c.close()

    # ── Asserts ───────────────────────────────────────
    assert len(rows) == 2, f"Expected 2 entry_exit rows, got {len(rows)}"

    # First row: GATE_A → GATE_B (closed, with duration)
    assert rows[0]["entry_gate"] == "GATE_A", "First entry should be GATE_A"
    assert rows[0]["exit_gate"]  == "GATE_B", "First exit should be GATE_B"
    assert rows[0]["exit_time"] is not None,   "First row must be closed"
    assert rows[0]["duration_minutes"] is not None, \
        "First row must have a duration"

    # Second row: GATE_C → (open)
    assert rows[1]["entry_gate"] == "GATE_C", "Second entry should be GATE_C"
    assert rows[1]["exit_time"] is None,      "Second row must still be open"

    print("\n[cleanup] removing test data")
    cleanup(vid, tok)

    print("\n" + "=" * 62)
    print("  ✅  ANY-GATE TEST PASSED")
    print("=" * 62)


if __name__ == "__main__":
    main()