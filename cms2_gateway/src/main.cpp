/*
 * CMS2 LoRa Gateway Modem — Locked to CH2 (868.5 MHz)
 * Plain passthrough: USB <-> LoRa.
 */

#include <Arduino.h>
#include <SPI.h>
#include <LoRa.h>

#define LORA_SS    5
#define LORA_RST   14
#define LORA_DIO0  2
#define FREQ_CH2   868.5E6

void setup() {
    Serial.begin(115200);
    delay(500);

    LoRa.setPins(LORA_SS, LORA_RST, LORA_DIO0);
    if (!LoRa.begin(FREQ_CH2)) {
        Serial.println("[GW2] init failed");
        while (1) delay(1000);
    }
    LoRa.setSpreadingFactor(7);
    LoRa.setSignalBandwidth(125E3);
    LoRa.setCodingRate4(5);
    Serial.println("[GW2] ready on 868.5 MHz");
}

void loop() {
    if (Serial.available()) {
        String line = Serial.readStringUntil('\n');
        line.trim();
        if (line.length()) {
            LoRa.beginPacket();
            LoRa.print(line);
            LoRa.endPacket();
        }
    }

    int sz = LoRa.parsePacket();
    if (sz) {
        while (LoRa.available()) Serial.print((char)LoRa.read());
        Serial.println();
    }
}