# ESP-12E LoRa Bridge and System Controller

The NodeMCU ESP-12E owns the SX1278 radio, 16×2 LCD, PCF8574 output
expander, two LEDs, and buzzer. It receives UART requests and status events
from the ESP32-CAM. The verification JSON sent over LoRa and the existing
LoRa reply behavior are unchanged.

## Final pin allocation

| Device / signal | NodeMCU pin | ESP8266 GPIO | Notes |
|---|---:|---:|---|
| LCD + output expander SDA | D3 | GPIO 0 | I²C SDA |
| LCD + output expander SCL | D4 | GPIO 2 | I²C SCL |
| SX1278 SCK | D5 | GPIO 14 | Hardware SPI |
| SX1278 MISO | D6 | GPIO 12 | Hardware SPI |
| SX1278 MOSI | D7 | GPIO 13 | Hardware SPI |
| SX1278 NSS / CS | D1 | GPIO 5 | Explicitly assigned in firmware |
| SX1278 RESET | D0 | GPIO 16 | Explicitly assigned in firmware |
| SX1278 DIO0 | D2 | GPIO 4 | Explicitly assigned in firmware |
| ESP32-CAM UART TX → NodeMCU RX | RX | GPIO 3 | UART0, 115200 baud |
| NodeMCU UART TX → ESP32-CAM RX | TX | GPIO 1 | UART0, 115200 baud |

**Pin-conflict decision:** the initial D1/D2 I²C proposal conflicts with the
existing SX1278 NSS and DIO0 assignments. The firmware keeps the established
LoRa pins and moves I²C to D3/D4. GPIO 0 and GPIO 2 are ESP8266 boot-strapping
pins; the I²C bus must be pulled high at reset. Use the PCF8574 modules'
pull-ups only if they pull up to 3.3 V and leave both lines high while idle.
If either line can be held low during reset, add suitable 3.3 V pull-ups or
choose a different validated wiring arrangement before powering the board.

GPIO 1/3 are also used for USB programming. Disconnect the inter-board UART
signals while uploading firmware. Connect grounds and use 3.3 V UART logic.
The former optional UART1 diagnostics on GPIO 2 are no longer available there,
because GPIO 2 is now the I²C clock pin.

## Peripherals

### SX1278

| SX1278 label | NodeMCU |
|---|---|
| VCC | 3V3 |
| GND | GND |
| SCLK / SLCK | D5 / GPIO 14 |
| MISO | D6 / GPIO 12 |
| MOSI | D7 / GPIO 13 |
| NSS / CS | D1 / GPIO 5 |
| REST / RESET | D0 / GPIO 16 |
| DIO0 | D2 / GPIO 4 |

Leave DIO1, DIO2, and DIO3 unconnected. Use a regulated 3.3 V supply sized
for the ESP8266 and radio's transmit current. Do not connect the radio to 5 V
unless its documentation explicitly confirms 5 V tolerance.

### LCD and output expander

Both I²C devices share D3/D4:

| I²C device | Address | Function |
|---|---:|---|
| 16×2 LCD with PCF8574 backpack | `0x27` | Display |
| PCF8574 output expander | `0x20` | LEDs and buzzer |

The output expander retains the original active-low mapping:

| PCF8574 output | Signal | Active level |
|---|---|---|
| P0 | Green LED | LOW |
| P1 | Red LED | LOW |
| P2 | Buzzer | LOW |

Use a series resistor for each LED. Use a transistor driver if the buzzer
exceeds the PCF8574 output rating. Power the I²C devices and pull-ups at 3.3 V
or use proper level shifting; ESP8266 GPIOs are not 5 V tolerant.

### ESP32-CAM UART

| ESP32-CAM | NodeMCU |
|---|---|
| GPIO 14 / UART2 TX | RX / GPIO 3 |
| GPIO 13 / UART2 RX | TX / GPIO 1 |
| GND | GND |

The microSD interface on the ESP32-CAM must remain unused because its GPIO
13/14 pins are assigned to UART.

## UART protocol

Messages are newline-delimited at 115200 baud. The established verification
protocol remains:

- ESP32-CAM → NodeMCU: `REQ:<JSON>\n`
- NodeMCU → ESP32-CAM: `RSP:<JSON>\n` or `ERR:<reason>\n`

The bridge forwards the JSON body (without the UART prefix) over LoRa, then
returns the LoRa reply unchanged in an `RSP:` frame. It reports radio/request
errors using the existing `ERR:` format, including `BAD_REQUEST`,
`RADIO_INIT_FAILED`, `TX_FAILED`, and `TIMEOUT`.

The ESP32-CAM sends local UI events using `STATUS:<name>[:<detail>]\n`:

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

The bridge accepts partial UART frames, discards overlong frames, and resets
stalled partial frames after one second (`ERR:FRAME_TOO_LONG` or
`ERR:FRAME_TIMEOUT`). Malformed status/command frames receive `ERR:BAD_STATUS`
or `ERR:BAD_COMMAND`.

## LoRa settings

- Frequency: 433.1 MHz
- Spreading factor: SF12
- Bandwidth: 125 kHz
- Coding rate: 4/5
- TX power: 20 dBm
- Reply timeout: 6 seconds

All peer radios must use matching frequency and modulation parameters.
Confirm the selected frequency and transmit power are permitted locally.

## Build and upload

Run in this directory:

```text
pio run
pio run --target upload
```
