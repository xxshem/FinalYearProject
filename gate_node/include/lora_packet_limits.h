#ifndef LORA_PACKET_LIMITS_H
#define LORA_PACKET_LIMITS_H

#include <stddef.h>

constexpr size_t LORA_MAX_PACKET_BYTES = 255;

constexpr bool isLoRaPacketSizeValid(size_t byteLength) {
    return byteLength > 0 && byteLength <= LORA_MAX_PACKET_BYTES;
}

static_assert(isLoRaPacketSizeValid(255), "255-byte LoRa packets must be accepted");
static_assert(!isLoRaPacketSizeValid(256), "256-byte LoRa packets must be rejected");

#endif
