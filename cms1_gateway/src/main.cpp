/*
 * CMS1 LoRa Gateway Modem — CH1 listener with explicit CH2 transmissions
 * ESP32 Dev Board + SX1278 (868 MHz)
 *
 * USB protocol:
 *   Host -> modem : "C1:<payload>\n"   (send on CH1, 868.1 MHz)
 *                   "C2:<payload>\n"   (send on CH2, 868.5 MHz)
 *   Modem -> host : "C1:<payload>\n"   (received on CH1)
 *                   "C2:<payload>\n"   (received on CH2)
 *
 */

#include <Arduino.h>
#include <SPI.h>
#include <LoRa.h>

// ── Pin map (ESP32 Dev Board) ────────────────────────
#define LORA_SS    5
#define LORA_RST   14
#define LORA_DIO0  2

#define FREQ_CH1   868.1E6
#define FREQ_CH2   868.5E6

int      currentCh = 1;

void applyChannel(int ch) {
    LoRa.idle();
    LoRa.setFrequency(ch == 1 ? FREQ_CH1 : FREQ_CH2);
    LoRa.setSpreadingFactor(ch == 1 ? 12 : 7);
    LoRa.setSignalBandwidth(125E3);
    LoRa.setCodingRate4(5);
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
    LoRa.setSpreadingFactor(12);
    LoRa.setSignalBandwidth(125E3);
    LoRa.setCodingRate4(5);
    LoRa.setTxPower(20);
    LoRa.setSpreadingFactor(12);
    LoRa.setSignalBandwidth(125E3);
    LoRa.setCodingRate4(5);
    Serial.println("[GW1] ready on CH1; CH2 sync uses SF7");
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

        if (line.length()) {
            LoRa.beginPacket();
            LoRa.print(line);
            LoRa.endPacket();
        }

        if (waitForReply) {
            uint32_t started = millis();
            bool received = false;
            while (millis() - started < 2500) {
                int packetSize = LoRa.parsePacket();
                if (packetSize) {
                    Serial.print("C2:");
                    while (LoRa.available()) Serial.print((char)LoRa.read());
                    Serial.println();
                    received = true;
                    break;
                }
                delay(10);
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