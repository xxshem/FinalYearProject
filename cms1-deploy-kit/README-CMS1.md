# VVS — CMS1 (Raspberry Pi Zero 2 W) Deployment

## Overview
CMS1 is the master node. It:
- Receives gate verification traffic over LoRa CH1 (868.1 MHz)
- Verifies against the master SQLite DB
- Serves a Flask dashboard on port 5000
- Syncs data to CMS2 over LoRa CH2 (868.5 MHz)

## What You Need
- Raspberry Pi Zero 2 W with Raspberry Pi OS (Bookworm) on SD card
- Pi connected to WiFi, SSH enabled
- The `cms1-deploy-kit` folder (this folder)

## Deploy (10 minutes)

### 1. Copy kit to the Pi
From your dev PC:
    scp -r cms1-deploy-kit pi@<CMS1-IP>:~/
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
    systemctl status vvs-sync      --no-pager

All three should show "active (running)".

### 6. Open dashboard
From any browser: http://<CMS1-IP>:5000

## After Adding LoRa Hardware

When the ESP32+SX1278 gateway is plugged into the Pi:

1. Find the serial port:
    ls /dev/ttyUSB*
    # Usually /dev/ttyUSB0

2. If different, edit:
    nano ~/vvs/lora/gate_listener.py   # SERIAL_PORT
    nano ~/vvs/lora/lora_sync_server.py # SERIAL_PORT

3. Restart services:
    sudo systemctl restart vvs-listener vvs-sync

## Add Test Data

    cd ~/vvs/scripts
    python3 add_test_data.py vehicle "Alice Wanjiku" "KDA 111A" car
    python3 add_test_data.py vehicle "Brian Otieno" "KDB 222B" bike
    python3 add_test_data.py vehicle "Carol Mwangi" "KDC 333C" truck
    python3 add_test_data.py summary

## Generate QR Code for Testing

    cd ~/vvs/scripts
    python3 -c "
    import sqlite3, os, qrcode
    DB = os.path.expanduser('~/vvs/database/vvs.db')
    c = sqlite3.connect(DB)
    row = c.execute('SELECT vid, qr_token FROM vehicles LIMIT 1').fetchone()
    payload = f'{row[0]}|{row[1]}|test-sig'
    img = qrcode.make(payload)
    img.save(os.path.expanduser('~/vvs/test_qr.png'))
    print('Saved:', payload)
    "

## Common Commands

| Task | Command |
|---|---|
| Restart dashboard | `sudo systemctl restart vvs-dashboard` |
| Restart listener | `sudo systemctl restart vvs-listener` |
| Restart sync | `sudo systemctl restart vvs-sync` |
| Live listener log | `journalctl -u vvs-listener -f` |
| Live sync log | `journalctl -u vvs-sync -f` |
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