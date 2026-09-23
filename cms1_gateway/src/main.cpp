/*
 * CMS1 LoRa Gateway Modem — Time-Shared CH1 <-> CH2
 * ESP32 Dev Board + SX1278 (868 MHz)
 *
 * USB protocol:
 *   Host -> modem : "C1:<payload>\n"   (send on CH1, 868.1 MHz)
 *                   "C2:<payload>\n"   (send on CH2, 868.5 MHz)
 *   Modem -> host : "C1:<payload>\n"   (received on CH1)
 *                   "C2:<payload>\n"   (received on CH2)
 *
 * Hops between CH1 and CH2 every HOP_MS milliseconds.
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
#define HOP_MS     100

int      currentCh = 1;
uint32_t lastHop   = 0;

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
    Serial.println("[GW1] ready, hopping CH1<->CH2 @100ms");
}

void switchTo(int ch) {
    if (ch == currentCh) return;
    LoRa.idle();
    LoRa.setFrequency(ch == 1 ? FREQ_CH1 : FREQ_CH2);
    currentCh = ch;
}

void loop() {
    // Hop scheduler
    if (millis() - lastHop >= HOP_MS) {
        switchTo(currentCh == 1 ? 2 : 1);
        lastHop = millis();
    }

    // USB -> LoRa
    if (Serial.available()) {
        String line = Serial.readStringUntil('\n');
        line.trim();
        if (line.startsWith("C1:")) {
            switchTo(1);
            LoRa.beginPacket();
            LoRa.print(line.substring(3));
            LoRa.endPacket();
        } else if (line.startsWith("C2:")) {
            switchTo(2);
            LoRa.beginPacket();
            LoRa.print(line.substring(3));
            LoRa.endPacket();
        }
    }

    // LoRa -> USB
    int sz = LoRa.parsePacket();
    if (sz) {
        String out = (currentCh == 1) ? "C1:" : "C2:";
        while (LoRa.available()) out += (char)LoRa.read();
        Serial.println(out);
    }
}