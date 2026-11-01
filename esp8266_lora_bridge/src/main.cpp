#include <Arduino.h>
#include <Wire.h>
#include <LiquidCrystal_I2C.h>
#include <SPI.h>
#include <LoRa.h>

#define LORA_NSS          5
#define LORA_RESET       16
#define LORA_DIO0         4
#define I2C_SDA           0
#define I2C_SCL           2
#define LCD_ADDR        0x27
#define OUTPUTS_ADDR    0x20
#define RADIO_FREQ    433.1E6
#define UART_BAUD      115200
#define RADIO_TIMEOUT_MS 6000
#define UART_FRAME_TIMEOUT_MS 1000
#define MAX_UART_LINE  512
#define MAX_LORA_PACKET 255

#define PCF_LED_GREEN 0
#define PCF_LED_RED   1
#define PCF_BUZZER    2

enum FeedbackStage {
    FEEDBACK_IDLE,
    GRANTED_BEEP,
    GRANTED_WAIT,
    REJECTED_BEEP_1,
    REJECTED_GAP,
    REJECTED_BEEP_2,
    REJECTED_WAIT
};

LiquidCrystal_I2C lcd(LCD_ADDR, 16, 2);
String uartLine;
bool uartLineTooLong = false;
bool radioReady = false;
FeedbackStage feedbackStage = FEEDBACK_IDLE;
unsigned long feedbackDeadline = 0;
unsigned long lastUartByteAt = 0;

void sendBridgeError(const char* error) {
    Serial.print("ERR:");
    Serial.println(error);
}

void pcfWrite(uint8_t value) {
    Wire.beginTransmission(OUTPUTS_ADDR);
    Wire.write(value);
    Wire.endTransmission();
}

void allOutputsOff() {
    pcfWrite(0xFF);
}

void buzzerOn() {
    pcfWrite(0xFF & ~(1 << PCF_BUZZER));
}

void lcdShow(const String& first, const String& second) {
    lcd.clear();
    lcd.setCursor(0, 0);
    lcd.print(first.substring(0, 16));
    lcd.setCursor(0, 1);
    lcd.print(second.substring(0, 16));
}

String displaySafe(const String& value) {
    String safe;
    safe.reserve(value.length());
    for (size_t i = 0; i < value.length(); ++i) {
        const char c = value[i];
        safe += (c >= 32 && c != 127) ? c : ' ';
    }
    return safe;
}

void startResultFeedback(bool granted, const String& reg) {
    allOutputsOff();
    pcfWrite(0xFF & ~(1 << (granted ? PCF_LED_GREEN : PCF_LED_RED)));
    lcdShow(granted ? "ACCESS GRANTED" : "ACCESS DENIED", displaySafe(reg));

    buzzerOn();
    if (granted) {
        feedbackStage = GRANTED_BEEP;
        feedbackDeadline = millis() + 200;
    } else {
        feedbackStage = REJECTED_BEEP_1;
        feedbackDeadline = millis() + 500;
    }
}

void serviceFeedback() {
    if (feedbackStage == FEEDBACK_IDLE ||
        static_cast<long>(millis() - feedbackDeadline) < 0) {
        return;
    }

    switch (feedbackStage) {
        case GRANTED_BEEP:
            allOutputsOff();
            feedbackStage = GRANTED_WAIT;
            feedbackDeadline = millis() + 1500;
            break;
        case GRANTED_WAIT:
            feedbackStage = FEEDBACK_IDLE;
            lcdShow("Ready", "Scan QR code");
            break;
        case REJECTED_BEEP_1:
            allOutputsOff();
            feedbackStage = REJECTED_GAP;
            feedbackDeadline = millis() + 200;
            break;
        case REJECTED_GAP:
            buzzerOn();
            feedbackStage = REJECTED_BEEP_2;
            feedbackDeadline = millis() + 500;
            break;
        case REJECTED_BEEP_2:
            allOutputsOff();
            feedbackStage = REJECTED_WAIT;
            feedbackDeadline = millis() + 800;
            break;
        case REJECTED_WAIT:
            feedbackStage = FEEDBACK_IDLE;
            lcdShow("Ready", "Scan QR code");
            break;
        case FEEDBACK_IDLE:
            break;
    }
}

bool handleStatus(const String& line) {
    const int separator = line.indexOf(':', 7);
    const String status = separator < 0 ? line.substring(7) : line.substring(7, separator);
    const String detail = separator < 0 ? "" : displaySafe(line.substring(separator + 1));

    if (status == "INITIALIZING") {
        lcdShow("VVS GATE NODE", "Initializing...");
    } else if (status == "CAMERA_READY") {
        lcdShow("Camera ready", "");
    } else if (status == "CAMERA_ERROR") {
        allOutputsOff();
        lcdShow("CAMERA FAIL", "Check ribbon");
    } else if (status == "READY") {
        lcdShow("Ready", "Scan QR code");
    } else if (status == "GATE_ID_SET") {
        lcdShow("Gate ID set", detail);
    } else if (status == "GATE_ID") {
        lcdShow("Gate ID", detail);
    } else if (status == "CHECKING") {
        lcdShow("Checking...", detail);
    } else if (status == "REQUEST_TOO_LARGE") {
        lcdShow("REQ TOO LARGE", "LoRa limit 255 B");
    } else if (status == "VERIFIED") {
        startResultFeedback(true, detail);
    } else if (status == "REJECTED") {
        startResultFeedback(false, detail);
    } else {
        return false;
    }
    return true;
}

void handleRequest(const String& line) {
    if (!line.startsWith("REQ:")) {
        if (line.startsWith("STATUS:")) {
            if (radioReady && !handleStatus(line)) sendBridgeError("BAD_STATUS");
        } else {
            sendBridgeError("BAD_COMMAND");
        }
        return;
    }

    if (!radioReady) {
        sendBridgeError("RADIO_INIT_FAILED");
        return;
    }

    const String payload = line.substring(4);
    if (payload.length() == 0 || payload.length() > MAX_LORA_PACKET) {
        sendBridgeError("BAD_REQUEST");
        return;
    }

    LoRa.idle();
    LoRa.beginPacket();
    LoRa.print(payload);
    if (!LoRa.endPacket()) {
        sendBridgeError("TX_FAILED");
        return;
    }

    const unsigned long started = millis();
    while (millis() - started < RADIO_TIMEOUT_MS) {
        serviceFeedback();
        const int packetSize = LoRa.parsePacket();
        if (packetSize > 0) {
            String response;
            while (LoRa.available()) response += static_cast<char>(LoRa.read());
            Serial.print("RSP:");
            Serial.println(response);
            return;
        }
        delay(1);
    }

    sendBridgeError("TIMEOUT");
}

void finishUartLine() {
    if (uartLine.startsWith("REQ:")) {
        Serial.print("[UART RX first 16]");
        for (size_t i = 0; i < uartLine.length() && i < 16; ++i) {
            Serial.printf(" %02X", static_cast<uint8_t>(uartLine[i]));
        }
        Serial.println();
    }

    if (uartLineTooLong) {
        sendBridgeError("FRAME_TOO_LONG");
    } else if (uartLine.length() > 0) {
        handleRequest(uartLine);
    }
    uartLine = "";
    uartLineTooLong = false;
}

void setup() {
    Serial.begin(UART_BAUD, SERIAL_8N1);

    Wire.begin(I2C_SDA, I2C_SCL);
    Wire.setClock(100000);
    lcd.init();
    lcd.backlight();
    allOutputsOff();
    lcdShow("VVS GATE NODE", "Initializing...");

    LoRa.setPins(LORA_NSS, LORA_RESET, LORA_DIO0);
    LoRa.setSPIFrequency(4E6);
    if (!LoRa.begin(RADIO_FREQ)) {
        lcdShow("RADIO FAIL", "Check SX1278");
        return;
    }

    radioReady = true;
    LoRa.setSpreadingFactor(12);
    LoRa.setSignalBandwidth(125E3);
    LoRa.setCodingRate4(5);
    LoRa.setTxPower(20);
    lcdShow("LoRa ready", "Waiting for gate");
}

void loop() {
    while (Serial.available()) {
        const char c = static_cast<char>(Serial.read());
        lastUartByteAt = millis();

        if (c == '\r') continue;
        if (c == '\n') {
            finishUartLine();
        } else if (uartLine.length() < MAX_UART_LINE) {
            uartLine += c;
        } else {
            uartLineTooLong = true;
        }
    }

    if ((uartLine.length() > 0 || uartLineTooLong) &&
        millis() - lastUartByteAt > UART_FRAME_TIMEOUT_MS) {
        uartLine = "";
        uartLineTooLong = false;
        sendBridgeError("FRAME_TIMEOUT");
    }

    serviceFeedback();
}
