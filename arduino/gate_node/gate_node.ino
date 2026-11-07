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

// UART0 carries the ESP-12E protocol and is also used for firmware upload.
constexpr int BRIDGE_UART_RX = 3;
constexpr int BRIDGE_UART_TX = 1;
constexpr uint32_t BRIDGE_BAUD = 9600;
constexpr unsigned long BRIDGE_TIMEOUT_MS = 20000;
constexpr uint8_t MAX_BRIDGE_ATTEMPTS = 3;

HardwareSerial& RadioBridge = Serial;
ESP32QRCodeReader qrReader(CAMERA_MODEL_AI_THINKER);

enum class BridgeReplyStatus {
    RESPONSE,
    ERROR,
    TIMEOUT
};

struct BridgeReply {
    BridgeReplyStatus status;
    String body;
};

// ── Forward declarations ──────────────────────────────
bool sendVerifyRequest(const String& fullPayload, uint16_t requestSeq);
BridgeReply waitForBridgeReply(unsigned long timeoutMs, uint32_t requestBoot,
                               uint16_t requestSeq);
void sendStatus(const char* status, const String& detail = String());
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

void setup() {
    RadioBridge.begin(BRIDGE_BAUD, SERIAL_8N1, BRIDGE_UART_RX, BRIDGE_UART_TX);
    delay(500);
    bootId = esp_random();
    sendStatus("INITIALIZING");

    qrReader.setDebug(false);
    const QRCodeReaderSetupErr cameraSetup = qrReader.setup();
    if (cameraSetup != SETUP_OK) {
        sendStatus("CAMERA_ERROR");
        while (true) {
            delay(1000);
        }
    }
    qrReader.beginOnCore(1);
    sendStatus("CAMERA_READY");
    sendStatus("READY");
}

void loop() {
    const String payload = qrScanOnce();

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

    const bool granted = (bool)obj["granted"];
    const String reg = (const char*)obj["reg"];
    sendStatus(granted ? "VERIFIED" : "REJECTED", reg);
}

String qrScanOnce() {
    QRCodeData qrCodeData;
    if (qrReader.receiveQrCode(&qrCodeData, 100) && qrCodeData.valid) {
        String payload = String((const char*)qrCodeData.payload);
        return payload;
    }
    return "";
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
    JSONVar request;
    request["type"] = "VERIFY_REQ";
    request["gate"] = GATE_ID;
    request["boot"] = bootId;
    request["payload"] = fullPayload;
    request["seq"] = requestSeq;
    const String body = JSON.stringify(request);

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
