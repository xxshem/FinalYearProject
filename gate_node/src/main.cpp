/*
 * VVS Gate Node — ESP32-CAM + ESP-12E LoRa bridge
 *
 * Verification flow:
 *   1. Capture QR code
 *   2. Extract token
 *   3. Send over UART to the LoRa bridge
 *   4. Wait for reply
 *   5. Send display and feedback events to the ESP-12E
 */

#include <Arduino.h>
#include <Arduino_JSON.h>
#include "qr_decoder.h"
#include "lora_packet_limits.h"

// ── Configuration ─────────────────────────────────────
String GATE_ID = "GATE_A";        // runtime-changeable default
#define DEVICE_ID 0x01
uint32_t bootId = 0;

// UART2 link to the ESP-12E LoRa bridge (CH1, 433.1 MHz). Leave the microSD
// slot unused; this ESP32-CAM does not configure the radio.
#define BRIDGE_UART_RX  13
#define BRIDGE_UART_TX  14
#define BRIDGE_BAUD     115200
#define BRIDGE_TIMEOUT_MS 20000  // SF12 transmit airtime plus the radio reply timeout

HardwareSerial RadioBridge(2);
uint16_t seq = 0;

// ── Forward declarations ─────────────────────────────
bool sendVerifyRequest(const String& fullPayload, uint16_t requestSeq);
String waitForBridgeReply(unsigned long timeoutMs);
void sendStatus(const char* status, const String& detail = String());

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

// ─────────────────────────────────────────────────────

// ─────────────────────────────────────────────────────
void setup() {
    Serial.begin(115200);
    RadioBridge.begin(BRIDGE_BAUD, SERIAL_8N1, BRIDGE_UART_RX, BRIDGE_UART_TX);
    delay(500);
    bootId = esp_random();
    Serial.println("\n[GATE] booting");
    Serial.println("[BRIDGE] ESP-12E UART ready");

    sendStatus("INITIALIZING");

    // Camera
    if (!qr_camera_init()) {
        sendStatus("CAMERA_ERROR");
        Serial.println("[GATE] camera init failed");
        while (1) delay(1000);
    }

    sendStatus("CAMERA_READY");
    sendStatus("READY");
    Serial.println("[GATE] ready");
}

void loop() {

    String payload = "";

    if (Serial.available()) {
        String line = Serial.readStringUntil('\n');
        line.trim();

        // Runtime commands (SET_GATE=..., WHOAMI) — not tokens
        if (handleSerialCommand(line)) {
            return;
        }

        // Otherwise treat the line as a manual signed QR payload for bench testing
        payload = line;
    } else {
        payload = qr_scan_once();
    }

    if (payload.length() == 0) { delay(50); return; }

    

    // Payload format: vid|token|signature → extract token
    int p1 = payload.indexOf('|');
    int p2 = payload.indexOf('|', p1 + 1);
    String token = (p1 > 0 && p2 > p1) ? payload.substring(p1 + 1, p2) : payload;
    Serial.printf("[GATE] token: %s\n", token.c_str());
    sendStatus("CHECKING", token);

    uint16_t requestSeq = seq++;
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
    bool granted = (bool)obj["granted"];
    String reg   = (const char*)obj["reg"];
    Serial.printf("[GATE] reply: %s\n", reply.c_str());
    sendStatus(granted ? "VERIFIED" : "REJECTED", reg);
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
    JSONVar o;
    o["type"]    = "VERIFY_REQ";
    o["gate"]    = GATE_ID;
    o["boot"]    = bootId;
    o["payload"] = fullPayload;   // full vid|token|signature
    o["seq"]     = requestSeq;
    String body = JSON.stringify(o);

    if (!isLoRaPacketSizeValid(body.length())) {
        Serial.printf("[BRIDGE] request rejected locally: JSON body is %u bytes; LoRa limit is %u bytes\n",
                      body.length(), static_cast<unsigned int>(LORA_MAX_PACKET_BYTES));
        return false;
    }

    RadioBridge.print("REQ:");
    RadioBridge.println(body);
    Serial.printf("[BRIDGE] request sent (%u bytes)\n", body.length());
    return true;
}

String waitForBridgeReply(unsigned long timeoutMs) {
    unsigned long t0 = millis();
    String line;
    bool lineTooLong = false;
    while (millis() - t0 < timeoutMs) {
        while (RadioBridge.available()) {
            char c = (char)RadioBridge.read();
            if (c == '\r') continue;
            if (c == '\n') {
                if (!lineTooLong) {
                    line.trim();
                    if (line.startsWith("ERR:")) {
                        Serial.printf("[BRIDGE] %s\n", line.c_str());
                        return "";
                    }
                    if (line.startsWith("RSP:")) return line.substring(4);
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
