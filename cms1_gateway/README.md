# CMS1 LoRa Gateway — ESP32 Dev + SX1278

## Role
Single-radio modem. Prioritizes gate traffic on CH1 (433.1 MHz) and performs
bounded, asynchronous sync transmissions on CH2 (433.5 MHz). Both channels use
SF7, 125 kHz bandwidth, coding rate 4/5, CRC, and 2 dBm transmit power. The
CMS1 listener process exclusively owns USB serial.

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
    [GW1] ready on CH1 433.1 MHz; CH2 433.5 MHz, SF7

## USB Protocol
Host -> modem : "C1:<payload>\n", "C2:<payload>\n", or "C2W:<payload>\n"
Modem -> host : "C1:<rssi>,<snr>:<payload>\n" or "C2:<payload>\n"

The firmware uses explicit `CH1_GATE_LISTEN`, `CH2_SYNC_TX`,
`CH2_ACK_WAIT`, `CH1_GATE_SERVICE`, and `RADIO_RECOVERY` states. Each sync
frame is transmitted asynchronously, then the radio returns to CH1; only
`C2W` (`SYNC_END`) opens a bounded 1000 ms CH2 ACK window. The ACK must match
the active transfer ID. Missing, stale, or mismatched ACKs do not advance the
CMS1 durable cursor; the publisher retries the same transfer identity and CMS2
reconciles the replay transactionally.

A single radio cannot hear CH1 while transmitting or tuned to CH2. A gate
request arriving during a sync transmission or ACK window can be missed; the
gate bridge's request retry is the recovery mechanism. The gate retries the
same boot/sequence identity up to three times when the bridge reports a radio
timeout; the bridge's receive window is 6000 ms and CMS1's request cache
prevents a successfully repeated request from applying the gate action twice.
Thus, a request lost during CH2 may be delayed until the bridge timeout.

TX completion is detected by the LoRa TX-done interrupt with a 1500 ms
fallback timeout, radio reinitialization, and at most one retransmission per
command. Repeated failures are reported over USB. The host airtime estimate
uses the configured SF7/CRC-enabled radio profile.

For hardware acceptance, record a fixed set of scans with sync disabled and
the same number during maximum-size sync batches. A gate-side test harness
must measure scan-to-result latency and count initiated scans, retries, and
final failures; compare those with CMS1's recorded `response_time_ms`
p50/p95/max, which measures CMS1 request handling rather than end-to-end RF
latency. The firmware build cannot measure RF losses or prove these rates.

## Python Side (on Raspberry Pi)
    ~/vvs/lora/gate_listener.py
    ~/vvs/lora/lora_sync_server.py

The listener owns the serial device and imports the sync publisher; do not run
`lora_sync_server.py` as a second serial process.