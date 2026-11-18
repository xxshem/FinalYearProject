# Reproducible Build Environment

## Tool versions

Install PlatformIO Core 6.2.0 and use the exact platform, framework, compiler,
SCons, and library packages pinned in each project's `platformio.ini`.

```text
python -m pip install platformio==6.2.0
```

| Target | Platform | Arduino framework | Compiler/toolchain |
|---|---|---|---|
| ESP32-CAM gate | espressif32 7.1.3 | 4.20017.260907+sha.dcc1105b | xtensa-esp32 8.4.0+2021r2-patch5 |
| CMS1 ESP32 gateway | espressif32 7.1.3 | 4.20017.260907+sha.dcc1105b | xtensa-esp32 8.4.0+2021r2-patch5 |
| ESP8266 gate bridge | espressif8266 4.2.1 | 3.30102.0 | xtensa 2.100300.220621 |
| CMS2 ESP8266 gateway | espressif8266 4.2.1 | 3.30102.0 | xtensa 2.100300.220621 |

The project pins the remaining platform build tools in `platform_packages`.
SCons is pinned to 4.41101.0 in all four firmware projects.
Firmware libraries are pinned to LoRa 0.8.0, LiquidCrystal_I2C 1.1.4,
Arduino_JSON 0.2.2, and ESP32QRCodeReader 1.1.0 where used. Do not remove the
exact versions or replace them with floating `^` or `~` constraints.

The pinned PlatformIO esptool package uses Python modules listed in
`platformio-build-requirements.txt`. After PlatformIO has resolved the pinned
packages, install these into the esptool package's `_contrib` directory:

```powershell
python -m pip install --no-compile --target "$env:PLATFORMIO_CORE_DIR\packages\tool-esptoolpy\_contrib" -r platformio-build-requirements.txt
```

Set `PLATFORMIO_CORE_DIR` to the PlatformIO Core directory in use. This
explicit step is necessary because a fresh package cache does not include
esptool's Python dependencies, and the otherwise successful ESP32 build
fails while generating the bootloader image.

The Python deployment dependencies, including the resolved transitive
dependencies, are pinned in `cms1-deploy-kit/requirements.txt`. The versions
were resolved using Python 3.14.7. The CMS1 target is Raspberry Pi OS
Bookworm and CMS2 is Ubuntu; deployments should use CPython 3.11 or newer.
The deployment hosts have not been accessed for this validation.

## Supported firmware environments

Run these commands from the repository root with PlatformIO Core 6.2.0:

```text
pio run -d esp8266_lora_bridge -e nodemcuv2
pio run -d cms1_gateway -e esp32dev
pio run -d cms2_gateway -e nodemcuv2
pio run -d gate_node -e esp32cam
pio run -d gate_node -e gate_b
pio run -d gate_node -e gate_c
```

For a clean dependency/build cache, set `PLATFORMIO_CORE_DIR` to a new empty
directory before running the commands. PlatformIO then resolves only the
versions pinned in the project configuration. Do not flash hardware as part
of a reproducibility build.

Install Python dependencies in a virtual environment:

```sh
python3 -m venv ~/vvs-venv
~/vvs-venv/bin/python -m pip install -r cms1-deploy-kit/requirements.txt
```

## Release manifest

`release-manifest.json` records the dependency set and the source revision
associated with a release build. It currently identifies the base commit and
marks the working tree as a candidate because this repository contains
uncommitted changes. Do not treat this candidate manifest as a released
artifact. After an approved commit, set `source_commit` to that commit, verify
the worktree is clean, and rerun the documented clean builds before publishing
the manifest with the release.

Radio source settings and the distinction between source and physical-device
configuration are documented in [RADIO_CONFIGURATION.md](RADIO_CONFIGURATION.md).
