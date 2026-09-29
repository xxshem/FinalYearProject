#!/bin/bash
# VVS CMS1 Install Script
# Run on Raspberry Pi after cloning/copying this kit into ~
set -e

echo "================================================"
echo "  VVS CMS1 — Raspberry Pi Installer"
echo "================================================"

# 1. Verify we are on the Pi
if [ ! -d "$HOME/vvs" ]; then
    echo "[!] Expected ~/vvs to exist. Copy this kit to ~ first."
    exit 1
fi

# 2. System packages
echo "[1/6] Installing system packages..."
sudo apt-get update -y
sudo apt-get install -y python3-pip python3-venv sqlite3

# 3. Python venv (cleaner than system pip)
echo "[2/6] Creating Python virtual environment..."
cd ~
python3 -m venv vvs-venv
source ~/vvs-venv/bin/activate
pip install --upgrade pip
pip install -r ~/cms1-deploy-kit/requirements.txt

# 4. Serial port permissions
echo "[3/6] Adding user to dialout group for serial access..."
sudo usermod -aG dialout pi

# Create a private per-install signing key shared by the dashboard and listener.
if [ ! -f "$HOME/vvs.env" ]; then
    umask 077
    printf 'VVS_QR_SECRET=%s\n' "$(python3 -c 'import secrets; print(secrets.token_hex(32))')" > "$HOME/vvs.env"
fi
chmod 600 "$HOME/vvs.env"

# 5. Initialize database
echo "[4/6] Initializing SQLite database..."
python ~/vvs/scripts/init_db.py

# 6. Install systemd services
echo "[5/6] Installing systemd services..."
for svc in vvs-sync vvs-sysc; do
    sudo systemctl disable --now "${svc}.service" 2>/dev/null || true
    sudo rm -f "/etc/systemd/system/${svc}.service"
done
sudo cp ~/cms1-deploy-kit/systemd/vvs-dashboard.service /etc/systemd/system/
sudo cp ~/cms1-deploy-kit/systemd/vvs-listener.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable vvs-dashboard vvs-listener
echo "[6/6] CMS1 services configured (one process owns the LoRa serial port)."

echo ""
echo "================================================"
echo "  Installation complete!"
echo "================================================"
echo ""
echo "Next steps:"
echo "  1. Reboot: sudo reboot"
echo "  2. After boot, verify services:"
echo "       systemctl status vvs-dashboard"
echo "       systemctl status vvs-listener"
echo "  3. Open dashboard: http://<pi-ip>:5000"
echo ""