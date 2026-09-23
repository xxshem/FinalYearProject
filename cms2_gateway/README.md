# CMS2 LoRa Gateway — ESP32 Dev + SX1278

## Role
Single-channel modem locked to CH2 (868.5 MHz). Receives DB sync
from CMS1. Plain passthrough — no channel prefix in USB protocol.

## Pins
Same as cms1_gateway.

## Build / Upload
    pio run
    pio run --target upload

## Monitor
    pio device monitor

Expected:
    [GW2] ready on 868.5 MHz

## Python Side (on Linux PC)
    ~/vvs/lora/lora_sync_client.py