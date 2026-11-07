#!/bin/bash
set -e

USER_NAME="$(id -un)"
KIT_DIR="$HOME/cms1-deploy-kit"
SYNC_ENV_FILE="$HOME/vvs-sync-client.env"

if [ ! -f "$HOME/vvs/lora/lora_sync_client.py" ]; then
    echo "Copy cms1-deploy-kit and its vvs directory to this account's home first."
    exit 1
fi
if [ ! -f "$SYNC_ENV_FILE" ]; then
    echo "Configure VVS_SERIAL_PORT in $SYNC_ENV_FILE before installing."
    echo "Prefer the adapter path under /dev/serial/by-id/."
    exit 1
fi
if ! grep -Eq '^VVS_SERIAL_PORT=/dev/[^[:space:]]+$' "$SYNC_ENV_FILE"; then
    echo "Configure VVS_SERIAL_PORT in $SYNC_ENV_FILE before installing."
    echo "Prefer the adapter path under /dev/serial/by-id/."
    exit 1
fi

sudo apt-get update -y
sudo apt-get install -y python3-venv
python3 -m venv "$HOME/vvs-venv"
"$HOME/vvs-venv/bin/pip" install --upgrade pip pyserial
python3 "$HOME/vvs/scripts/init_db.py" --mirror-only

sudo cp "$KIT_DIR/systemd/vvs-sync-client@.service" /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now "vvs-sync-client@${USER_NAME}.service"

echo "CMS2 sync receiver enabled for ${USER_NAME}."
echo "Check it with: sudo systemctl status vvs-sync-client@${USER_NAME}"