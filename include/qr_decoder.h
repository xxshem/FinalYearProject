#ifndef QR_DECODER_H
#define QR_DECODER_H

#include <Arduino.h>

bool qr_camera_init();
String qr_scan_once();

#endif