/*
 * CMS2 LoRa Gateway — NodeMCU v3 USB serial <-> SX1278 on CH2.
 */

#include <Arduino.h>
#include <SPI.h>
#include <LoRa.h>

#define LORA_SS    5
#define LORA_RST   16
#define LORA_DIO0  4
#define FREQ_CH2   433.5E6
#define RADIO_TX_TIMEOUT_MS 1500
#define RADIO_REPLY_TIMEOUT_MS 2000
#define RADIO_RECOVERY_RETRIES 2
#define RADIO_TX_ATTEMPTS 2
#define SERIAL_LINE_MAX 512

enum RadioState {
    RADIO_READY,
    RADIO_TX,
    RADIO_REPLY_WAIT,
    RADIO_RECOVERY
};

RadioState radioState = RADIO_READY;
bool radioReady = false;
volatile bool radioTxComplete = false;
String serialLine;
String pendingCommand;
bool serialLineTooLong = false;
String txPayload;
bool waitForReply = false;
uint8_t txAttempts = 0;
uint8_t recoveryAttempts = 0;
unsigned long txStartedAt = 0;
unsigned long replyDeadline = 0;
unsigned long recoveryNextAt = 0;

void configureRadio() {
    LoRa.setSpreadingFactor(7);
    LoRa.setSignalBandwidth(125E3);
    LoRa.setCodingRate4(5);
    LoRa.setTxPower(2);
    LoRa.setPreambleLength(8);
    LoRa.setSyncWord(0x12);
    LoRa.enableCrc();
    LoRa.disableInvertIQ();
}

void handleRadioTxDone() {
    radioTxComplete = true;
}

bool initializeRadio() {
    LoRa.setPins(LORA_SS, LORA_RST, LORA_DIO0);
    if (!LoRa.begin(FREQ_CH2)) return false;
    configureRadio();
    LoRa.onTxDone(handleRadioTxDone);
    radioReady = true;
    return true;
}

void finishTransmitFailure() {
    Serial.println("[GW2] RADIO_TX_FAILURE");
    Serial.println("C2:TX_ERROR");
    txPayload = "";
    waitForReply = false;
    radioState = RADIO_READY;
}

void startRecovery() {
    Serial.println("[GW2] RADIO_RECOVERY after transmit failure");
    LoRa.end();
    radioReady = false;
    recoveryAttempts = 0;
    recoveryNextAt = millis();
    radioState = RADIO_RECOVERY;
}

void startTransmitAttempt() {
    if (!radioReady || !LoRa.beginPacket()) {
        startRecovery();
        return;
    }
    LoRa.print(txPayload);
    radioTxComplete = false;
    if (!LoRa.endPacket(true)) {
        startRecovery();
        return;
    }
    txStartedAt = millis();
    radioState = RADIO_TX;
}

void beginCommand(const String& command) {
    String line = command;
    line.trim();
    waitForReply = line.startsWith("C2W:");
    if (waitForReply) {
        line.remove(0, 4);
    } else if (line.startsWith("C2:")) {
        line.remove(0, 3);
    } else {
        return;
    }
    if (line.length() == 0) return;

    txPayload = line;
    txAttempts = radioReady ? 1 : 0;
    if (!radioReady) {
        recoveryAttempts = 0;
        recoveryNextAt = millis();
        radioState = RADIO_RECOVERY;
        return;
    }
    startTransmitAttempt();
}

void recoverRadio() {
    if (static_cast<long>(millis() - recoveryNextAt) < 0) return;
    ++recoveryAttempts;
    if (initializeRadio()) {
        if (txPayload.length() == 0) {
            radioState = RADIO_READY;
        } else if (txAttempts < RADIO_TX_ATTEMPTS) {
            ++txAttempts;
            startTransmitAttempt();
        } else {
            finishTransmitFailure();
        }
        return;
    }
    if (recoveryAttempts < RADIO_RECOVERY_RETRIES) {
        recoveryNextAt = millis() + 100;
        return;
    }
    radioReady = false;
    if (txPayload.length() == 0) {
        Serial.println("[GW2] RADIO_INIT_FAILURE; radio remains unavailable");
        radioState = RADIO_READY;
    } else {
        finishTransmitFailure();
    }
}

void serviceTransmit() {
    if (!radioTxComplete) {
        if (millis() - txStartedAt >= RADIO_TX_TIMEOUT_MS) {
            Serial.println("[GW2] RADIO_TX_TIMEOUT");
            startRecovery();
        }
        return;
    }
    if (waitForReply) {
        replyDeadline = millis() + RADIO_REPLY_TIMEOUT_MS;
        radioState = RADIO_REPLY_WAIT;
    } else {
        txPayload = "";
        radioState = RADIO_READY;
    }
}

void serviceReplyWait() {
    const int packetSize = LoRa.parsePacket();
    if (packetSize > 0) {
        Serial.print("C2:");
        while (LoRa.available()) Serial.write(static_cast<uint8_t>(LoRa.read()));
        Serial.println();
        txPayload = "";
        waitForReply = false;
        radioState = RADIO_READY;
    } else if (static_cast<long>(millis() - replyDeadline) >= 0) {
        Serial.println("C2:TIMEOUT");
        txPayload = "";
        waitForReply = false;
        radioState = RADIO_READY;
    }
}

void serviceSerialInput() {
    while (Serial.available()) {
        const char c = static_cast<char>(Serial.read());
        if (c == '\r') continue;
        if (c == '\n') {
            if (serialLineTooLong) {
                Serial.println("[GW2] SERIAL_FRAME_TOO_LONG");
            } else if (serialLine.length() > 0) {
                if (pendingCommand.length() == 0) pendingCommand = serialLine;
                else Serial.println("[GW2] SERIAL_COMMAND_BUSY");
            }
            serialLine = "";
            serialLineTooLong = false;
        } else if (serialLine.length() < SERIAL_LINE_MAX) {
            serialLine += c;
        } else {
            serialLineTooLong = true;
        }
    }
}

void setup() {
    Serial.begin(115200);
    delay(500);
    radioReady = initializeRadio();
    if (!radioReady) {
        radioState = RADIO_RECOVERY;
        recoveryAttempts = 0;
        recoveryNextAt = millis() + 100;
        Serial.println("[GW2] init failed; recovery scheduled");
    } else {
        Serial.println("[GW2] ready on 433.5 MHz");
    }
}

void loop() {
    serviceSerialInput();
    switch (radioState) {
        case RADIO_TX:
            if (radioTxComplete ||
                millis() - txStartedAt >= RADIO_TX_TIMEOUT_MS) {
                serviceTransmit();
            }
            break;
        case RADIO_REPLY_WAIT:
            serviceReplyWait();
            break;
        case RADIO_RECOVERY:
            recoverRadio();
            break;
        case RADIO_READY:
            if (radioReady) {
                const int packetSize = LoRa.parsePacket();
                if (packetSize > 0) {
                    Serial.print("C2:");
                    while (LoRa.available()) Serial.write(static_cast<uint8_t>(LoRa.read()));
                    Serial.println();
                }
            }
            break;
    }
    if (radioState == RADIO_READY && pendingCommand.length() > 0) {
        const String command = pendingCommand;
        pendingCommand = "";
        beginCommand(command);
    }
}
