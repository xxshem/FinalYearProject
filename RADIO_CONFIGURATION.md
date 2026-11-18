# VVS Radio Configuration Baseline

This document records the authoritative **source configuration** for the three
SX1278 endpoints. It is derived from the PlatformIO sources and their
byte-identical Arduino sketch mirrors. It does not prove which firmware is
currently flashed or measure the physical radios.

## Shared modulation profile

| Parameter | Source setting |
|---|---|
| Spreading factor | SF7 |
| Bandwidth | 125 kHz |
| Coding rate | 4/5 |
| Preamble | 8 symbols |
| Sync word | `0x12` |
| Header | Explicit |
| CRC | Enabled |
| IQ | Normal (inversion disabled) |
| Transmit power | 2 dBm |
| SPI clock | 4 MHz |

Both ends of each channel use this profile. The bridge and CMS1 share CH1;
CMS1 and CMS2 share CH2. CMS1 uses one radio and changes frequency between
the channels; it cannot receive on both at once.

## Per-role frequency

| Radio role | Active source | Channel | Frequency |
|---|---|---|---:|
| ESP8266 gate bridge | `esp8266_lora_bridge/src/main.cpp` | CH1 | 433.1 MHz |
| CMS1 ESP32 gateway | `cms1_gateway/src/main.cpp` | CH1 | 433.1 MHz |
| CMS1 ESP32 gateway | `cms1_gateway/src/main.cpp` | CH2 | 433.5 MHz |
| CMS2 ESP8266 gateway | `cms2_gateway/src/main.cpp` | CH2 | 433.5 MHz |

The corresponding `arduino/` sketches mirror the PlatformIO source. The gate
ESP32-CAM itself does not configure a radio; the separate ESP8266 bridge owns
the SX1278 on CH1.

## Airtime estimate

CMS1's publisher uses the same SF7, 125 kHz, coding-rate 4/5, 8-symbol
preamble, explicit-header, CRC-enabled profile in
`cms1-deploy-kit/vvs/lora/lora_sync_server.py`. At this profile, the current
SX127x estimator returns approximately 0.174336 seconds for a 100-byte RF
payload and 0.399616 seconds for a 255-byte payload. These are formula-based
estimates; they exclude serial scheduling, radio startup, processing, and
hardware timing variation. They are not measurements of deployed airtime.

## Physical configuration limitation

The repository cannot establish which image is actually flashed onto a
microcontroller, verify module crystal/frequency accuracy, antenna behavior,
RF output power, or the legality of these settings at the installation site.
Record those separately during controlled hardware commissioning.

## Radio diagnostics

The bridge maintains CH1 counters; CMS1 maintains separate CH1 and CH2
counters; CMS2 maintains CH2 counters. Counters start at zero on firmware boot
and saturate at `UINT32_MAX` rather than wrapping. The `saturated` field in a
stats response becomes true if an increment is attempted after a counter has
reached that limit. `tx_failures` counts unsuccessful transmit attempts;
`tx_timeouts` is the subset that exceeded the TX deadline. `rx_timeouts` counts
finite receive windows that expired; continuously listening channels have no
receive timeout to count. CRC errors are read from the SX127x IRQ flags before
the LoRa library clears them.

On Linux, send `SIGUSR1` to the process owning the gateway serial port:

```sh
sudo systemctl kill --signal=USR1 vvs-listener
sudo journalctl -u vvs-listener -n 50 --no-pager
sudo systemctl kill --signal=USR1 vvs-sync-client@pi
sudo journalctl -u vvs-sync-client@pi -n 50 --no-pager
```

CMS1 and CMS2 defer the serial query until their host-side synchronization
transfer is idle. The returned `STATS:` JSON line is parsed as diagnostics and
does not enter the LoRa protocol or reset counters. Gate-bridge counters are
maintained but cannot currently be retrieved while the gate is operating; an
operator trigger has not been approved, so no additional gate UART traffic or
UI behavior is introduced.
