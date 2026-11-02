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
| UART TX to bridge | U0T / GPIO 1 (UART0 TX) | Connect to NodeMCU GPIO 3 / RX |
| UART RX from bridge | U0R / GPIO 3 (UART0 RX) | Connect to NodeMCU GPIO 1 / TX |
| Ground | GND | Common ground with NodeMCU |

The inter-board link uses UART0 at 9600 baud, 8-N-1, with 3.3 V logic. UART0
is also the ESP32-CAM programming UART. Disconnect both UART signal wires
between boards while flashing, then reconnect them for runtime. The ESP32-CAM
USB-to-serial console is unavailable while connected: its U0R input would
otherwise be driven by both the USB adapter TX and NodeMCU TX. The NodeMCU
also shares its GPIO 1/3 UART pins with its onboard USB-to-serial interface;
disconnect NodeMCU USB while the inter-board link is active, or use a switch
to select the TX source. Never tie active TX outputs together.

The ESP32-CAM no longer connects to or controls the LCD, PCF8574 output
expander, LEDs, or buzzer. This keeps those peripherals away from the camera
and its pin assignments.

The ESP-12E bridge in `esp8266_lora_bridge/` owns the SX1278 radio. It operates
on CH1 at 433.1 MHz with SF12, 125 kHz bandwidth, coding rate 4/5, and 20 dBm
transmit power. The ESP32-CAM in this project communicates with that bridge
over UART and does not configure the radio.

## UART protocol

All messages are newline-terminated. The verification payload is unchanged;
the UART request envelope includes identifiers so retries and replies can be
correlated:

- ESP32-CAM → NodeMCU: `REQ:<boot-id>:<seq>:<JSON>\n`
- NodeMCU → ESP32-CAM: `RSP:<JSON>\n` or `ERR:<reason>\n`

The JSON request body is still limited to 255 bytes, excluding the `REQ:`
envelope and newline. The bridge forwards that same JSON body over LoRa.
Responses echo the request `boot` and `seq`; replies with another sequence are
discarded, and pending UART input is drained before the first attempt to
remove startup noise or an earlier transaction's leftover frame. The gate
waits up to 20 seconds per attempt and retries at most three times with the
same identifiers. Request-specific errors use
`ERR:<boot-id>:<seq>:<reason>\n`; uncorrelated framing/command errors use
`ERR:<reason>\n`. Retries reuse the same request sequence so the listener can
return its cached decision instead of processing the scan twice.
The ESP32-CAM ROM may emit boot text on UART0 at its bootloader baud; that
startup noise is not application data and is discarded before a scan request.

The ESP32-CAM also sends these local display/feedback events to the NodeMCU:

```text
STATUS:INITIALIZING
STATUS:CAMERA_READY
STATUS:CAMERA_ERROR
STATUS:READY
STATUS:CHECKING:<token>
STATUS:REQUEST_TOO_LARGE
STATUS:BRIDGE_ERROR:<reason>
STATUS:VERIFIED:<registration>
STATUS:REJECTED:<registration>
```

For an empty registration, the final colon and detail are omitted. UART or
radio failures use `STATUS:BRIDGE_ERROR:<reason>`. Status messages do not
change the LoRa JSON request/reply application protocol.
The ESP-12E continues to process status messages even if its LoRa radio fails
to initialize, allowing its LCD to report camera and gate state.

## Build and upload

From this directory, with the ESP32-CAM UART link disconnected:

```text
pio run
pio run --target upload
```

UART0 is shared by flashing and the 9600-baud bridge protocol. The gate does
not send diagnostic text or accept serial commands on this port, so it cannot
be used as a USB console while the bridge is connected. Verification starts
from the camera QR scanner. The standalone sketch uses the same UART0 application
link and has the same console limitation.
The gate ID is set in firmware (`GATE_ID`) and requires a rebuild/reflash to
change.

## Arduino IDE

A standalone Arduino IDE sketch is in
[`arduino/gate_node/gate_node.ino`](arduino/gate_node/gate_node.ino). It combines
the PlatformIO `main.cpp` and QR decoder implementation and inlines the
255-byte LoRa packet limit, so it does not need the project `include/` or
`src/` folders. It retains the QR verification, UART bridge protocol, and
status messages.

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
| Core Debug Level | **None** |
| Port | The serial/COM port for the FTDI adapter |

Menu wording can vary slightly with the installed Espressif ESP32 board-package
version. The camera QR decoder requires working PSRAM.

The PlatformIO-specific differences are already handled in the sketch:
`main.cpp` and `qr_decoder.cpp` are combined into one `.ino`, the local QR
decoder header is no longer needed, and `lora_packet_limits.h` is replaced by
the same 255-byte limit and a small validation function. Both variants use
UART0 at 9600 baud for the bridge, check camera setup, and share the same
request/reply behavior. `Arduino_JSON` and `ESP32QRCodeReader` remain required
libraries.

### USB-to-serial wiring and upload

Use a USB-to-TTL adapter with **3.3V UART logic**. Disconnect both bridge UART
signal wires before attaching the programming adapter. The ESP32-CAM may be
powered through its **5V** pin with a stable supply capable of at least 500 mA
(1 A is recommended). Many FTDI adapters cannot supply enough current from
their 3.3V output; do not use that output to power the camera board.

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

1. Disconnect the ESP-12E bridge UART wires from GPIO 1 and GPIO 3. Connect the
   programming adapter TX/RX as shown above, crossed, with a shared ground.
2. Connect GPIO 0 to GND.
3. Apply power or pulse the ESP32-CAM **RESET** button. Keep GPIO 0 grounded
   while the board enters its bootloader.
4. In Arduino IDE, select the **AI Thinker ESP32-CAM** board and the FTDI COM
   port, then click **Upload**. If the upload process waits at `Connecting...`,
   press and release **RESET** once.
5. When upload completes, disconnect GPIO 0 from GND and press **RESET** (or
   power-cycle) to run the sketch.
6. Disconnect the programming adapter, reconnect ESP32-CAM GPIO 1/3 to the
   bridge, and reset the board. The inter-board protocol then runs on UART0 at
   9600 baud; do not open a serial monitor while the bridge is connected.

### Upload troubleshooting

PlatformIO is configured not to pulse reset before or after flashing because
this ESP32-CAM setup uses manual bootloader entry. If upload reports
“Failed to connect to ESP32: No serial data received”, check these items:

- **Bootloader mode:** hold GPIO 0 to GND, reset the ESP32-CAM, and start the
  upload. Release GPIO 0 after flashing and reset once more to run the firmware.
  The no-reset upload settings do not automate this step.
- **Power:** use a stable 5V supply to the board's 5V pin and a common ground.
  Keep UART signals at 3.3V logic; do not power the camera from an under-rated
  adapter output.
- **Bridge wiring:** disconnect the ESP-12E bridge UART while flashing. It is
  connected to the same ESP32-CAM UART0 pins (GPIO 1/3) used for flashing.
- **Port/driver:** select the port that appears when the adapter is connected,
  install its USB-serial driver if needed, and close other applications using
  that port.
- **Strapping pins:** disconnect circuitry that may hold GPIO 0, 2, 5, 12, or
  15 at the wrong level during reset. GPIO 1/3 are used by UART0 and must be
  connected to the programming adapter only while flashing.
- **Upload speed:** use the configured 115200 baud.
- **Board/flash settings:** verify AI Thinker ESP32-CAM, QIO flash mode, and
  PSRAM enabled. These settings matter for runtime/firmware layout; they do
  not replace manual GPIO 0 bootloader entry.

After a successful flash, remove the GPIO 0 jumper before resetting or the
board will return to download mode instead of running the firmware.
