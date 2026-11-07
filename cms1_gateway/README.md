# CMS1 LoRa Gateway — ESP32 Dev + SX1278

## Role
Single-radio modem. Listens continuously for gate traffic on CH1 (433.1 MHz)
and briefly switches to CH2 (433.5 MHz) for explicit sync frames. CH1 uses
SF12; CH2 also uses SF12. Both channels use 125 kHz bandwidth, coding rate
4/5, and 20 dBm transmit power. The CMS1 listener process exclusively owns USB
serial.

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
    [GW1] ready on CH1 433.1 MHz; CH2 433.5 MHz, SF12

## USB Protocol
Host -> modem : "C1:<payload>\n", "C2:<payload>\n", or "C2W:<payload>\n"
Modem -> host : "C1:<rssi>,<snr>:<payload>\n" or "C2:<payload>\n"

`C2W` is used for `SYNC_END`. It transmits on CH2, then waits up to 15 seconds
for a `SYNC_ACK` whose transfer ID matches the active END frame. Unrelated or
malformed packets are ignored without ending the wait. The host deadline
includes the END frame's estimated airtime, the firmware receive window, and a
serial scheduling margin.

## Python Side (on Raspberry Pi)
    ~/vvs/lora/gate_listener.py
    ~/vvs/lora/lora_sync_server.py

The listener owns the serial device and imports the sync publisher; do not run
`lora_sync_server.py` as a second serial process.