# VVS — CMS1 (Raspberry Pi Zero 2 W) Deployment

## Overview
CMS1 is the master node. It:
- Receives gate verification traffic over LoRa CH1 (433.1 MHz)
- Verifies signed QR payloads against the master SQLite DB
- Serves a Flask dashboard on port 5000
- Sends acknowledged database updates to CMS2 over LoRa CH2 (433.5 MHz)

CMS1 uses one SX1278 radio. It listens continuously on CH1 and briefly switches
to CH2 for sync frames; both gateway firmware builds must use the matching
radio profiles included here. The CMS1 listener is the only process that opens
the gateway serial port. The bridge/CMS1 CH1 and CMS1/CMS2 CH2 source profiles
are SF7, 125 kHz, coding rate 4/5, 8-symbol preamble, CRC enabled, normal IQ,
2 dBm, and 4 MHz SPI. See the
[radio configuration baseline](../RADIO_CONFIGURATION.md); repository settings
do not establish the profile of firmware already flashed on hardware.
Build and tool versions are pinned and documented in
[`BUILD_ENVIRONMENT.md`](../BUILD_ENVIRONMENT.md).

## What You Need
- Raspberry Pi Zero 2 W with Raspberry Pi OS (Bookworm) on SD card
- Pi connected to WiFi, SSH enabled
- The `cms1-deploy-kit` folder (this folder)

## Deploy (10 minutes)

### 1. Copy kit to the Pi
From your dev PC, replace `<PI-USER>` with the Linux username you created:
    ssh <PI-USER>@<CMS1-IP> "mkdir -p ~/cms1-deploy-kit ~/vvs"
    scp cms1-deploy-kit/install.sh cms1-deploy-kit/requirements.txt <PI-USER>@<CMS1-IP>:~/cms1-deploy-kit/
    scp -r cms1-deploy-kit/systemd <PI-USER>@<CMS1-IP>:~/cms1-deploy-kit/
    scp -r cms1-deploy-kit/vvs <PI-USER>@<CMS1-IP>:~/

### 2. SSH in
    ssh <PI-USER>@<CMS1-IP>

### 3. Run installer
    chmod +x ~/cms1-deploy-kit/install.sh
    ~/cms1-deploy-kit/install.sh

The installer configures both services for the account running it, so the
default Raspberry Pi OS username `pi` is not required.
If you cloned the project instead of copying the kit into `~/cms1-deploy-kit`,
run `~/FinalYearProject/cms1-deploy-kit/install.sh`; the installer locates its
requirements and service files beside itself.

### 4. Reboot
    sudo reboot

### 5. Verify (after reboot)
    systemctl status vvs-dashboard --no-pager
    systemctl status vvs-listener  --no-pager

Both should show "active (running)". The listener handles gate checks and
scheduled sync transmissions.

### 6. Open dashboard
From any browser: http://<CMS1-IP>:5000

### Register a vehicle and generate its QR code

Enter vehicle information in the dashboard; you do not need to create the QR
code or its signing data yourself:

1. Open **Vehicles** and select **Register Vehicle**.
2. Enter the owner's name and vehicle registration number, choose the vehicle
   type, and set how many days the registration is valid.
3. Select **Register & Generate QR**. The dashboard saves the vehicle in the
   CMS1 database and opens its QR image.
4. Save or print the image and give it to the vehicle owner to present when
   scanned at a gate. Later, find the vehicle on the **Vehicles** page and use
   its **QR** action to display the image again.

The system creates the vehicle's internal QR identifier and token, then signs
them with the CMS1 signing key. The QR contains this signed verification
payload, not the owner's name or registration number. Keep `~/vvs.env` (which
contains the signing key) secure; changing that key means existing QR codes
must be regenerated.

## After Adding LoRa Hardware

When the updated CMS1 ESP32+SX1278 gateway is plugged into the Pi:

1. Find the serial port:
    ls /dev/ttyUSB*
    # Usually /dev/ttyUSB0

2. Prefer the adapter's stable `/dev/serial/by-id/` path where available. If
   needed, edit `VVS_SERIAL_PORT` in `~/vvs.env`:
    nano ~/vvs.env

3. Restart services:
    sudo systemctl restart vvs-listener

The listener reconnects automatically after serial open, read, or write failures.
It logs attempts and backs off by 1, 2, 4, 8, 16, then 30 seconds (capped).
Partial frames time out after one second; buffered input is cleared when a
connection is reopened, and a retransmitted gate request is deduplicated by its
request identity.

## Install the CMS2 Sync Receiver

Flash `cms2_gateway` to the CMS2 ESP8266 NodeMCU gateway and connect it to the CMS2 Linux
host. Copy the kit and VVS files from the development PC:

    ssh pi@<CMS2-IP> "mkdir -p ~/cms1-deploy-kit/systemd ~/vvs"
    scp cms1-deploy-kit/install-cms2-client.sh pi@<CMS2-IP>:~/cms1-deploy-kit/
    scp cms1-deploy-kit/systemd/vvs-sync-client@.service pi@<CMS2-IP>:~/cms1-deploy-kit/systemd/
    scp -r cms1-deploy-kit/vvs pi@<CMS2-IP>:~/
    ssh pi@<CMS2-IP>
    ls -l /dev/serial/by-id/
    nano ~/vvs-sync-client.env
    bash ~/cms1-deploy-kit/install-cms2-client.sh

Set `VVS_SERIAL_PORT` in `~/vvs-sync-client.env` to the exact persistent path
reported under `/dev/serial/by-id/`, for example:

    VVS_SERIAL_PORT=/dev/serial/by-id/<adapter-specific-name>

Do not copy the example literally; use the path present on the CMS2 host. The
installer refuses to enable the service until this value is configured. If the
adapter has no by-id link, configure its verified device path and document the
udev rule or hardware-specific selection used to keep that path stable. The
receiver backs off and retries when the configured device is temporarily
unavailable.

The install script creates the mirror database and enables
`vvs-sync-client@pi`. Check its status and transfer logs with:

    sudo systemctl status vvs-sync-client@pi --no-pager
    sudo journalctl -u vvs-sync-client@pi -f

The CMS1 publisher sends independently acknowledged batches up to 4 KiB,
split into 100-byte chunks. A serialized row is limited to 3 KiB, leaving room
for batch metadata and other rows. CMS1 initialization adds a local change
journal and durable ACK cursor; it tracks row changes by sequence number,
including deletes, rather than relying on timestamps. CMS2 is initialized in
mirror-only mode and does not create publisher journal triggers.

Rows over 3 KiB are quarantined in CMS1's local `sync_errors` table with their
table, primary key, serialized size, reason, and serialized snapshot. They are
not journaled or sent. This explicit quarantine does not prevent subsequent
supported records from syncing; an older pending oversized journal entry is
also detected and quarantined by the publisher before it can block later
batches. The durable cursor advances only after CMS2 acknowledges a batch;
skipped data remains visible in `sync_errors` and is not silently discarded.
CMS2 independently rejects oversized records and batches before applying any
database changes.

The CMS1 modem services queued gate-response (`C1`) commands ahead of sync
(`C2`) commands and dwells on CH1 for at least 500 ms between CH2 frames. A
single radio still cannot receive CH1 during a CH2 transmission. If a sync
transfer exhausts its initial attempt and three retries without an ACK, the
publisher backs off for 30 seconds, 1 minute, 2 minutes, then 5 minutes on
successive failed transfers (capped at 5 minutes). A positive ACK resets the
backoff; the durable cursor remains unchanged until then.

To recover, inspect unresolved items with:

    sqlite3 ~/vvs/database/vvs.db "SELECT table_name, row_key, serialized_bytes, message FROM sync_errors WHERE resolved_at IS NULL;"

Use the table and key to reduce or otherwise correct the source record so its
serialized form fits within 3 KiB. The database journal will queue the
correction; after CMS2 acknowledges it, the corresponding `sync_errors` row is
marked resolved. The original quarantined snapshot remains available for
diagnosis. Do not delete the source record or quarantine entry as a substitute
for resolving the underlying data-size issue.

CMS2 receives the database mirror only; its dashboard is not installed by this
client setup. To use another Linux account, replace `pi` in the service name
with that account name.

To retrieve the CMS1 and CMS2 gateway counters without opening a second
process on either serial port, signal the service that owns the port:

    sudo systemctl kill --signal=USR1 vvs-listener
    sudo journalctl -u vvs-listener -n 50 --no-pager
    sudo systemctl kill --signal=USR1 vvs-sync-client@pi
    sudo journalctl -u vvs-sync-client@pi -n 50 --no-pager

The request is deferred until the corresponding host-side synchronization
transfer is idle. Counters reset on firmware reboot and saturate rather than
wrap. Gate-bridge statistics are counted but not currently retrievable during
normal operation because the gate has no approved operator trigger.

## Add Test Data (Optional)

For command-line demos or test records, use the helper below instead of the
dashboard form. It adds vehicle records to the same database, but does not
display or save QR images. To get a QR for one of these records, open
**Vehicles** in the dashboard and select that vehicle's **QR** action.

    cd ~/vvs/scripts
    python3 add_test_data.py vehicle "Alice Wanjiku" "KDA 111A" car
    python3 add_test_data.py vehicle "Brian Otieno" "KDB 222B" bike
    python3 add_test_data.py vehicle "Carol Mwangi" "KDC 333C" truck
    python3 add_test_data.py summary

## Generate QR Code for Testing

Use the QR image served by the dashboard’s vehicle page. For command-line
generation, load the install-specific signing key first:

    source ~/vvs.env
    cd ~/vvs
    ~/vvs-venv/bin/python3 -c "
    import os, sqlite3, qrcode
    from scripts.qr_handler import QRHandler
    db = os.path.expanduser('~/vvs/database/vvs.db')
    with sqlite3.connect(db) as connection:
        vid, token = connection.execute('SELECT vid, qr_token FROM vehicles LIMIT 1').fetchone()
    signature = QRHandler().generate_signature(vid, token)
    image = qrcode.make(f'{vid}|{token}|{signature}')
    image.save(os.path.expanduser('~/vvs/test_qr.png'))
    "

Keep `~/vvs.env` backed up securely. Existing QR codes signed with an earlier
key must be regenerated after the key is changed.

## Any-Gate Demo

Initialize the database and create the idempotent `DEMO-001` bike record and
QR image:

    cd ~/vvs/scripts
    python3 init_demo.py

The command prints the signed QR payload and saves `demo_qr.png` beside the
database. If no signing key exists, it creates one in `~/vvs.env`; keep that
file shared with the listener service. For a local test database, set
`VVS_DB_PATH` before running the command.

Build and flash separate gate firmware images for the physical gates. For
example, use the `esp32cam` environment for `GATE_A` and `gate_b` for `GATE_B`
(see `gate_node/README.md` for the build and flash commands). Scan the demo QR
at each gate; confirm the CMS1 listener logs and `verification_logs.gate_id`
show the corresponding configured IDs. Gate IDs are compiled into firmware,
survive ordinary reboots, and cannot be reassigned by a runtime serial command.

The dashboard's `/logs`, `/lora`, and `/operational` pages refresh every five
seconds and show the audit trail, RSSI/SNR, CMS1 processing latency, response
time, and DB query time. Latency is CMS1 request handling time, not a measured
over-the-air round trip.

## Database Integrity Enhancements

Fresh databases declare foreign keys from `entry_exit.vehicle_id` to the
internal `vehicles.vehicle_id` key, from `entry_exit.entry_gate` and
`entry_exit.exit_gate` to `gates.gate_id`, and from `verification_logs` to the
same vehicle and gate keys. `vehicles.vid` remains the signed QR identifier;
the runtime and demo scripts use integer `vehicle_id` for relational joins.
Validity dates, active-state values, and verification outcomes have checks,
and foreign-key/audit columns are indexed.

SQLite cannot add foreign-key or check constraints to existing columns with
`ALTER TABLE`. The idempotent triggers in `database/schema.sql` therefore
validate vehicle and gate references on legacy inserts/updates and prevent
deleting referenced vehicle/gate history. `init_db.py` applies those triggers
without dropping existing data. Existing malformed rows are not rewritten;
back up and audit the database before deployment.

CMS1 also stores each identified gate request and its original response in
`verification_request_receipts`, in the same SQLite transaction as the vehicle
transition. Receipts are retained indefinitely so delayed retries remain
idempotent. Exact retries receive the stored response; reuse of an identity
with different payload is rejected without a state transition. Since this
protocol uses a 16-bit sequence, the gate rotates its random 32-bit boot ID
when the sequence wraps. Reuse of a boot ID across gate restarts is still
possible, though unlikely; identical payloads reusing the same
gate/boot/sequence key are indistinguishable from delayed retries and receive
the original response. Different payloads reusing a key are rejected. Do not
delete receipt rows as routine cleanup.

## Common Commands

| Task | Command |
|---|---|
| Restart dashboard | `sudo systemctl restart vvs-dashboard` |
| Restart listener | `sudo systemctl restart vvs-listener` |
| Live listener log | `journalctl -u vvs-listener -f` |
| CMS2 sync log | `sudo journalctl -u vvs-sync-client@pi -f` |
| DB summary | `python3 ~/vvs/scripts/add_test_data.py summary` |
| Backup DB | `cp ~/vvs/database/vvs.db ~/vvs/database/vvs-$(date +%F).db` |

## Troubleshooting

### Dashboard not loading
    sudo systemctl status vvs-dashboard
    sudo journalctl -u vvs-dashboard -n 50

### Serial permission denied
    sudo usermod -aG dialout pi
    # then log out and back in

### Services fail after edit
    source ~/vvs-venv/bin/activate
    python3 ~/vvs/lora/gate_listener.py
    # Run manually to see the exact error