/*
 * VVS Gate Node — ESP32-CAM + ESP-12E LoRa bridge + I2C LCD + PCF8574
 *
 * Verification flow:
 *   1. Capture QR code
 *   2. Extract token
 *   3. Send over UART to the LoRa bridge
 *   4. Wait for reply
 *   5. Feedback: LCD text + green/red LED + buzzer
 *
 * PCF8574 output mapping:
 *   P0 = Green LED  (LOW = on)
 *   P1 = Red LED    (LOW = on)
 *   P2 = Buzzer     (LOW = on)
 */

#include <Arduino.h>
#include <Arduino_JSON.h>
#include <Wire.h>
#include <LiquidCrystal_I2C.h>
#include "qr_decoder.h"
#include "lora_packet_limits.h"

// ── Configuration ─────────────────────────────────────
String GATE_ID = "GATE_A";        // runtime-changeable default
#define DEVICE_ID 0x01
uint32_t bootId = 0;

// UART2 link to the ESP-12E LoRa bridge. Leave the microSD slot unused.
#define BRIDGE_UART_RX  13
#define BRIDGE_UART_TX  14
#define BRIDGE_BAUD     115200
#define BRIDGE_TIMEOUT_MS 20000  // SF12 transmit airtime plus the radio reply timeout

// I2C bus (shared by LCD and PCF8574); see README boot/flash LED precautions.
#define I2C_SDA    2
#define I2C_SCL    4

// I2C device addresses — update from i2c_scan results
#define LCD_ADDR   0x27
#define PCF_ADDR   0x20

// PCF8574 bit positions
#define PCF_LED_GREEN  0
#define PCF_LED_RED    1
#define PCF_BUZZER     2

LiquidCrystal_I2C lcd(LCD_ADDR, 16, 2);
HardwareSerial RadioBridge(2);
uint16_t seq = 0;

// ── Forward declarations ─────────────────────────────
bool sendVerifyRequest(const String& fullPayload, uint16_t requestSeq);
String waitForBridgeReply(unsigned long timeoutMs);
void indicate(bool granted, const String& reg);
void pcf_write(uint8_t v);
void pcf_all_off();
void lcd_show(const char* line1, const char* line2);
void beep(uint16_t duration_ms);
bool handleSerialCommand(const String& line) {
    if (line.startsWith("SET_GATE=")) {
        String newId = line.substring(9);
        newId.trim();
        if (newId.length() > 0) {
            GATE_ID = newId;
            Serial.printf("[GATE] GATE_ID now = %s\n", GATE_ID.c_str());
            lcd_show("Gate ID set", GATE_ID.c_str());
            delay(1500);
            lcd_show("Ready", "Scan QR code");
            return true;
        }
    }
    if (line == "WHOAMI") {
        Serial.printf("[GATE] current GATE_ID = %s\n", GATE_ID.c_str());
        lcd_show("Gate ID", GATE_ID.c_str());
        delay(1500);
        lcd_show("Ready", "Scan QR code");
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

    // I2C bus init (shared by LCD + PCF8574)
    Wire.begin(I2C_SDA, I2C_SCL);
    Wire.setClock(100000);

    // LCD
    lcd.init();
    lcd.backlight();
    lcd_show("VVS GATE NODE", "Initializing...");

    // PCF8574: all HIGH = LEDs off, buzzer off
    pcf_all_off();
    delay(10);

    // Camera
    if (!qr_camera_init()) {
        lcd_show("CAMERA FAIL", "Check ribbon");
        Serial.println("[GATE] camera init failed");
        while (1) delay(1000);
    }

    lcd_show("Ready", "Scan QR code");
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
    lcd_show("Checking...", token.c_str());

    uint16_t requestSeq = seq++;
    String reply;
    for (uint8_t attempt = 0; attempt < 3 && reply.length() == 0; ++attempt) {
        if (!sendVerifyRequest(payload, requestSeq)) {
            lcd_show("REQ TOO LARGE", "LoRa limit 255 B");
            delay(1500);
            lcd_show("Ready", "Scan QR code");
            return;
        }
        reply = waitForBridgeReply(BRIDGE_TIMEOUT_MS);
    }
    if (reply.length() == 0) {
        Serial.println("[GATE] timeout");
        indicate(false, "Timeout");
        return;
    }

    JSONVar obj = JSON.parse(reply);
    bool granted = (bool)obj["granted"];
    String reg   = (const char*)obj["reg"];
    Serial.printf("[GATE] reply: %s\n", reply.c_str());
    indicate(granted, reg);
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
    while (millis() - t0 < timeoutMs) {
        while (RadioBridge.available()) {
            char c = (char)RadioBridge.read();
            if (c == '\r') continue;
            if (c == '\n') {
                line.trim();
                if (line.startsWith("ERR:")) {
                    Serial.printf("[BRIDGE] %s\n", line.c_str());
                    return "";
                }
                if (line.startsWith("RSP:")) return line.substring(4);
                line = "";
            } else if (line.length() < 512) {
                line += c;
            } else {
                line = "";
            }
        }
        delay(1);
    }
    return "";
}

// ── Feedback: LCD + LED + buzzer ─────────────────────
void indicate(bool granted, const String& reg) {
    pcf_all_off();  // reset

    if (granted) {
        // Green LED on
        pcf_write(0xFF & ~(1 << PCF_LED_GREEN));
        lcd_show("ACCESS GRANTED", reg.length() ? reg.c_str() : "");
        beep(200);
        delay(1500);
    } else {
        // Red LED on
        pcf_write(0xFF & ~(1 << PCF_LED_RED));
        lcd_show("ACCESS DENIED", reg.length() ? reg.c_str() : "");
        beep(500);
        delay(200);
        beep(500);
        delay(800);
    }

    pcf_all_off();
    lcd_show("Ready", "Scan QR code");
}

// ── Low-level helpers ────────────────────────────────
void pcf_write(uint8_t v) {
    Wire.beginTransmission(PCF_ADDR);
    Wire.write(v);
    Wire.endTransmission();
}

void pcf_all_off() {
    pcf_write(0xFF);   // all PCF pins HIGH → LEDs off, buzzer off
}

void lcd_show(const char* line1, const char* line2) {
    lcd.clear();
    lcd.setCursor(0, 0);
    lcd.print(line1);
    if (line2 && strlen(line2)) {
        lcd.setCursor(0, 1);
        char buf[17];
        strncpy(buf, line2, 16);
        buf[16] = 0;
        lcd.print(buf);
    }
}

void beep(uint16_t duration_ms) {
    pcf_write(0xFF & ~(1 << PCF_BUZZER));
    delay(duration_ms);
    pcf_all_off();
}