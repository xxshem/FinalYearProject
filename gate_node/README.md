# Gate Node — ESP32-CAM + SX1278

## Role
Standalone gate scanner. Reads signed QR codes, sends verification requests
over UART to the ESP-12E LoRa bridge, retries timed-out requests with the same
sequence number, and shows the result via LCD, LED, and buzzer. The ESP-12E
bridge sends requests over LoRa CH1 (433.1 MHz).

## Pins
Do not connect the SX1278 directly to the ESP32-CAM. Connect the radio to the
ESP-12E using the pin map in [`esp8266_lora_bridge/README.md`](../esp8266_lora_bridge/README.md).

The UART bridge uses ESP32-CAM GPIO 13 (RX) and GPIO 14 (TX); leave the
microSD slot unused. Connect ESP32 GPIO 14 (TX) to ESP8266 GPIO 3 (RX), ESP32
GPIO 13 (RX) to ESP8266 GPIO 1 (TX), and connect grounds. The inter-board UART
runs at 115200 baud. Disconnect those two UART wires while programming the
ESP-12E, because GPIO 1/3 are also its programming UART pins.
The ESP32-CAM bridge uses UART2, separate from its USB programming UART.

Before sending a verification request, `gate_node` checks the serialized JSON
body against the SX1278's 255-byte packet limit. The `REQ:` UART prefix and
newline are not included in that limit. Oversized JSON is rejected locally,
shown on the LCD, and is not retried or sent to the ESP-12E.

For the AI-Thinker-style ESP32-CAM module with OV2640 and 4 MB PSRAM (the
ESP32-CAM-MB is the USB programmer carrier), the LCD and PCF8574 expander share
I2C on GPIO 2 and GPIO 4. Do not use the microSD slot; GPIO 13/14 are used for
the ESP-12E UART.

| I2C device pin | ESP32-CAM |
|---|---|
| SDA | GPIO 2 |
| SCL | GPIO 4 |
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

GPIO 2 is a boot-strapping pin, so disconnect the LCD and PCF8574 from the I2C
bus while serial-flashing the ESP32-CAM. Verify the board boots normally with
the I2C devices reconnected. GPIO 4 is also connected to the onboard camera
flash LED; disable or physically disconnect that LED before using GPIO 4 as
I2C SCL. GPIO 16 is reserved for PSRAM on this module and is not used for I2C.
The camera's GPIO 0 XCLK pin is no longer assigned to I2C.

**Hardware validation is still required:** after wiring, confirm the camera
initializes and scan the I2C bus to verify that the LCD (`0x27`) and PCF8574
(`0x20`) both respond while the camera is operating. A successful PlatformIO
build does not verify pin-level compatibility or physical bus operation.

## Build
    pio run

## Upload
    pio run --target upload

## Monitor
    pio device monitor

Expected on the ESP32-CAM USB serial monitor:
    [GATE] booting
    [QR] camera ready (ESP32QRCodeReader)
    [BRIDGE] ESP-12E UART ready
    [GATE] ready

The ESP-12E bridge is a separate PlatformIO project. Build/upload it from
`esp8266_lora_bridge` with `pio run` and `pio run --target upload`. Its
diagnostics are on ESP-12E GPIO 2 (UART1 TX-only), separate from the
request/reply UART. The bridge expects newline-delimited `REQ:<JSON>` frames
and returns `RSP:<JSON>` or `ERR:<reason>`.

## Bench test (no QR)
Paste a full signed QR payload (`vid|token|signature`) into the serial monitor
and press Enter. Unsigned token-only input is intentionally denied.

## Configuration
Edit GATE_ID in src/main.cpp to change this node's ID (e.g., "GATE_B").