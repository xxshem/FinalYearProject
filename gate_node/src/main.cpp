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
String GATE_ID = "GATE_A";
#define DEVICE_ID 0x01
uint32_t bootId = 0;

// UART0 carries the ESP-12E protocol and is also used for firmware upload.
#define BRIDGE_UART_RX  3
#define BRIDGE_UART_TX  1
#define BRIDGE_BAUD     9600
#define BRIDGE_TIMEOUT_MS 20000
#define MAX_BRIDGE_ATTEMPTS 3

HardwareSerial& RadioBridge = Serial;
uint16_t seq = 0;

enum class BridgeReplyStatus {
    RESPONSE,
    ERROR,
    TIMEOUT
};

struct BridgeReply {
    BridgeReplyStatus status;
    String body;
};

// ── Forward declarations ─────────────────────────────
bool sendVerifyRequest(const String& fullPayload, uint16_t requestSeq);
BridgeReply waitForBridgeReply(unsigned long timeoutMs, uint32_t requestBoot,
                               uint16_t requestSeq);
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

// ─────────────────────────────────────────────────────

// ─────────────────────────────────────────────────────
void setup() {
    RadioBridge.begin(BRIDGE_BAUD, SERIAL_8N1, BRIDGE_UART_RX, BRIDGE_UART_TX);
    delay(500);
    bootId = esp_random();

    sendStatus("INITIALIZING");

    // Camera
    if (!qr_camera_init()) {
        sendStatus("CAMERA_ERROR");
        while (1) delay(1000);
    }

    sendStatus("CAMERA_READY");
    sendStatus("READY");
}

void loop() {
    String payload = qr_scan_once();

    if (payload.length() == 0) { delay(50); return; }

    // Payload format: vid|token|signature → extract token
    int p1 = payload.indexOf('|');
    int p2 = payload.indexOf('|', p1 + 1);
    String token = (p1 > 0 && p2 > p1) ? payload.substring(p1 + 1, p2) : payload;
    sendStatus("CHECKING", token);

    const uint16_t requestSeq = seq++;
    while (RadioBridge.available()) RadioBridge.read();

    String reply;
    String bridgeError;
    for (uint8_t attempt = 0; attempt < MAX_BRIDGE_ATTEMPTS; ++attempt) {
        if (!sendVerifyRequest(payload, requestSeq)) {
            sendStatus("REQUEST_TOO_LARGE");
            delay(1500);
            sendStatus("READY");
            return;
        }
        const BridgeReply bridgeReply = waitForBridgeReply(
            BRIDGE_TIMEOUT_MS, bootId, requestSeq);
        if (bridgeReply.status == BridgeReplyStatus::RESPONSE) {
            reply = bridgeReply.body;
            break;
        }
        if (bridgeReply.status == BridgeReplyStatus::ERROR) {
            bridgeError = bridgeReply.body;
            if (bridgeError != "TIMEOUT") break;
        } else {
            bridgeError = "UART_TIMEOUT";
        }
    }
    if (reply.length() == 0) {
        sendStatus("BRIDGE_ERROR", bridgeError.length() > 0 ? bridgeError : "UNKNOWN");
        return;
    }

    JSONVar obj = JSON.parse(reply);
    if (JSON.typeof(obj) != "object" || !obj.hasOwnProperty("granted") ||
        !obj.hasOwnProperty("reg") ||
        JSON.typeof(obj["granted"]) != "boolean" ||
        JSON.typeof(obj["reg"]) != "string") {
        sendStatus("BRIDGE_ERROR", "BAD_RESPONSE");
        return;
    }

    bool granted = (bool)obj["granted"];
    String reg   = (const char*)obj["reg"];
    sendStatus(granted ? "VERIFIED" : "REJECTED", reg);
}

void sendStatus(const char* status, const String& detail) {
    String frame = "STATUS:";
    frame += status;
    if (detail.length() > 0) {
        frame += ':';
        frame += cleanStatusDetail(detail);
    }
    frame += '\n';
    RadioBridge.print(frame);
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
        return false;
    }

    String frame = "REQ:";
    frame += bootId;
    frame += ':';
    frame += requestSeq;
    frame += ':';
    frame += body;
    frame += '\n';
    RadioBridge.print(frame);
    return true;
}

bool parseUnsignedDecimal(const String& value, uint32_t maximum, uint32_t& result) {
    if (value.length() == 0) return false;

    uint32_t parsed = 0;
    for (size_t i = 0; i < value.length(); ++i) {
        const char c = value[i];
        if (c < '0' || c > '9') return false;
        const uint8_t digit = static_cast<uint8_t>(c - '0');
        if (parsed > (maximum - digit) / 10) return false;
        parsed = parsed * 10 + digit;
    }
    result = parsed;
    return true;
}

bool matchesRequest(const JSONVar& response, uint32_t requestBoot,
                    uint16_t requestSeq) {
    if (JSON.typeof(response) != "object" ||
        !response.hasOwnProperty("type") ||
        !response.hasOwnProperty("boot") ||
        !response.hasOwnProperty("seq") ||
        JSON.typeof(response["type"]) != "string" ||
        JSON.typeof(response["boot"]) != "number" ||
        JSON.typeof(response["seq"]) != "number" ||
        !response.hasPropertyEqual("type", "VERIFY_RESP")) {
        return false;
    }

    const double responseBoot = (double)response["boot"];
    const double responseSeq = (double)response["seq"];
    return responseBoot == static_cast<double>(requestBoot) &&
        responseSeq == static_cast<double>(requestSeq);
}

BridgeReply waitForBridgeReply(unsigned long timeoutMs, uint32_t requestBoot,
                               uint16_t requestSeq) {
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
                        const int bootSeparator = line.indexOf(':', 4);
                        const int seqSeparator = bootSeparator < 0
                            ? -1 : line.indexOf(':', bootSeparator + 1);
                        uint32_t responseBoot = 0;
                        uint32_t responseSeq = 0;
                        if (seqSeparator > bootSeparator &&
                            parseUnsignedDecimal(
                                line.substring(4, bootSeparator), UINT32_MAX,
                                responseBoot) &&
                            parseUnsignedDecimal(
                                line.substring(bootSeparator + 1, seqSeparator),
                                UINT16_MAX, responseSeq) &&
                            responseBoot == requestBoot &&
                            responseSeq == requestSeq &&
                            line.length() > static_cast<unsigned int>(seqSeparator + 1)) {
                            return {BridgeReplyStatus::ERROR,
                                    line.substring(seqSeparator + 1)};
                        }
                    }
                    if (line.startsWith("RSP:")) {
                        const String responseBody = line.substring(4);
                        const JSONVar response = JSON.parse(responseBody);
                        if (matchesRequest(response, requestBoot, requestSeq)) {
                            return {BridgeReplyStatus::RESPONSE, responseBody};
                        }
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
    return {BridgeReplyStatus::TIMEOUT, ""};
}
