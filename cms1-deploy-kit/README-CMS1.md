# VVS — CMS1 (Raspberry Pi Zero 2 W) Deployment

## Overview
CMS1 is the master node. It:
- Receives gate verification traffic over LoRa CH1 (868.1 MHz)
- Verifies signed QR payloads against the master SQLite DB
- Serves a Flask dashboard on port 5000
- Sends acknowledged database updates to CMS2 over LoRa CH2 (868.5 MHz)

CMS1 uses one SX1278 radio. It listens continuously on CH1 and briefly switches
to CH2 for sync frames; both gateway firmware builds must use the matching
radio profiles included here. The CMS1 listener is the only process that opens
the gateway serial port.

## What You Need
- Raspberry Pi Zero 2 W with Raspberry Pi OS (Bookworm) on SD card
- Pi connected to WiFi, SSH enabled
- The `cms1-deploy-kit` folder (this folder)

## Deploy (10 minutes)

### 1. Copy kit to the Pi
From your dev PC:
    ssh pi@<CMS1-IP> "mkdir -p ~/cms1-deploy-kit ~/vvs"
    scp cms1-deploy-kit/install.sh cms1-deploy-kit/requirements.txt pi@<CMS1-IP>:~/cms1-deploy-kit/
    scp -r cms1-deploy-kit/systemd pi@<CMS1-IP>:~/cms1-deploy-kit/
    scp -r cms1-deploy-kit/vvs pi@<CMS1-IP>:~/

### 2. SSH in
    ssh pi@<CMS1-IP>

### 3. Run installer
    chmod +x ~/cms1-deploy-kit/install.sh
    ~/cms1-deploy-kit/install.sh

### 4. Reboot
    sudo reboot

### 5. Verify (after reboot)
    systemctl status vvs-dashboard --no-pager
    systemctl status vvs-listener  --no-pager

Both should show "active (running)". The listener handles gate checks and
scheduled sync transmissions.

### 6. Open dashboard
From any browser: http://<CMS1-IP>:5000

## After Adding LoRa Hardware

When the updated CMS1 ESP32+SX1278 gateway is plugged into the Pi:

1. Find the serial port:
    ls /dev/ttyUSB*
    # Usually /dev/ttyUSB0

2. If different, edit `VVS_SERIAL_PORT` in `~/vvs.env`:
    nano ~/vvs.env

3. Restart services:
    sudo systemctl restart vvs-listener

## Install the CMS2 Sync Receiver

Flash `cms2_gateway` to the CMS2 ESP32 gateway and connect it to the CMS2 Linux
host. Copy the kit and VVS files from the development PC:

    ssh pi@<CMS2-IP> "mkdir -p ~/cms1-deploy-kit/systemd ~/vvs"
    scp cms1-deploy-kit/install-cms2-client.sh pi@<CMS2-IP>:~/cms1-deploy-kit/
    scp cms1-deploy-kit/systemd/vvs-sync-client@.service pi@<CMS2-IP>:~/cms1-deploy-kit/systemd/
    scp -r cms1-deploy-kit/vvs pi@<CMS2-IP>:~/
    ssh pi@<CMS2-IP>
    bash ~/cms1-deploy-kit/install-cms2-client.sh

The install script creates the mirror database and enables
`vvs-sync-client@pi`. Check its status and transfer logs with:

    sudo systemctl status vvs-sync-client@pi --no-pager
    sudo journalctl -u vvs-sync-client@pi -f

CMS2 receives the database mirror only; its dashboard is not installed by this
client setup. To use another Linux account, replace `pi` in the service name
with that account name.

## Add Test Data

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

Connect the ESP32-CAM scanner's USB serial adapter to the operator computer
(this is separate from the CMS1 gateway serial port), then assign its logical
gate before scanning:

    python3 ~/vvs/scripts/set_gate.py --gate A --port /dev/ttyUSB1

Scan the demo QR to record entry at `GATE_A`, then reassign and scan again to
record an any-gate exit:

    python3 ~/vvs/scripts/set_gate.py --gate B --port /dev/ttyUSB1

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