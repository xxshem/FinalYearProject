#include "qr_decoder.h"
#include "ESP32QRCodeReader.h"

static ESP32QRCodeReader *reader = nullptr;

bool qr_camera_init() {
    reader = new ESP32QRCodeReader(CAMERA_MODEL_AI_THINKER);
    reader->setup();
    reader->beginOnCore(1);
    Serial.println("[QR] camera ready (ESP32QRCodeReader)");
    return true;
}

String qr_scan_once() {
    if (!reader) return "";

    struct QRCodeData qrCodeData;
    if (reader->receiveQrCode(&qrCodeData, 100)) {
        if (qrCodeData.valid) {
            String payload = String((const char *)qrCodeData.payload);
            Serial.printf("[QR] decoded: %s\n", payload.c_str());
            return payload;
        }
    }
    return "";
}