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
[ ] Gate node (ESP32-CAM + SX1278 + PCF8574 + LCD + LEDs + buzzer) — powered on
[ ] Gate node antenna attached
[ ] CMS1 gateway (ESP32 Dev + SX1278) — plugged into Pi USB
[ ] CMS1 gateway antenna attached
[ ] CMS1 (Raspberry Pi) — powered on, connected to WiFi
[ ] CMS2 gateway (ESP32 Dev + SX1278) — plugged into PC USB
[ ] CMS2 gateway antenna attached
[ ] CMS2 (Linux PC) — powered on, connected to WiFi
[ ] Dev PC — plugged into gate node via USB (for serial commands)
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
[ ] Window D — Dev PC: PlatformIO serial monitor on gate node

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
SECTION 2 — FIRMWARE STATE VERIFICATION  (1 min)
═══════════════════════════════════════════════════════════════════════════════

Step 2.1 — Open serial monitor on gate node

  Window D (Dev PC terminal):
  $ cd C:\Users\User\esp32-projects\gate_node
  $ pio device monitor

  Expected boot banner (if not already booted, press reset on gate node):

    [GATE] booting
    [QR] camera ready (ESP32QRCodeReader)
    [LORA] ready on 868.1 MHz
    [GATE] ready

[ ] Boot banner confirmed
[ ] LCD shows "Ready / Scan QR code"

Step 2.2 — Confirm starting gate ID

  In the serial monitor, type:
  WHOAMI

  Expected:
    [GATE] current GATE_ID = GATE_A

  Expected LCD: "Gate ID / GATE_A" for 1.5 s, then back to Ready.

[ ] GATE_ID confirmed as GATE_A
[ ] LCD acknowledged

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

Observed in Serial Monitor (Window D):
[ ] [QR] decoded: <payload>
[ ] [GATE] token: <token>
[ ] [LORA] tx XX bytes
[ ] [RF] RSSI=XX SNR=XX.X
[ ] [GATE] reply: {"granted":true,...,"action":"entry"}

Observed in Window A (CMS1 listener):
[ ] [CMS1] GATE_A -> granted=True (entry at GATE_A)

Observed in Window C (browser /logs):
[ ] New row: DEMO 001 | GATE_A | granted | <timestamp>

Time of entry (write): __________:__________

── ACTION 2 ── SWITCH GATE IDENTITY TO GATE_C ────────────────────────────────

Narration: "We now reassign the same physical node to represent a different
            gate on campus — the hostel gate — without reflashing."

Action:
[ ] In the serial monitor, type:
    SET_GATE=GATE_C

Expected on Serial Monitor:
[ ] [GATE] GATE_ID now = GATE_C

Expected on LCD:
[ ] "Gate ID set / GATE_C" for 1.5 s, then back to "Ready / Scan QR code"

Verification:
[ ] Type: WHOAMI
[ ] Confirms: [GATE] current GATE_ID = GATE_C

Time of switch (write): __________:__________

── ACTION 3 ── WAIT FOR CLONE WINDOW TO CLEAR ────────────────────────────────

Narration: "The system treats back-to-back scans within 5 seconds as
            a possible clone attempt. We wait 7 seconds to allow a
            legitimate exit."

Action:
[ ] Wait 7 full seconds (count slowly, or use a stopwatch)

  [ ] Waited 7+ seconds since entry

Time exit begins (write): __________:__________

── ACTION 4 ── EXIT AT GATE_C ────────────────────────────────────────────────

Narration: "The same vehicle now leaves campus through the hostel gate.
            Note this is a DIFFERENT gate from the one it entered."

Action:
[ ] Scan the same QR code again

Observed on LCD:
[ ] "Checking..."
[ ] "ACCESS GRANTED" / "DEMO 001"

Observed on hardware:
[ ] Green LED + single beep

Observed in Serial Monitor:
[ ] [QR] decoded
[ ] [LORA] tx
[ ] [GATE] reply: {"granted":true,...,"action":"exit"}

Observed in Window A (CMS1 listener):
[ ] [CMS1] GATE_C -> granted=True (exit from GATE_A, X.XX min on campus)

Observed in Window C (browser /logs):
[ ] Two new rows total: GATE_A (entry) and GATE_C (exit)

Time of exit (write): __________:__________

Calculated duration on campus: __________ min

── ACTION 5 ── SWITCH GATE IDENTITY BACK TO GATE_A ──────────────────────────

Narration: "Vehicle returns to campus later the same day."

Action:
[ ] In serial monitor, type:
    SET_GATE=GATE_A

Expected:
[ ] [GATE] GATE_ID now = GATE_A
[ ] LCD acknowledges

Verification:
[ ] WHOAMI shows GATE_A

── ACTION 6 ── RE-ENTRY AT GATE_A ────────────────────────────────────────────

Narration: "The vehicle comes back in through the main entrance."

Action:
[ ] Wait 2–3 seconds after the SET_GATE command (to avoid duplicate-scan guard)
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
[ ] Ensure GATE_ID = GATE_A (WHOAMI)
[ ] Vehicle currently OUTSIDE (after step 4, it's inside — first scan below
    will be an exit, then scan again immediately)

Action 1:
[ ] Scan QR (registers exit at GATE_A)
[ ] Wait 1 second
[ ] Change gate ID: SET_GATE=GATE_B
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
[ ] SET_GATE=GATE_A
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

Problem: "SET_GATE" command ignored
  → Confirm exact format: SET_GATE=GATE_C  (no spaces, capital GATE)
  → Serial line ending must be Newline or CR+LF

Problem: Two rows in entry_exit but no exit_gate on first
  → The exit was denied as a clone
  → Wait longer between entry and exit, then re-scan

═══════════════════════════════════════════════════════════════════════════════
SECTION 7 — DEMO SIGN-OFF
═══════════════════════════════════════════════════════════════════════════════

Demo completed at: __________:__________

Summary of results:
[ ] Entry at GATE_A — recorded successfully
[ ] SET_GATE changed identity at runtime without reflash
[ ] Exit at GATE_C — recorded successfully (any-gate exit PROVEN)
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