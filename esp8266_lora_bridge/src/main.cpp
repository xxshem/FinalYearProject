#include <Arduino.h>
#include <SPI.h>
#include <LoRa.h>

#define LORA_NSS       5
#define LORA_RESET     16
#define LORA_DIO0      4
#define RADIO_FREQ     433.1E6
#define RADIO_TIMEOUT_MS 6000
#define MAX_UART_LINE  512
#define MAX_LORA_PACKET 255

String uartLine;
bool uartLineTooLong = false;
bool radioReady = false;

void sendBridgeError(const char* error) {
    Serial.print("ERR:");
    Serial.println(error);
}

void handleRequest(const String& line) {
    if (!line.startsWith("REQ:")) return;

    if (!radioReady) {
        Serial1.println("[BRIDGE] cannot transmit: radio is not initialized");
        sendBridgeError("RADIO_INIT_FAILED");
        return;
    }

    String payload = line.substring(4);
    if (payload.length() == 0 || payload.length() > MAX_LORA_PACKET) {
        Serial1.println("[BRIDGE] rejected request with invalid LoRa packet length");
        sendBridgeError("BAD_REQUEST");
        return;
    }

    Serial1.printf("[BRIDGE] transmitting %u bytes\n", payload.length());
    LoRa.idle();
    LoRa.beginPacket();
    LoRa.print(payload);
    if (!LoRa.endPacket()) {
        Serial1.println("[BRIDGE] LoRa transmit failed");
        sendBridgeError("TX_FAILED");
        return;
    }

    unsigned long started = millis();
    while (millis() - started < RADIO_TIMEOUT_MS) {
        int packetSize = LoRa.parsePacket();
        if (packetSize > 0) {
            String response;
            while (LoRa.available()) response += (char)LoRa.read();
            Serial.print("RSP:");
            Serial.println(response);
            Serial1.printf("[BRIDGE] received %u-byte reply; RSSI=%d SNR=%.1f\n",
                           response.length(), LoRa.packetRssi(), LoRa.packetSnr());
            return;
        }
        delay(1);
    }

    Serial1.println("[BRIDGE] LoRa reply timeout");
    sendBridgeError("TIMEOUT");
}

void setup() {
    Serial.begin(115200);
    Serial1.begin(115200);

    LoRa.setPins(LORA_NSS, LORA_RESET, LORA_DIO0);
    if (!LoRa.begin(RADIO_FREQ)) {
        Serial1.println("[BRIDGE] LoRa initialization failed");
        return;
    }
    radioReady = true;
    LoRa.setSpreadingFactor(12);
    LoRa.setSignalBandwidth(125E3);
    LoRa.setCodingRate4(5);
    LoRa.setTxPower(20);
    Serial1.println("[BRIDGE] ready on 433.1 MHz; SF12, BW125, CR4/5");
}

void loop() {
    while (Serial.available()) {
        char c = (char)Serial.read();
        if (c == '\r') continue;

        if (c == '\n') {
            if (uartLineTooLong) {
                Serial1.println("[BRIDGE] rejected oversized UART frame");
                sendBridgeError("FRAME_TOO_LONG");
            } else if (uartLine.length()) {
                handleRequest(uartLine);
            }
            uartLine = "";
            uartLineTooLong = false;
        } else if (uartLine.length() < MAX_UART_LINE) {
            uartLine += c;
        } else {
            uartLineTooLong = true;
        }
    }
}
