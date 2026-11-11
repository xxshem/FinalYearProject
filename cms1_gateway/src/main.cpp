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
#define SYNC_ACK_TIMEOUT_MS 1000
#define RADIO_TX_TIMEOUT_MS 1500
#define RADIO_RECOVERY_RETRIES 2
#define RADIO_RECOVERY_RETRY_DELAY_MS 100
#define RADIO_RECOVERY_RETRY_INTERVAL_MS 5000UL
#define RADIO_TX_ATTEMPTS 2
#define SERIAL_LINE_MAX 512

enum RadioState {
    CH1_GATE_LISTEN,
    CH2_SYNC_TX,
    CH2_ACK_WAIT,
    CH1_GATE_SERVICE,
    RADIO_RECOVERY,
    RADIO_RECOVERY_PENDING
};

RadioState radioState = CH1_GATE_LISTEN;
int currentCh = 1;
bool radioReady = false;
volatile bool radioTxComplete = false;
String serialLine;
String pendingCommand;
String queuedCommand;
bool serialLineTooLong = false;
String txPayload;
String txTransferId;
int txChannel = 1;
bool txWaitForAck = false;
uint8_t txAttempts = 0;
uint8_t recoveryAttempts = 0;
unsigned long txStartedAt = 0;
unsigned long ackDeadline = 0;
unsigned long recoveryNextAt = 0;

void scheduleBackgroundRecovery();

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

void reportSyncTimeout(const String& transferId) {
    Serial.print("C2:{\"type\":\"SYNC_TIMEOUT\",\"id\":\"");
    Serial.print(transferId);
    Serial.println("\"}");
}

void handleRadioTxDone() {
    radioTxComplete = true;
}

void configureRadio() {
    LoRa.setSpreadingFactor(7);
    LoRa.setSignalBandwidth(125E3);
    LoRa.setCodingRate4(5);
    LoRa.setPreambleLength(8);
    LoRa.setSyncWord(0x12);
    LoRa.enableCrc();
    LoRa.disableInvertIQ();
    LoRa.setTxPower(2);
}

bool initializeRadio(int channel) {
    radioReady = false;
    radioTxComplete = false;
    LoRa.setPins(LORA_SS, LORA_RST, LORA_DIO0);
    if (!LoRa.begin(channel == 1 ? FREQ_CH1 : FREQ_CH2)) return false;
    configureRadio();
    LoRa.onTxDone(handleRadioTxDone);
    currentCh = channel;
    radioTxComplete = false;
    radioReady = true;
    return true;
}

void applyChannel(int ch) {
    LoRa.idle();
    LoRa.setFrequency(ch == 1 ? FREQ_CH1 : FREQ_CH2);
    configureRadio();
    currentCh = ch;
}

void switchTo(int ch) {
    if (ch == currentCh || !radioReady) return;
    applyChannel(ch);
}

void finishTransmitFailure() {
    Serial.print("[GW1] RADIO_TX_FAILURE channel=");
    Serial.print(txChannel);
    Serial.print(" attempts=");
    Serial.println(txAttempts);
    if (txChannel == 2) reportSyncTxError(txTransferId);
    radioState = CH1_GATE_LISTEN;
    if (radioReady) switchTo(1);
    txPayload = "";
    txTransferId = "";
    txWaitForAck = false;
    if (!radioReady) {
        scheduleBackgroundRecovery();
    }
}

void startRadioRecovery() {
    Serial.println("[GW1] RADIO_RECOVERY after transmit failure");
    LoRa.onTxDone(nullptr);
    radioTxComplete = false;
    LoRa.end();
    radioReady = false;
    recoveryAttempts = 0;
    recoveryNextAt = millis();
    radioState = RADIO_RECOVERY;
}

void scheduleBackgroundRecovery() {
    radioReady = false;
    radioTxComplete = false;
    recoveryAttempts = 0;
    recoveryNextAt = millis() + RADIO_RECOVERY_RETRY_INTERVAL_MS;
    radioState = RADIO_RECOVERY_PENDING;
    Serial.print("[GW1] RADIO_RECOVERY_PENDING retry_ms=");
    Serial.println(RADIO_RECOVERY_RETRY_INTERVAL_MS);
}

void startTransmitAttempt() {
    if (!radioReady || !LoRa.beginPacket()) {
        startRadioRecovery();
        return;
    }
    LoRa.print(txPayload);
    radioTxComplete = false;
    if (!LoRa.endPacket(true)) {
        startRadioRecovery();
        return;
    }
    txStartedAt = millis();
    radioState = txChannel == 2 ? CH2_SYNC_TX : CH1_GATE_SERVICE;
}

void beginTransmit(const String& payload, int channel, bool waitForAck,
                   const String& transferId) {
    txPayload = payload;
    txChannel = channel;
    txWaitForAck = waitForAck;
    txTransferId = transferId;
    txAttempts = 1;
    if (!radioReady) {
        txAttempts = 0;
        if (radioState != RADIO_RECOVERY_PENDING) {
            scheduleBackgroundRecovery();
        }
        return;
    }
    switchTo(channel);
    startTransmitAttempt();
}

void recoverRadio() {
    if (static_cast<long>(millis() - recoveryNextAt) < 0) return;
    ++recoveryAttempts;
    const bool backgroundRecovery = txPayload.length() == 0;
    const int channel = backgroundRecovery ? 1 : txChannel;
    Serial.print("[GW1] RADIO_RECOVERY_ATTEMPT ");
    Serial.print(recoveryAttempts);
    Serial.print('/');
    Serial.println(RADIO_RECOVERY_RETRIES);
    if (initializeRadio(channel)) {
        Serial.print("[GW1] RADIO_RECOVERY_SUCCESS channel=");
        Serial.println(channel);
        if (txPayload.length() == 0) {
            radioState = CH1_GATE_LISTEN;
        } else if (txAttempts < RADIO_TX_ATTEMPTS) {
            ++txAttempts;
            startTransmitAttempt();
        } else {
            finishTransmitFailure();
        }
        return;
    }
    radioReady = false;
    radioTxComplete = false;
    Serial.print("[GW1] RADIO_RECOVERY_ATTEMPT_FAILED ");
    Serial.println(recoveryAttempts);
    if (recoveryAttempts < RADIO_RECOVERY_RETRIES) {
        recoveryNextAt = millis() + RADIO_RECOVERY_RETRY_DELAY_MS;
        return;
    }
    if (txPayload.length() == 0) {
        scheduleBackgroundRecovery();
    } else {
        finishTransmitFailure();
    }
}

void serviceTransmit() {
    if (!radioTxComplete) {
        if (millis() - txStartedAt >= RADIO_TX_TIMEOUT_MS) {
            Serial.println("[GW1] RADIO_TX_TIMEOUT; recovering radio");
            startRadioRecovery();
        }
        return;
    }
    if (txWaitForAck) {
        ackDeadline = millis() + SYNC_ACK_TIMEOUT_MS;
        radioState = CH2_ACK_WAIT;
    } else {
        radioState = CH1_GATE_LISTEN;
        switchTo(1);
        txPayload = "";
        txTransferId = "";
    }
}

void finishAckWait() {
    radioState = CH1_GATE_LISTEN;
    switchTo(1);
    txPayload = "";
    txTransferId = "";
    txWaitForAck = false;
}

void serviceAckWait() {
    const int packetSize = LoRa.parsePacket();
    if (packetSize > 0) {
        String response;
        while (LoRa.available()) response += static_cast<char>(LoRa.read());
        const SyncAckResult result = parseSyncAck(
            response.c_str(), response.length(), txTransferId.c_str());
        if (result == SyncAckResult::Positive || result == SyncAckResult::Negative) {
            Serial.print("C2:");
            Serial.println(response);
            finishAckWait();
            return;
        }
    }
    if (static_cast<long>(millis() - ackDeadline) >= 0) {
        reportSyncTimeout(txTransferId);
        finishAckWait();
    }
}

void startCommand(const String& command) {
    String line = command;
    line.trim();
    const bool waitForReply = line.startsWith("C2W:");
    int channel = 0;
    if (waitForReply) {
        channel = 2;
        line.remove(0, 4);
    } else if (line.startsWith("C1:")) {
        channel = 1;
        line.remove(0, 3);
    } else if (line.startsWith("C2:")) {
        channel = 2;
        line.remove(0, 3);
    } else {
        return;
    }
    if (line.length() == 0) return;

    String transferId;
    if (waitForReply) {
        String messageType;
        if (!jsonStringField(line, "type", messageType)
            || messageType != "SYNC_END"
            || !jsonStringField(line, "id", transferId)
            || transferId.length() == 0) {
            Serial.println("[GW1] invalid C2W sync frame");
            return;
        }
    } else if (channel == 2) {
        jsonStringField(line, "id", transferId);
    }
    beginTransmit(line, channel, waitForReply, transferId);
}

void serviceSerialInput() {
    while (Serial.available()) {
        const char c = static_cast<char>(Serial.read());
        if (c == '\r') continue;
        if (c == '\n') {
            if (serialLineTooLong) {
                Serial.println("[GW1] SERIAL_FRAME_TOO_LONG");
            } else if (serialLine.length() > 0) {
                if (pendingCommand.length() == 0) pendingCommand = serialLine;
                else if (queuedCommand.length() == 0) queuedCommand = serialLine;
                else Serial.println("[GW1] SERIAL_COMMAND_QUEUE_FULL");
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

void serviceGateReceive() {
    const int packetSize = LoRa.parsePacket();
    if (packetSize <= 0) return;
    String out = "C1:" + String(LoRa.packetRssi()) + "," +
                 String(LoRa.packetSnr(), 1) + ":";
    while (LoRa.available()) out += static_cast<char>(LoRa.read());
    Serial.println(out);
    radioState = CH1_GATE_SERVICE;
}

void setup() {
    Serial.begin(115200);
    delay(500);
    radioReady = initializeRadio(1);
    if (!radioReady) {
        radioState = RADIO_RECOVERY;
        recoveryAttempts = 0;
        recoveryNextAt = millis() + RADIO_RECOVERY_RETRY_DELAY_MS;
        Serial.print("[GW1] INITIAL_RADIO_INIT_FAILED retry_ms=");
        Serial.println(RADIO_RECOVERY_RETRY_DELAY_MS);
    } else {
        Serial.println("[GW1] ready on CH1 433.1 MHz; CH2 433.5 MHz, SF7");
    }
}

void loop() {
    serviceSerialInput();
    switch (radioState) {
        case CH2_SYNC_TX:
            if (radioTxComplete ||
                millis() - txStartedAt >= RADIO_TX_TIMEOUT_MS) {
                serviceTransmit();
            }
            break;
        case CH1_GATE_SERVICE:
            if (txPayload.length() > 0) {
                if (radioTxComplete ||
                    millis() - txStartedAt >= RADIO_TX_TIMEOUT_MS) {
                    serviceTransmit();
                }
            } else if (radioReady) {
                serviceGateReceive();
            }
            break;
        case CH2_ACK_WAIT:
            serviceAckWait();
            break;
        case RADIO_RECOVERY:
            recoverRadio();
            break;
        case RADIO_RECOVERY_PENDING:
            if (static_cast<long>(millis() - recoveryNextAt) >= 0) {
                recoveryAttempts = 0;
                recoveryNextAt = millis();
                radioState = RADIO_RECOVERY;
            }
            break;
        case CH1_GATE_LISTEN:
            if (radioReady) serviceGateReceive();
            break;
    }
    if (pendingCommand.length() == 0 && queuedCommand.length() > 0) {
        pendingCommand = queuedCommand;
        queuedCommand = "";
    }
    if ((radioState == CH1_GATE_LISTEN || radioState == CH1_GATE_SERVICE)
        && pendingCommand.length() > 0 && txPayload.length() == 0) {
        const String command = pendingCommand;
        pendingCommand = "";
        startCommand(command);
    }
}