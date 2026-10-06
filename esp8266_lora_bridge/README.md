# ESP-12E SX1278 LoRa Bridge

This firmware runs on an ESP-12E / ESP8266MOD and owns the SX1278 radio. It
receives newline-delimited `REQ:<JSON>` frames from the ESP32-CAM, transmits
the JSON over LoRa, waits up to six seconds for a reply, and returns
`RSP:<JSON>` or `ERR:<reason>` over UART. UART protocol data is on GPIO 1/3;
diagnostics are sent separately on GPIO 2 (UART1 TX-only).

## Wiring

### SX1278 module to ESP-12E

| SX1278 label | ESP-12E GPIO |
|---|---:|
| VCC | 3V3 |
| GND (either) | GND |
| SCLK / SLCK | GPIO 14 |
| MISO | GPIO 12 |
| MOSI | GPIO 13 |
| NSS | GPIO 5 |
| REST / RESET | GPIO 16 |
| DIO0 | GPIO 4 |

Leave DIO1, DIO2, and DIO3 unconnected. Use 3.3 V logic and a regulated supply
that can handle the ESP8266 and radio's transmit current. Join the grounds.
Do not connect the SX1278 to 5 V unless the module documentation explicitly
confirms 5 V tolerant power and logic.

The SPI bus uses the ESP8266 Arduino core's default hardware SPI pins
(GPIO 14/12/13); NSS is explicitly assigned to GPIO 5. GPIO 16 is used only
for radio reset.

### ESP32-CAM UART

| ESP32-CAM | ESP-12E |
|---|---|
| GPIO 14 (UART TX) | GPIO 3 (UART RX) |
| GPIO 13 (UART RX) | GPIO 1 (UART TX) |
| GND | GND |

The UART runs at 115200 baud. On the ESP32-CAM, GPIO 13/14 are also used by
the microSD interface, so do not use the microSD slot. The ESP-12E GPIO 1/3
are also its programming UART pins: disconnect the inter-board TX/RX wires
when uploading firmware to either board. Keep grounds connected.

GPIO 2 carries optional UART1 diagnostic output. Connect a 3.3 V USB-serial
adapter's RX to GPIO 2 and its GND to ESP-12E GND to view logs; do not connect
the adapter's TX pin. Keep GPIO 2 high at reset as required by ESP8266 boot
strapping.

## LoRa settings

- Frequency: 433.1 MHz
- Spreading factor: SF12
- Bandwidth: 125 kHz
- Coding rate: 4/5
- TX power: 20 dBm
- Reply timeout: 6 seconds

All peer radios must use matching frequency and modulation parameters.
Confirm that the selected frequency and transmit power are permitted locally.

## Build and upload

Run in this directory:

```text
pio run
pio run --target upload
```
