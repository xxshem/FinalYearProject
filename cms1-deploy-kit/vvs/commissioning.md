═══════════════════════════════════════════════════════════════════════════════
                     VVS COMMISSIONING WALKTHROUGH
                 Any-Gate Exit Live Demonstration Script
                       Dedan Kimathi University of Technology
═══════════════════════════════════════════════════════════════════════════════

Purpose: Demonstrate that a single gate node can perform entry at one gate
         and exit at a different gate, with full state tracking, on real
         hardware — no simulation.

Duration: ~15 minutes
Operators: 1 (you) + 1 observer (supervisor / partner)

Test vehicle reg no: _________________________
Test QR code printed: [ ] Yes  [ ] No
Date: ________________  Start time: _________

═══════════════════════════════════════════════════════════════════════════════
SECTION 0 — PRE-FLIGHT CHECKLIST  (5 min before demo)
═══════════════════════════════════════════════════════════════════════════════

Hardware in place:
[ ] Gate A node (ESP32-CAM + ESP-12E/SX1278 + LCD + indicators) — built as GATE_A
[ ] Gate C node (same hardware) — built as GATE_C
[ ] (Optional clone demo) Gate B node — built as GATE_B
[ ] Both gate antennas attached
[ ] CMS1 gateway (ESP32 Dev + SX1278) — plugged into Pi USB
[ ] CMS1 gateway antenna attached
[ ] CMS1 (Raspberry Pi) — powered on, connected to WiFi
[ ] CMS2 gateway (ESP32 Dev + SX1278) — plugged into PC USB
[ ] CMS2 gateway antenna attached
[ ] CMS2 (Linux PC) — powered on, connected to WiFi
[ ] Dev PC — available to build/flash gate firmware with the bridge UART disconnected
[ ] Printed QR code for the test vehicle — in hand

Software running (check CMS1 via SSH):

  $ ssh pi@<CMS1-IP>
  $ systemctl status vvs-dashboard --no-pager
  $ systemctl status vvs-listener  --no-pager

[ ] Both CMS1 services show "active (running)"
[ ] Dashboard reachable at http://<CMS1-IP>:5000
[ ] Test vehicle exists in DB (see Section 1)

Terminal windows open and visible:
[ ] Window A — CMS1 SSH: tail of listener log
[ ] Window B — CMS2 SSH: tail of sync client log (optional, for cross-CMS demo)
[ ] Window C — Browser: CMS1 dashboard /logs page
[ ] Window D — Dev PC: PlatformIO terminal (only needed if gate firmware must be reflashed)

═══════════════════════════════════════════════════════════════════════════════
SECTION 1 — PRE-DEMO DATA SETUP  (2 min)
═══════════════════════════════════════════════════════════════════════════════

Step 1.1 — Register the test vehicle (if not already done)

  On CMS1:
  $ cd ~/vvs/scripts
  $ python3 add_test_data.py vehicle "Demo Driver" "DEMO 001" car

  Expected output:
    [OK] DEMO 001 registered. Token: <random_string>

  Write the token here for reference: ___________________________________

Step 1.2 — Confirm test vehicle exists

  $ python3 add_test_data.py summary

  Expected:
    vehicles                : 1  (or more)
    ...

[ ] Test vehicle registered and visible in summary

Step 1.3 — Generate and print QR code

  $ source ~/vvs.env
  $ python3 -c "
  import sqlite3, os, qrcode
  DB = os.path.expanduser('~/vvs/database/vvs.db')
  c = sqlite3.connect(DB)
  row = c.execute('SELECT vid, qr_token FROM vehicles WHERE registration_no=?',
                  ('DEMO 001',)).fetchone()
  from qr_handler import QRHandler
  sig = QRHandler().generate_signature(row[0], row[1])
  payload = f'{row[0]}|{row[1]}|{sig}'
  img = qrcode.make(payload)
  img.save(os.path.expanduser('~/vvs/demo_qr.png'))
  print('Saved ~/vvs/demo_qr.png')
  "

  Transfer to a device you can display at the camera:
  $ scp pi@<CMS1-IP>:~/vvs/demo_qr.png ~/Desktop/

  Open the PNG, display it on your phone screen OR print it at A6 size.
  Make sure the camera can see it clearly (contrast, no glare).

[ ] QR code printed or displayed on phone screen
[ ] Camera confirms it can read the QR (test scan optional)

Step 1.4 — Start live log tails

  Window A (SSH CMS1):
  $ ssh pi@<CMS1-IP>
  $ journalctl -u vvs-listener -f

  Expected: the terminal is idle, waiting for packets.

[ ] Window A is tailing and shows no errors
[ ] Window C (browser) shows http://<CMS1-IP>:5000/logs

═══════════════════════════════════════════════════════════════════════════════
SECTION 2 — BUILD AND FLASH GATE IDS  (before connecting gate nodes)
═══════════════════════════════════════════════════════════════════════════════

Step 2.1 — Build each gate with its fixed ID

  In the project `gate_node` directory:

  Gate A:
  $ pio run -e esp32cam

  Gate C:
  $ pio run -e gate_c

`GATE_ID_VALUE` is a compile-time firmware value. The build rejects values
outside `GATE_[A-Z]`; CMS1 must also have the selected ID registered in its
`gates` table. Never use a serial command to change gate identity.

Step 2.2 — Flash each matching build

Before flashing, disconnect the ESP32-CAM/bridge UART wires and connect the
programming adapter. For Gate A:

  $ pio run -e esp32cam -t upload

For Gate C:

  $ pio run -e gate_c -t upload

Reconnect the bridge and reset each node after flashing. The compiled gate ID
is constant in firmware and survives normal resets and power cycles.

[ ] Gate A has the `esp32cam` build; Gate C has the `gate_c` build
[ ] Both nodes show their normal ready state on their displays

═══════════════════════════════════════════════════════════════════════════════
SECTION 3 — LIVE DEMONSTRATION
═══════════════════════════════════════════════════════════════════════════════

── ACTION 1 ── ENTRY AT GATE_A ────────────────────────────────────────────────

Narration: "The vehicle arrives at the main entrance. The guard scans the QR."

Action:
[ ] Hold the QR code 10–15 cm in front of the ESP32-CAM lens

Observed on gate node LCD:
[ ] "Checking..."
[ ] Then "ACCESS GRANTED" / "DEMO 001"

Observed on gate node hardware:
[ ] Green LED lit for ~1.5 s
[ ] Buzzer sounded single short beep

Observed in Window A (CMS1 listener):
[ ] [CMS1] GATE_A granted=True action=entry reason=
[ ] `verification_logs.gate_id` for this scan is `GATE_A`

Observed in Window C (browser /logs):
[ ] New row: DEMO 001 | GATE_A | granted | <timestamp>

Time of entry (write): __________:__________

── ACTION 2 ── EXIT AT THE SEPARATELY BUILT GATE_C ────────────────────────────

Narration: "The vehicle now leaves through a second physical gate. Its
            firmware was built with the fixed identity GATE_C."

Action:
[ ] Move to Gate C

── ACTION 3 ── WAIT FOR CLONE WINDOW TO CLEAR ────────────────────────────────

Narration: "The system treats back-to-back scans within 5 seconds as
            a possible clone attempt. We wait 7 seconds to allow a
            legitimate exit at the second gate."

Action:
[ ] Wait 7 full seconds (count slowly, or use a stopwatch)

  [ ] Waited 7+ seconds since entry

Time exit begins (write): __________:__________

── ACTION 4 ── EXIT AT GATE_C ───────────────────────────────────────────────

Action:
[ ] Scan the same QR code at Gate C

Observed on LCD:
[ ] "Checking..."
[ ] "ACCESS GRANTED" / "DEMO 001"

Observed on hardware:
[ ] Green LED + single beep

Observed in Window A (CMS1 listener):
[ ] [CMS1] GATE_C granted=True action=exit reason=
[ ] `verification_logs.gate_id` for this scan is `GATE_C`

Observed in Window C (browser /logs):
[ ] Two new rows total: GATE_A (entry) and GATE_C (exit)

Time of exit (write): __________:__________

Calculated duration on campus: __________ min

── ACTION 5 ── RE-ENTRY AT THE SEPARATELY BUILT GATE_A ───────────────────────

Narration: "The vehicle returns later through Gate A, whose firmware retains
            its GATE_A identity across resets."

Action:
[ ] Return to Gate A

── ACTION 6 ── RE-ENTRY AT GATE_A ────────────────────────────────────────────

Narration: "The vehicle comes back in through the main entrance."

Action:
[ ] Wait 7+ seconds after the exit scan (to avoid the duplicate-scan guard)
[ ] Scan the QR code again

Observed:
[ ] LCD "ACCESS GRANTED" / "DEMO 001"
[ ] Green LED + beep
[ ] Serial: [GATE] reply: {"granted":true,...,"action":"entry"}
[ ] Window A: [CMS1] GATE_A -> granted=True (entry at GATE_A)
[ ] Browser /logs: third new row for DEMO 001

Time of re-entry (write): __________:__________

═══════════════════════════════════════════════════════════════════════════════
SECTION 4 — DATABASE VERIFICATION  (2 min)
═══════════════════════════════════════════════════════════════════════════════

Step 4.1 — Query the entry_exit table

  On CMS1 (Window A, or a new SSH session):

  $ sqlite3 ~/vvs/database/vvs.db "
  SELECT record_id, entry_gate, entry_time,
         exit_gate, exit_time, duration_minutes
  FROM entry_exit
  WHERE vehicle_id = (
      SELECT vehicle_id FROM vehicles WHERE registration_no = 'DEMO 001'
  )
  ORDER BY record_id;
  "

Expected output (3 rows after the demo — or 2 if only entry+exit tested):

  ┌──────────┬───────────┬────────┬───────────┬───────────┬────────────┐
  │record_id │entry_gate │entry...│exit_gate  │exit_time  │duration_min│
  ├──────────┼───────────┼────────┼───────────┼───────────┼────────────┤
  │    1     │  GATE_A   │ 14:02  │  GATE_C   │ 14:02:45  │   0.57     │
  │    2     │  GATE_A   │ 14:03  │  (empty)  │  (empty)  │  (empty)   │
  └──────────┴───────────┴────────┴───────────┴───────────┴────────────┘

Copy the actual output here for your report:

  Row 1: entry_gate=________  exit_gate=________  duration=________
  Row 2: entry_gate=________  exit_gate=________

[ ] Row 1 shows entry GATE_A, exit GATE_C (any-gate exit PROVEN)
[ ] Row 1 has a non-empty duration
[ ] Row 2 shows entry GATE_A, exit still open (vehicle currently inside)

Step 4.2 — Confirm logs

  $ sqlite3 ~/vvs/database/vvs.db "
  SELECT log_id, gate_id, verification_result, denial_reason
  FROM verification_logs
  WHERE qr_token = (SELECT qr_token FROM vehicles WHERE registration_no='DEMO 001')
  ORDER BY log_id;
  "

Expected: three rows, all 'granted', gate IDs matching the sequence.

[ ] Three verification_logs rows
[ ] gate_ids in order: GATE_A, GATE_C, GATE_A

Step 4.3 — Confirm no clone alerts (should be zero for this test)

  $ sqlite3 ~/vvs/database/vvs.db "
  SELECT COUNT(*) FROM system_events
  WHERE event_type='clone_alert'
    AND timestamp > datetime('now', '-10 minutes');
  "

Expected output: 0

[ ] Zero clone alerts (proof that 7-second wait avoided false positive)

Step 4.4 — Optional: Cross-CMS verification

  On CMS2 (Window B):
  $ sudo journalctl -u vvs-sync-client@pi -n 20

  Expected within about one minute of the CMS1 update:
    [SYNC-RX] <transfer-id> ok=True

  Check the mirror database directly:
  $ sqlite3 ~/vvs/database/vvs.db "SELECT COUNT(*) FROM verification_logs;"
  [ ] The CMS2 verification log count includes the CMS1 scans

═══════════════════════════════════════════════════════════════════════════════
SECTION 5 — CLONE DETECTION (OPTIONAL BONUS DEMO)
═══════════════════════════════════════════════════════════════════════════════

This demonstrates anti-fraud in action. Run only if the primary demo passed.

Setup:
[ ] Use the physical Gate A node built as GATE_A
[ ] Vehicle currently OUTSIDE (after step 4, it's inside — first scan below
    will be an exit, then scan again immediately)

Action 1:
[ ] Scan QR (registers exit at GATE_A)
[ ] Wait 1 second
[ ] Move to the separate Gate B node built as GATE_B
[ ] Scan QR within 5 seconds of the previous scan

Expected on this second scan:
[ ] LCD "ACCESS DENIED"
[ ] Red LED + double beep
[ ] Serial: [GATE] reply: {"granted":false,"reason":"clone suspected:..."}
[ ] Window A: [CMS1] GATE_B -> granted=False (clone suspected...)

Verify:
  $ sqlite3 ~/vvs/database/vvs.db "
  SELECT timestamp, description FROM system_events
  WHERE event_type='clone_alert' ORDER BY timestamp DESC LIMIT 1;
  "

Expected: one row with text "clone suspected: also entered at GATE_A ..."

[ ] Clone alert recorded

Cleanup:
[ ] Delete test vehicle if desired:
    $ python3 add_test_data.py summary  (confirm before deleting)

═══════════════════════════════════════════════════════════════════════════════
SECTION 6 — TROUBLESHOOTING
═══════════════════════════════════════════════════════════════════════════════

Problem: Gate LED never lights, LCD stays blank
  → Check PCF8574 + LCD wiring
  → Confirm I2C addresses in firmware (LCD_ADDR, PCF_ADDR)
  → Run i2c scanner sketch (see separate guide)

Problem: Gate LED lights green but no CMS1 log
  → Check CMS1-GW ESP32 is plugged into Pi USB
  → Confirm `journalctl -u vvs-listener` shows "[CMS1] listening on..."
  → Verify both antennas attached

Problem: CMS1 log shows "granted=True" but no LCD feedback
  → Feedback path (LoRa reply → gate) is broken
  → Check gate node serial for "[GATE] timeout"
  → Move gate node closer to CMS1 antenna

Problem: Scan denied unexpectedly
  → Vehicle may be expired or deactivated
  → Check: SELECT is_active, valid_from, valid_to FROM vehicles WHERE registration_no='DEMO 001'
  → Re-activate: UPDATE vehicles SET is_active=1

Problem: Clone alert triggered during legit exit
  → You scanned within 5 seconds of the entry
  → Wait longer (7+ seconds) between entry and exit

Problem: CMS1 reports the wrong gate ID
  → Check which PlatformIO environment was used to build and flash that node
  → Rebuild/flash the matching environment with the bridge UART disconnected
  → Reset the node, then scan again and verify the CMS1 log/database value

Problem: Two rows in entry_exit but no exit_gate on first
  → The exit was denied as a clone
  → Wait longer between entry and exit, then re-scan

═══════════════════════════════════════════════════════════════════════════════
SECTION 7 — DEMO SIGN-OFF
═══════════════════════════════════════════════════════════════════════════════

Demo completed at: __________:__________

Summary of results:
[ ] Entry at GATE_A — recorded successfully
[ ] Separate GATE_C build reported its ID and recorded the exit
[ ] Re-entry at GATE_A — recorded successfully
[ ] Database state machine verified (2+ rows, correct gates, duration)
[ ] CMS2 mirror received synced rows
[ ] (Optional) Clone detection triggered and denied correctly

Observer signature: ______________________________  Date: __________

Operator signature: ______________________________  Date: __________

Notes / observations:
_______________________________________________________________
_______________________________________________________________
_______________________________________________________________
_______________________________________________________________

═══════════════════════════════════════════════════════════════════════════════
                        END OF COMMISSIONING WALKTHROUGH
═══════════════════════════════════════════════════════════════════════════════