# CMS1 LoRa Gateway — ESP32 Dev + SX1278

## Role
Time-shared dual-channel modem. Receives gate traffic on CH1 (868.1 MHz)
and syncs DB with CMS2 on CH2 (868.5 MHz). Hops every 100 ms.

## Pins
| SX1278 | ESP32 Dev |
|---|---|
| VCC    | 3.3V  |
| GND    | GND   |
| SCK    | GPIO 18 |
| MISO   | GPIO 19 |
| MOSI   | GPIO 23 |
| NSS    | GPIO 5  |
| RESET  | GPIO 14 |
| DIO0   | GPIO 2  |

## Build / Upload
    pio run
    pio run --target upload

## Monitor
    pio device monitor

Expected:
    [GW1] ready, hopping CH1<->CH2 @100ms

## USB Protocol
Host -> modem : "C1:<payload>\n"  or  "C2:<payload>\n"
Modem -> host : "C1:<payload>\n"  or  "C2:<payload>\n"

## Python Side (on Raspberry Pi)
    ~/vvs/lora/gate_listener.py
    ~/vvs/lora/lora_sync_server.py