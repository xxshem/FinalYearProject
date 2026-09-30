#!/usr/bin/env python3
"""Initialize the VVS database and create a repeatable demo vehicle."""
import os
import secrets
import sqlite3
import sys
from datetime import datetime, timedelta
from pathlib import Path

VVS_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, VVS_DIR)

from scripts.config import DB_PATH
from scripts.init_db import init
from scripts.qr_handler import QRHandler

DEMO_REGISTRATION = "DEMO-001"


def ensure_signing_key():
    env_path = Path.home() / "vvs.env"
    lines = env_path.read_text(encoding="utf-8").splitlines() if env_path.exists() else []
    key_line = next((index for index, line in enumerate(lines)
                     if line.partition("=")[0].strip() == "VVS_QR_SECRET"), None)
    file_secret = None
    if key_line is not None:
        file_secret = lines[key_line].partition("=")[2].strip().strip("\"'")

    secret = os.environ.get("VVS_QR_SECRET") or file_secret
    created = not secret
    if not secret:
        secret = secrets.token_hex(32)
    os.environ["VVS_QR_SECRET"] = secret

    if file_secret != secret:
        env_path.parent.mkdir(parents=True, exist_ok=True)
        secret_line = f"VVS_QR_SECRET={secret}"
        if key_line is None:
            lines.append(secret_line)
        else:
            lines[key_line] = secret_line
        env_path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
    try:
        env_path.chmod(0o600)
    except OSError:
        pass
    if created:
        print(f"[OK] Created persistent QR signing key at {env_path}")


def init_demo():
    ensure_signing_key()
    init()

    connection = sqlite3.connect(DB_PATH)
    try:
        row = connection.execute(
            "SELECT vid, qr_token, vehicle_id FROM vehicles WHERE registration_no=?",
            (DEMO_REGISTRATION,),
        ).fetchone()
        if row is None:
            vid = secrets.token_hex(16)
            token = secrets.token_urlsafe(18)
            today = datetime.now().date()
            cursor = connection.execute("""INSERT INTO vehicles
                (vid, owner_name, registration_no, vehicle_type, qr_token,
                 valid_from, valid_to, is_active)
                VALUES (?, ?, ?, ?, ?, ?, ?, 1)""",
                (vid, "VVS Demo User", DEMO_REGISTRATION, "bike", token,
                 today.isoformat(), (today + timedelta(days=365)).isoformat()))
            vehicle_id = cursor.lastrowid
            connection.commit()
        else:
            vid, token, vehicle_id = row
            today = datetime.now().date()
            connection.execute("""UPDATE vehicles SET is_active=1,
                valid_from=?, valid_to=? WHERE vehicle_id=?""",
                (today.isoformat(), (today + timedelta(days=365)).isoformat(), vehicle_id))
            connection.commit()
    finally:
        connection.close()

    payload = f"{vid}|{token}|{QRHandler().generate_signature(vid, token)}"
    print(f"[OK] Demo vehicle {DEMO_REGISTRATION} ready (vehicle_id={vehicle_id})")
    print(f"QR payload: {payload}")
    try:
        import qrcode
        image_path = Path(DB_PATH).parent / "demo_qr.png"
        qrcode.make(payload).save(image_path)
        print(f"QR image: {image_path}")
    except ImportError:
        print("[!] Install qrcode[pil] to generate a QR image; payload is ready above.")


if __name__ == "__main__":
    init_demo()