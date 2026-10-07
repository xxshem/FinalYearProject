# CMS2 LoRa Gateway — ESP8266 NodeMCU v3

## Role and hardware

This single-channel bridge connects a Linux PC host over USB serial to the
CMS1 Raspberry Pi over LoRa CH2. It uses an ESP8266 NodeMCU v3 and an
Ai-Thinker SX1278 RF LoRa PM1280 module, which operates in the 433 MHz band.

- CH2 frequency: **433.5 MHz**
- Spreading factor: **SF12**
- Bandwidth: **125 kHz**
- Coding rate: **4/5**
- Transmit power: **20 dBm**

Use a 433 MHz antenna and ensure the CMS1 gateway uses the same CH2 profile.

## Wiring: SX1278 to NodeMCU v3

| SX1278 module | NodeMCU v3 pin | GPIO | Note |
|---|---|---:|---|
| VCC | 3V3 | — | 3.3 V only |
| GND | GND | — | Common ground |
| SCK | D5 | 14 | Hardware SPI clock |
| MISO | D6 | 12 | Hardware SPI MISO |
| MOSI | D7 | 13 | Hardware SPI MOSI |
| NSS | D1 | 5 | Chip select |
| RESET | D0 | 16 | Radio reset |
| DIO0 | D2 | 4 | Interrupt-capable |

Do not connect the radio control pins to GPIO0, GPIO2, or GPIO15; those pins
affect ESP8266 boot mode.

```text
 SX1278 / PM1280                         NodeMCU v3
 +-------------+                         +--------------------+
 | VCC  o------+------------------------>| 3V3                |
 | GND  o------+------------------------>| GND                |
 | SCK  o------+------------------------>| D5  / GPIO14 (SCK) |
 | MISO o------+------------------------>| D6  / GPIO12       |
 | MOSI o------+------------------------>| D7  / GPIO13       |
 | NSS  o------+------------------------>| D1  / GPIO5        |
 | RESET o-----+------------------------>| D0  / GPIO16       |
 | DIO0 o------+------------------------>| D2  / GPIO4        |
 +-------------+                         +--------------------+
```

Check continuity for all eight connections and verify that no power pin is
shorted to ground before powering the boards.

## USB serial on Linux

Connect the NodeMCU to the Linux PC with a USB data cable. Its onboard USB to
UART bridge (commonly CH340 or CP2102) normally appears as `/dev/ttyUSB0`;
use `/dev/ttyUSB1` or another detected port if needed. The CMS2 sync client
defaults to `/dev/ttyUSB0` and 115200 baud. Set `VVS_SERIAL_PORT` to override
the path. The account running the client needs serial-device access, usually
by membership in `dialout`:

```sh
sudo usermod -aG dialout "$USER"
```

Log out and back in for the group change to apply.

## Build, upload, and monitor

From this directory:

```sh
pio run
pio run --target upload
pio device monitor
```

Use a 115200 baud monitor. The expected startup line is:

```text
[GW2] ready on 433.5 MHz
```

## USB serial protocol

Every command and response is one newline-terminated line:

- Host `C2:<payload>`: remove `C2:` and transmit `<payload>` over LoRa.
- Host `C2W:<payload>`: remove `C2W:`, transmit, then wait up to 2000 ms.
- Reply received over LoRa: send `C2:<reply>` to the host.
- No reply within the wait period: send `C2:TIMEOUT` to the host.
- Any LoRa payload received outside a wait: send `C2:<payload>` to the host.

The CMS2 Linux host should run
`~/vvs/lora/lora_sync_client.py` while the gateway is connected. See
`cms1-deploy-kit/README-CMS1.md` for deployment instructions.
