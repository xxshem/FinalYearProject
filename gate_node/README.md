# Gate Node — ESP32-CAM + SX1278

## Role
Standalone gate scanner. Reads QR codes, sends verification requests
over LoRa CH1 (868.1 MHz), shows result via LED + buzzer.

## Pins
| SX1278 | ESP32-CAM |
|---|---|
| VCC    | 3.3V       |
| GND    | GND        |
| SCK    | GPIO 14    |
| MISO   | GPIO 2     |
| MOSI   | GPIO 15    |
| NSS    | GPIO 13    |
| RESET  | GPIO 12    |
| DIO0   | GPIO 4     |

| Device     | Pin |
|---|---|
| LED Green  | GPIO 33 |
| LED Red    | GPIO 32 |
| Buzzer     | GPIO 25 |

## Build
    pio run

## Upload
    pio run --target upload

## Monitor
    pio device monitor

Expected:
    [GATE] booting
    [QR] camera ready (ESP32QRCodeReader)
    [LORA] ready on 868.1 MHz
    [GATE] ready

## Bench test (no QR)
Type a token in the serial monitor and press Enter. The node will
send it as if it were scanned.

## Configuration
Edit GATE_ID in src/main.cpp to change this node's ID (e.g., "GATE_B").