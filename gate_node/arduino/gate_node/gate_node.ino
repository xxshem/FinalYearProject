/*
 * VVS Gate Node — AI-Thinker ESP32-CAM + ESP-12E LoRa bridge
 *
 * Capture a QR code, send its signed payload to the ESP-12E over UART,
 * wait for the verification reply, and forward status events to the bridge.
 */

#include <Arduino.h>
#include <Arduino_JSON.h>
#include <ESP32QRCodeReader.h>

// ── Configuration ─────────────────────────────────────
String GATE_ID = "GATE_A";
constexpr size_t LORA_MAX_PACKET_BYTES = 255;

uint32_t bootId = 0;
uint16_t seq = 0;

// UART2 link to the ESP-12E LoRa bridge (CH1, 433.1 MHz). Do not use the
// microSD slot; GPIO 13 and 14 are shared with that interface.
constexpr int BRIDGE_UART_RX = 13;
constexpr int BRIDGE_UART_TX = 14;
constexpr uint32_t BRIDGE_BAUD = 115200;
constexpr unsigned long BRIDGE_TIMEOUT_MS = 20000;

HardwareSerial RadioBridge(2);
ESP32QRCodeReader qrReader(CAMERA_MODEL_AI_THINKER);

// ── Forward declarations ──────────────────────────────
bool sendVerifyRequest(const String& fullPayload, uint16_t requestSeq);
String waitForBridgeReply(unsigned long timeoutMs);
void sendStatus(const char* status, const String& detail = String());
bool handleSerialCommand(const String& line);
String qrScanOnce();

bool isLoRaPacketSizeValid(size_t byteLength) {
    return byteLength > 0 && byteLength <= LORA_MAX_PACKET_BYTES;
}

String cleanStatusDetail(const String& value) {
    String clean;
    clean.reserve(value.length());
    for (size_t i = 0; i < value.length(); ++i) {
        const char c = value[i];
        clean += (c == '\r' || c == '\n') ? ' ' : c;
    }
    return clean;
}

bool handleSerialCommand(const String& line) {
    if (line.startsWith("SET_GATE=")) {
        String newId = line.substring(9);
        newId.trim();
        if (newId.length() > 0) {
            GATE_ID = newId;
            Serial.printf("[GATE] GATE_ID now = %s\n", GATE_ID.c_str());
            sendStatus("GATE_ID_SET", GATE_ID);
            delay(1500);
            sendStatus("READY");
            return true;
        }
    }
    if (line == "WHOAMI") {
        Serial.printf("[GATE] current GATE_ID = %s\n", GATE_ID.c_str());
        sendStatus("GATE_ID", GATE_ID);
        delay(1500);
        sendStatus("READY");
        return true;
    }
    return false;
}

void setup() {
    Serial.begin(115200);
    RadioBridge.begin(BRIDGE_BAUD, SERIAL_8N1, BRIDGE_UART_RX, BRIDGE_UART_TX);
    delay(500);
    bootId = esp_random();
    Serial.println("\n[GATE] booting");
    Serial.println("[BRIDGE] ESP-12E UART ready");
    sendStatus("INITIALIZING");

    if (qrReader.setup() != SETUP_OK) {
        sendStatus("CAMERA_ERROR");
        Serial.println("[GATE] camera initialization failed (check camera and PSRAM)");
        while (true) {
            delay(1000);
        }
    }
    qrReader.beginOnCore(1);
    Serial.println("[QR] camera ready (ESP32QRCodeReader)");

    sendStatus("CAMERA_READY");
    sendStatus("READY");
    Serial.println("[GATE] ready");
}

void loop() {
    String payload;

    if (Serial.available()) {
        String line = Serial.readStringUntil('\n');
        line.trim();

        // Runtime commands (SET_GATE=..., WHOAMI) — not tokens.
        if (handleSerialCommand(line)) {
            return;
        }

        // Otherwise treat the line as a signed payload for bench testing.
        payload = line;
    } else {
        payload = qrScanOnce();
    }

    if (payload.length() == 0) {
        delay(50);
        return;
    }

    // Payload format: vid|token|signature → extract token.
    const int p1 = payload.indexOf('|');
    const int p2 = payload.indexOf('|', p1 + 1);
    const String token = (p1 > 0 && p2 > p1)
        ? payload.substring(p1 + 1, p2)
        : payload;
    Serial.printf("[GATE] token: %s\n", token.c_str());
    sendStatus("CHECKING", token);

    const uint16_t requestSeq = seq++;
    String reply;
    for (uint8_t attempt = 0; attempt < 3 && reply.length() == 0; ++attempt) {
        if (!sendVerifyRequest(payload, requestSeq)) {
            sendStatus("REQUEST_TOO_LARGE");
            delay(1500);
            sendStatus("READY");
            return;
        }
        reply = waitForBridgeReply(BRIDGE_TIMEOUT_MS);
    }
    if (reply.length() == 0) {
        Serial.println("[GATE] timeout");
        sendStatus("REJECTED", "Timeout");
        return;
    }

    JSONVar obj = JSON.parse(reply);
    const bool granted = (bool)obj["granted"];
    const String reg = (const char*)obj["reg"];
    Serial.printf("[GATE] reply: %s\n", reply.c_str());
    sendStatus(granted ? "VERIFIED" : "REJECTED", reg);
}

String qrScanOnce() {
    QRCodeData qrCodeData;
    if (qrReader.receiveQrCode(&qrCodeData, 100) && qrCodeData.valid) {
        String payload = String((const char*)qrCodeData.payload);
        Serial.printf("[QR] decoded: %s\n", payload.c_str());
        return payload;
    }
    return "";
}

void sendStatus(const char* status, const String& detail) {
    RadioBridge.print("STATUS:");
    RadioBridge.print(status);
    if (detail.length() > 0) {
        RadioBridge.print(':');
        RadioBridge.print(cleanStatusDetail(detail));
    }
    RadioBridge.println();
}

bool sendVerifyRequest(const String& fullPayload, uint16_t requestSeq) {
    JSONVar request;
    request["type"] = "VERIFY_REQ";
    request["gate"] = GATE_ID;
    request["boot"] = bootId;
    request["payload"] = fullPayload;
    request["seq"] = requestSeq;
    const String body = JSON.stringify(request);

    if (!isLoRaPacketSizeValid(body.length())) {
        Serial.printf(
            "[BRIDGE] request rejected locally: JSON body is %u bytes; "
            "LoRa limit is %u bytes\n",
            (unsigned int)body.length(),
            (unsigned int)LORA_MAX_PACKET_BYTES
        );
        return false;
    }

    RadioBridge.print("REQ:");
    RadioBridge.println(body);
    Serial.printf("[BRIDGE] request sent (%u bytes)\n", (unsigned int)body.length());
    return true;
}

String waitForBridgeReply(unsigned long timeoutMs) {
    const unsigned long startTime = millis();
    String line;
    bool lineTooLong = false;

    while (millis() - startTime < timeoutMs) {
        while (RadioBridge.available()) {
            const char c = (char)RadioBridge.read();
            if (c == '\r') {
                continue;
            }
            if (c == '\n') {
                if (!lineTooLong) {
                    line.trim();
                    if (line.startsWith("ERR:")) {
                        Serial.printf("[BRIDGE] %s\n", line.c_str());
                        return "";
                    }
                    if (line.startsWith("RSP:")) {
                        return line.substring(4);
                    }
                }
                line = "";
                lineTooLong = false;
            } else if (!lineTooLong && line.length() < 512) {
                line += c;
            } else {
                lineTooLong = true;
            }
        }
        delay(1);
    }
    return "";
}
