# Gate Node — ESP32-CAM + SX1278

## Role
Standalone gate scanner. Reads signed QR codes, sends verification requests
over LoRa CH1 (868.1 MHz), retries timed-out requests with the same sequence
number, and shows the result via LCD, LED, and buzzer.

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

The LCD and PCF8574 expander share I2C:

| I2C device pin | ESP32-CAM |
|---|---|
| SDA | GPIO 16 |
| SCL | GPIO 0 |
| VCC | 3.3V |
| GND | GND |

| I2C device | Address | Connection |
|---|---|---|
| 16x2 LCD | `0x27` | I2C SDA/SCL |
| PCF8574 | `0x20` | I2C SDA/SCL |

| PCF8574 output | Signal | Active level |
|---|---|---|
| P0 | Green LED | LOW |
| P1 | Red LED | LOW |
| P2 | Buzzer | LOW |

Connect each LED with a series resistor. Confirm the buzzer current is within
the PCF8574 output rating; use a transistor driver if it is not.

**Pin conflict to resolve before wiring:** the AI-Thinker camera model used by
this firmware assigns GPIO 0 to camera XCLK, while the firmware also assigns it
to I2C SCL. GPIO 16 may also be reserved for PSRAM on some ESP32-CAM revisions.
Verify the board variant and move I2C to available pins before building the
hardware; update these assignments in `src/main.cpp` at the same time.

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
Paste a full signed QR payload (`vid|token|signature`) into the serial monitor
and press Enter. Unsigned token-only input is intentionally denied.

## Configuration
Edit GATE_ID in src/main.cpp to change this node's ID (e.g., "GATE_B").