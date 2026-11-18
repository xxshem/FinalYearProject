#ifndef VVS_GATE_CONFIG_H
#define VVS_GATE_CONFIG_H

#ifndef GATE_ID_VALUE
#define GATE_ID_VALUE "GATE_A"
#endif

constexpr char GATE_ID[] = GATE_ID_VALUE;

constexpr bool isValidGateId(const char* id) {
    return id[0] == 'G' && id[1] == 'A' && id[2] == 'T' &&
        id[3] == 'E' && id[4] == '_' &&
        id[5] >= 'A' && id[5] <= 'Z' && id[6] == '\0';
}

static_assert(isValidGateId(GATE_ID),
              "GATE_ID_VALUE must match GATE_[A-Z] (for example, GATE_A)");

#endif
