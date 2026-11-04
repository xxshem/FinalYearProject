/*
 * CMS2 LoRa Gateway — NodeMCU v3 USB serial <-> SX1278 on CH2.
 */

#include <Arduino.h>
#include <SPI.h>
#include <LoRa.h>

#define LORA_SS    5
#define LORA_RST   16
#define LORA_DIO0  4

// NodeMCU v3 hardware SPI pins.
#define LORA_SCK   14
#define LORA_MISO  12
#define LORA_MOSI  13

#define FREQ_CH2   433.5E6
#define REPLY_TIMEOUT_MS 2000

void waitForReply() {
    const unsigned long started = millis();
    while (millis() - started < REPLY_TIMEOUT_MS) {
        const int packetSize = LoRa.parsePacket();
        if (packetSize > 0) {
            Serial.print("C2:");
            while (LoRa.available()) {
                Serial.write(static_cast<uint8_t>(LoRa.read()));
            }
            Serial.println();
            return;
        }
        delay(1);
    }
    Serial.println("C2:TIMEOUT");
}

void setup() {
    Serial.begin(115200);
    delay(500);

    LoRa.setPins(LORA_SS, LORA_RST, LORA_DIO0);
    if (!LoRa.begin(FREQ_CH2)) {
        Serial.println("[GW2] init failed");
        while (1) delay(1000);
    }
    LoRa.setSpreadingFactor(12);
    LoRa.setSignalBandwidth(125E3);
    LoRa.setCodingRate4(5);
    LoRa.setTxPower(20);
    Serial.println("[GW2] ready on 433.5 MHz");
}

void loop() {
    if (Serial.available()) {
        String line = Serial.readStringUntil('\n');
        line.trim();

        const bool shouldWaitForReply = line.startsWith("C2W:");
        if (shouldWaitForReply) {
            line.remove(0, 4);
        } else if (line.startsWith("C2:")) {
            line.remove(0, 3);
        } else {
            line = "";
        }

        if (line.length() > 0) {
            LoRa.beginPacket();
            LoRa.print(line);
            if (LoRa.endPacket() != 1) {
                Serial.println("C2:TX_ERROR");
            } else if (shouldWaitForReply) {
                waitForReply();
            }
        }
    }

    int sz = LoRa.parsePacket();
    if (sz) {
        Serial.print("C2:");
        while (LoRa.available()) Serial.write(static_cast<uint8_t>(LoRa.read()));
        Serial.println();
    }
}