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

The ESP-12E bridge in `esp8266_lora_bridge/` owns the SX1278 radio. It operates
on CH1 at 433.1 MHz with SF12, 125 kHz bandwidth, coding rate 4/5, and 20 dBm
transmit power. The ESP32-CAM in this project communicates with that bridge
over UART and does not configure the radio.

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

## Arduino IDE

A standalone Arduino IDE sketch is in
[`arduino/gate_node/gate_node.ino`](arduino/gate_node/gate_node.ino). It combines
the PlatformIO `main.cpp` and QR decoder implementation and inlines the
255-byte LoRa packet limit, so it does not need the project `include/` or
`src/` folders. It retains the QR verification, serial test commands, UART
bridge protocol, and status messages.

### Install the ESP32 board package and libraries

1. In Arduino IDE, open **File > Preferences** and add this URL to **Additional
   Boards Manager URLs**:
   `https://espressif.github.io/arduino-esp32/package_esp32_index.json`
2. Open **Tools > Board > Boards Manager**, search for **esp32**, and install
   **esp32 by Espressif Systems**.
3. Open **Sketch > Include Library > Manage Libraries** and install:
   - **Arduino_JSON** by Arduino
   - **ESP32QRCodeReader** by Alvaro Wolfx

   If `ESP32QRCodeReader` is not listed in Library Manager, download its ZIP
   from [the library repository](https://github.com/alvarowolfx/ESP32QRCodeReader)
   and use **Sketch > Include Library > Add .ZIP Library**. The QR reader
   library includes its modified QR decoder; no separate `quirc` installation
   is needed.

### Board and sketch settings

Open `arduino/gate_node/gate_node.ino` in Arduino IDE. Select:

| Setting | Value |
|---|---|
| Board | **AI Thinker ESP32-CAM** |
| Upload Speed | **115200** (increase only after a successful baseline upload) |
| CPU Frequency | **240MHz (WiFi/BT)** |
| Flash Frequency | **80MHz** |
| Flash Mode | **QIO** |
| Partition Scheme | **Huge APP (3MB No OTA/1MB SPIFFS)** |
| PSRAM | **Enabled** |
| Port | The serial/COM port for the FTDI adapter |

Menu wording can vary slightly with the installed Espressif ESP32 board-package
version. The camera QR decoder requires working PSRAM.

The PlatformIO-specific differences are already handled in the sketch:
`main.cpp` and `qr_decoder.cpp` are combined into one `.ino`, the local QR
decoder header is no longer needed, and `lora_packet_limits.h` is replaced by
the same 255-byte limit and a small validation function. `Arduino_JSON` and
`ESP32QRCodeReader` remain required libraries.

### FTDI wiring and upload

Use a USB-to-TTL adapter with **3.3V UART logic**. The ESP32-CAM may be powered
through its **5V** pin with a stable supply capable of at least 500 mA (1 A is
recommended). Many FTDI adapters cannot supply enough current from their 3.3V
output; do not use that output to power the camera board.

| FTDI adapter | AI-Thinker ESP32-CAM |
|---|---|
| TXD | U0R / GPIO 3 (RX0) |
| RXD | U0T / GPIO 1 (TX0) |
| GND | GND |
| 5V supply output, if adequately rated | 5V |
| — | GPIO 0 to GND **only while entering flash mode** |

Keep FTDI TX/RX at 3.3V logic even when powering the board from 5V. Do not
connect the FTDI's 5V logic output to ESP32 pins. Avoid powering the board
simultaneously from two 5V sources.

1. Disconnect the bridge/peripherals from the ESP32-CAM and check that FTDI TX
   and RX are crossed as shown above, with a shared ground.
2. Connect GPIO 0 to GND.
3. Apply power or pulse the ESP32-CAM **RESET** button. Keep GPIO 0 grounded
   while the board enters its bootloader.
4. In Arduino IDE, select the **AI Thinker ESP32-CAM** board and the FTDI COM
   port, then click **Upload**. If the upload process waits at `Connecting...`,
   press and release **RESET** once.
5. When upload completes, disconnect GPIO 0 from GND and press **RESET** (or
   power-cycle) to run the sketch.
6. Open Serial Monitor at **115200 baud**. The bridge UART uses GPIO 13 (RX)
   and GPIO 14 (TX), at 115200 baud, and shares pins with the unused microSD
   interface.

### If upload still reports “Failed to connect to ESP32: No serial data received”

That message occurs before the firmware runs, so changing the sketch usually
does not fix it. Check these items:

- **Bootloader mode:** GPIO 0 must be LOW when the ESP32-CAM resets. Verify the
  GPIO 0-to-GND jumper and press RESET after starting the upload.
- **Power:** use a stable 5V supply to the board's 5V pin and a common ground.
  A weak FTDI 3.3V/5V output, long thin leads, or a brownout can prevent the
  bootloader from responding.
- **UART wiring:** FTDI TXD goes to U0R/GPIO 3; FTDI RXD goes to U0T/GPIO 1.
  Confirm the adapter uses 3.3V UART levels.
- **Port/driver:** select the port that appears when the adapter is connected,
  install its USB-serial driver if needed, and close other serial monitors or
  applications using that port.
- **Reset/strapping pins:** try a shorter cable, another USB port, and manual
  RESET timing. Disconnect attached circuitry that may drive GPIO 0, GPIO 1,
  GPIO 3, or GPIO 12 during startup.
- **Upload speed:** keep 115200 baud until upload works reliably; try 57600
  only if the connection is marginal.
- **Board/flash settings:** verify AI Thinker ESP32-CAM, QIO flash mode, and
  PSRAM enabled. These settings matter for runtime/firmware layout; they do
  not replace the GPIO 0 bootloader procedure.

After a successful flash, remove the GPIO 0 jumper before resetting or the
board will return to download mode instead of running the firmware.
