#include "qr_decoder.h"
#include "ESP32QRCodeReader.h"

static ESP32QRCodeReader *reader = nullptr;

bool qr_camera_init() {
    reader = new ESP32QRCodeReader(CAMERA_MODEL_AI_THINKER);
    reader->setDebug(false);
    const QRCodeReaderSetupErr setupResult = reader->setup();
    if (setupResult != SETUP_OK) {
        delete reader;
        reader = nullptr;
        return false;
    }

    reader->beginOnCore(1);
    return true;
}

String qr_scan_once() {
    if (!reader) return "";

    struct QRCodeData qrCodeData;
    if (reader->receiveQrCode(&qrCodeData, 100)) {
        if (qrCodeData.valid) {
            String payload = String((const char *)qrCodeData.payload);
            return payload;
        }
    }
    return "";
}