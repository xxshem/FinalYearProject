#!/usr/bin/env python3
"""Assign a logical gate ID to the ESP32-CAM scanner over USB serial."""
import argparse
import os
import time

import serial


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--gate", required=True, choices=("A", "B", "C"),
                        help="logical gate to report for subsequent scans")
    parser.add_argument("--port", default=os.environ.get("VVS_GATE_SERIAL_PORT"),
                        help="scanner USB serial port (or set VVS_GATE_SERIAL_PORT)")
    args = parser.parse_args()
    if not args.port:
        parser.error("provide --port or set VVS_GATE_SERIAL_PORT")

    gate_id = f"GATE_{args.gate}"
    with serial.Serial(args.port, 115200, timeout=0.5, write_timeout=2) as connection:
        time.sleep(2)
        connection.write(f"SET_GATE={gate_id}\n".encode("ascii"))
        connection.flush()
        deadline = time.monotonic() + 8
        confirmed = False
        while time.monotonic() < deadline:
            response = connection.readline().decode("utf-8", errors="replace").strip()
            if response:
                print(response)
                if f"GATE_ID now = {gate_id}" in response:
                    confirmed = True
                    break
    if not confirmed:
        raise SystemExit(f"Scanner did not confirm {gate_id}; check its USB port and reboot state.")
    print(f"[OK] Scanner assigned to {gate_id}. Future scans will use this gate ID.")


if __name__ == "__main__":
    main()