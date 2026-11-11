/*
 * CMS1 LoRa Gateway Modem — CH1 listener with explicit CH2 transmissions
 * ESP32 Dev Board + SX1278 (433 MHz)
 *
 * USB protocol:
 *   Host -> modem : "C1:<payload>\n"   (send on CH1, 433.1 MHz)
 *                   "C2:<payload>\n"   (send on CH2, 433.5 MHz)
 *   Modem -> host : "C1:<payload>\n"   (received on CH1)
 *                   "C2:<payload>\n"   (received on CH2)
 *
 */

#include <Arduino.h>
#include <ctype.h>
#include <SPI.h>
#include <LoRa.h>
#include "sync_ack_parser.h"

// ── Pin map (ESP32 Dev Board) ────────────────────────
#define LORA_SS    5
#define LORA_RST   14
#define LORA_DIO0  2

#define FREQ_CH1   433.1E6
#define FREQ_CH2   433.5E6
#define SYNC_ACK_TIMEOUT_MS 15000

int      currentCh = 1;

bool jsonStringField(const String& json, const char* key, String& value) {
    const String field = "\"" + String(key) + "\"";
    int position = json.indexOf(field);
    if (position < 0) return false;
    position = json.indexOf(':', position + field.length());
    if (position < 0) return false;

    ++position;
    while (position < json.length() &&
           isspace(static_cast<unsigned char>(json[position]))) {
        ++position;
    }
    if (position >= json.length() || json[position] != '"') return false;

    const int start = ++position;
    while (position < json.length()) {
        if (json[position] == '\\') return false;
        if (json[position] == '"') {
            value = json.substring(start, position);
            return true;
        }
        ++position;
    }
    return false;
}

void reportSyncTxError(const String& transferId) {
    Serial.print("C2:{\"type\":\"SYNC_TX_ERROR\",\"id\":\"");
    Serial.print(transferId);
    Serial.println("\"}");
}

void applyChannel(int ch) {
    LoRa.idle();
    LoRa.setFrequency(ch == 1 ? FREQ_CH1 : FREQ_CH2);
    LoRa.setSpreadingFactor(7);
    LoRa.setSignalBandwidth(125E3);
    LoRa.setCodingRate4(5);
    LoRa.setPreambleLength(8);
    LoRa.setSyncWord(0x12);
    LoRa.enableCrc();
    LoRa.disableInvertIQ();
    LoRa.setTxPower(2);
    currentCh = ch;
}

// ─────────────────────────────────────────────────────
void setup() {
    Serial.begin(115200);
    delay(500);

    LoRa.setPins(LORA_SS, LORA_RST, LORA_DIO0);
    if (!LoRa.begin(FREQ_CH1)) {
        Serial.println("[GW1] LoRa init failed");
        while (1) delay(1000);
    }
    LoRa.setSpreadingFactor(7);
    LoRa.setSignalBandwidth(125E3);
    LoRa.setCodingRate4(5);
    LoRa.setTxPower(2);
    LoRa.setPreambleLength(8);
    LoRa.setSyncWord(0x12);
    LoRa.enableCrc();
    LoRa.disableInvertIQ();
    Serial.println("[GW1] ready on CH1 433.1 MHz; CH2 433.5 MHz, SF7");
}

void switchTo(int ch) {
    if (ch == currentCh) return;
    applyChannel(ch);
}

void loop() {
    // USB -> LoRa
    if (Serial.available()) {
        String line = Serial.readStringUntil('\n');
        line.trim();
        bool waitForReply = line.startsWith("C2W:");
        if (waitForReply) {
            switchTo(2);
            line = line.substring(4);
        } else if (line.startsWith("C1:")) {
            switchTo(1);
            line = line.substring(3);
        } else if (line.startsWith("C2:")) {
            switchTo(2);
            line = line.substring(3);
        } else {
            line = "";
        }

        String expectedTransferId;
        if (waitForReply) {
            String messageType;
            if (!jsonStringField(line, "type", messageType)
                || messageType != "SYNC_END"
                || !jsonStringField(line, "id", expectedTransferId)
                || expectedTransferId.length() == 0) {
                Serial.println("[GW1] invalid C2W sync frame");
                switchTo(1);
                return;
            }
        }

        if (line.length()) {
            const bool isCh2Frame = currentCh == 2;
            String frameTransferId;
            jsonStringField(line, "id", frameTransferId);
            if (LoRa.beginPacket() != 1) {
                if (isCh2Frame) reportSyncTxError(frameTransferId);
                switchTo(1);
                return;
            }
            LoRa.print(line);
            if (LoRa.endPacket() != 1) {
                if (isCh2Frame) reportSyncTxError(frameTransferId);
                switchTo(1);
                return;
            }
        }

        if (waitForReply) {
            uint32_t started = millis();
            bool received = false;
            while (millis() - started < SYNC_ACK_TIMEOUT_MS) {
                int packetSize = LoRa.parsePacket();
                if (packetSize) {
                    String response;
                    while (LoRa.available()) response += (char)LoRa.read();
                    SyncAckResult ackResult = parseSyncAck(
                        response.c_str(), response.length(),
                        expectedTransferId.c_str());
                    if (ackResult == SyncAckResult::Positive
                        || ackResult == SyncAckResult::Negative) {
                        Serial.print("C2:");
                        Serial.println(response);
                        received = true;
                        break;
                    }
                }
                delay(1);
            }
            if (!received) {
                Serial.println("C2:{\"type\":\"SYNC_TIMEOUT\"}");
            }
            switchTo(1);
        } else if (currentCh == 2) {
            switchTo(1);
        }
    }

    // LoRa -> USB
    int sz = LoRa.parsePacket();
    if (sz) {
        String out = (currentCh == 1) ? "C1:" : "C2:";
        if (currentCh == 1) {
            out += String(LoRa.packetRssi()) + "," +
                   String(LoRa.packetSnr(), 1) + ":";
        }
        while (LoRa.available()) out += (char)LoRa.read();
        Serial.println(out);
    }
}