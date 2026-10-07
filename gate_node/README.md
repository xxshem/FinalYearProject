# VVS Gate Node — ESP32-CAM

## Role

The ESP32-CAM owns the OV2640 camera, QR decoding, verification request
construction, and the existing signed-payload verification flow. It sends
verification requests and UI/status events to the ESP-12E over UART. The
ESP-12E owns the LCD, indicators, buzzer, and SX1278.

## ESP32-CAM wiring

| Signal | ESP32-CAM connection | Notes |
|---|---|---|
| Camera | OV2640 camera connector | AI-Thinker camera pin map managed by `ESP32QRCodeReader` |
| UART TX to bridge | GPIO 14 (UART2 TX) | Connect to NodeMCU GPIO 3 / RX |
| UART RX from bridge | GPIO 13 (UART2 RX) | Connect to NodeMCU GPIO 1 / TX |
| Ground | GND | Common ground with NodeMCU |

Do not use the microSD slot; GPIO 13/14 are shared with that interface. UART
is 115200 baud, 8-N-1, with 3.3 V logic. Disconnect the two UART signal wires
when programming either board because the NodeMCU GPIO 1/3 pins are also its
USB programming UART.

The ESP32-CAM no longer connects to or controls the LCD, PCF8574 output
expander, LEDs, or buzzer. This keeps those peripherals away from the camera
and its pin assignments.

## UART protocol

All messages are newline-terminated. Existing verification traffic is
unchanged:

- ESP32-CAM → NodeMCU: `REQ:<JSON>\n`
- NodeMCU → ESP32-CAM: `RSP:<JSON>\n` or `ERR:<reason>\n`

The JSON request body is still limited to 255 bytes, excluding the `REQ:`
prefix and newline. The bridge forwards that same JSON body over LoRa.

The ESP32-CAM also sends these local display/feedback events to the NodeMCU:

```text
STATUS:INITIALIZING
STATUS:CAMERA_READY
STATUS:CAMERA_ERROR
STATUS:READY
STATUS:GATE_ID_SET:<gate-id>
STATUS:GATE_ID:<gate-id>
STATUS:CHECKING:<token>
STATUS:REQUEST_TOO_LARGE
STATUS:VERIFIED:<registration>
STATUS:REJECTED:<registration>
```

For an empty registration, the final colon and detail are omitted. A timeout
uses `STATUS:REJECTED:Timeout`. Status messages do not change the LoRa JSON
request/reply application protocol.

## Build, upload, monitor

From this directory:

```text
pio run
pio run --target upload
pio device monitor
```

The USB serial monitor remains the ESP32-CAM programming/diagnostics UART.
Bench testing also accepts a full signed QR payload (`vid|token|signature`)
typed into the serial monitor. Unsigned token-only input is intentionally
denied.

To change the gate ID at runtime, send `SET_GATE=<id>` or `WHOAMI` over the
ESP32-CAM USB serial monitor.
