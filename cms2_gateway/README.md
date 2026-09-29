# CMS2 LoRa Gateway — ESP32 Dev + SX1278

## Role
Single-channel modem locked to CH2 (868.5 MHz), using spreading factor 7 to
match CMS1 sync frames. Receives database sync from CMS1. Plain passthrough,
with no channel prefix in the USB protocol.

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

The CMS2 Linux host must run the receiver service while this gateway is
connected over USB; see `cms1-deploy-kit/README-CMS1.md`.