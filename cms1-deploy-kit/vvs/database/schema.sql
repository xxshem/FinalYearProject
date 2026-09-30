CREATE TABLE IF NOT EXISTS vehicles (
    vehicle_id       INTEGER PRIMARY KEY AUTOINCREMENT,
    vid              TEXT UNIQUE NOT NULL,
    owner_name       TEXT NOT NULL,
    registration_no  TEXT UNIQUE NOT NULL,
    vehicle_type     TEXT CHECK(vehicle_type IN ('bike','car','truck','bus')) NOT NULL,
    qr_token         TEXT UNIQUE NOT NULL,
    valid_from       DATE NOT NULL,
    valid_to         DATE NOT NULL,
        is_active        INTEGER NOT NULL DEFAULT 1 CHECK(is_active IN (0, 1)),
    created_at       TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        updated_at       TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        CHECK(date(valid_from) IS NOT NULL AND date(valid_to) IS NOT NULL
            AND date(valid_from) < date(valid_to))
);

CREATE TABLE IF NOT EXISTS gates (
    gate_id     TEXT PRIMARY KEY,
    location    TEXT NOT NULL,
    description TEXT,
    status      TEXT DEFAULT 'offline',
    last_seen   TIMESTAMP,
    created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS verification_logs (
    log_id              INTEGER PRIMARY KEY AUTOINCREMENT,
    vehicle_id          INTEGER REFERENCES vehicles(vehicle_id) ON DELETE RESTRICT,
    gate_id             TEXT NOT NULL REFERENCES gates(gate_id) ON DELETE RESTRICT,
    qr_token            TEXT NOT NULL,
    scan_time           TIMESTAMP NOT NULL,
    verification_result TEXT NOT NULL,
    denial_reason       TEXT,
    image_path          TEXT,
    synced              INTEGER DEFAULT 0,
    CHECK(verification_result IN ('granted', 'denied'))
);

CREATE TABLE IF NOT EXISTS entry_exit (
    record_id        INTEGER PRIMARY KEY AUTOINCREMENT,
    vehicle_id       INTEGER NOT NULL REFERENCES vehicles(vehicle_id) ON DELETE RESTRICT,
    entry_gate       TEXT NOT NULL REFERENCES gates(gate_id) ON DELETE RESTRICT,
    entry_time       TIMESTAMP NOT NULL,
    exit_gate        TEXT REFERENCES gates(gate_id) ON DELETE RESTRICT,
    exit_time        TIMESTAMP,
    duration_minutes REAL,
    updated_at       TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    CHECK((exit_gate IS NULL) = (exit_time IS NULL)),
    CHECK(duration_minutes IS NULL OR duration_minutes >= 0)
);

CREATE TABLE IF NOT EXISTS lora_metrics (
    metric_id        INTEGER PRIMARY KEY AUTOINCREMENT,
    test_id          TEXT NOT NULL,
    timestamp        TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    gate_id          TEXT,
    channel          INTEGER NOT NULL,
    distance_m       INTEGER,
    rssi_dbm         REAL,
    snr_db           REAL,
    packets_sent     INTEGER,
    packets_received INTEGER,
    pdr_percent      REAL,
    latency_ms       REAL,
    test_environment TEXT,
    notes            TEXT
);

CREATE TABLE IF NOT EXISTS operational_metrics (
    metric_id        INTEGER PRIMARY KEY AUTOINCREMENT,
    test_id          TEXT NOT NULL,
    timestamp        TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    gate_id          TEXT,
    response_time_ms REAL,
    cache_hit        INTEGER,
    throughput_vpm   REAL,
    db_query_ms      REAL,
    offline_mode     INTEGER DEFAULT 0,
    queue_size       INTEGER,
    notes            TEXT
);

CREATE TABLE IF NOT EXISTS power_metrics (
    metric_id   INTEGER PRIMARY KEY AUTOINCREMENT,
    test_id     TEXT NOT NULL,
    timestamp   TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    gate_id     TEXT,
    state       TEXT NOT NULL,
    voltage_v   REAL,
    current_ma  REAL,
    power_mw    REAL,
    notes       TEXT
);

CREATE TABLE IF NOT EXISTS system_events (
    event_id    INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp   TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    event_type  TEXT NOT NULL,
    source      TEXT,
    description TEXT
);

CREATE TABLE IF NOT EXISTS offline_queue (
    queue_id       INTEGER PRIMARY KEY AUTOINCREMENT,
    operation_type TEXT NOT NULL,
    vehicle_id     INTEGER,
    gate_id        TEXT,
    qr_token       TEXT,
    payload        TEXT,
    created_at     TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    attempt_count  INTEGER DEFAULT 0,
    last_attempt   TIMESTAMP,
    status         TEXT DEFAULT 'pending',
    UNIQUE(operation_type, qr_token, created_at)
);

CREATE INDEX IF NOT EXISTS idx_verif_time    ON verification_logs(scan_time);
CREATE INDEX IF NOT EXISTS idx_verif_gate    ON verification_logs(gate_id);
CREATE INDEX IF NOT EXISTS idx_verif_vehicle ON verification_logs(vehicle_id);
CREATE INDEX IF NOT EXISTS idx_entry_vehicle ON entry_exit(vehicle_id);
CREATE INDEX IF NOT EXISTS idx_entry_gate    ON entry_exit(entry_gate);
CREATE INDEX IF NOT EXISTS idx_exit_gate     ON entry_exit(exit_gate);
CREATE INDEX IF NOT EXISTS idx_lora_test     ON lora_metrics(test_id);
CREATE INDEX IF NOT EXISTS idx_lora_dist     ON lora_metrics(distance_m);
CREATE INDEX IF NOT EXISTS idx_op_test       ON operational_metrics(test_id);
CREATE INDEX IF NOT EXISTS idx_power_state   ON power_metrics(state);
CREATE INDEX IF NOT EXISTS idx_queue_status  ON offline_queue(status);

-- SQLite cannot add foreign-key constraints to existing columns with ALTER TABLE.
-- These triggers provide equivalent insert/update checks for legacy databases.
CREATE TRIGGER IF NOT EXISTS entry_exit_integrity_insert
BEFORE INSERT ON entry_exit
WHEN NOT EXISTS (SELECT 1 FROM vehicles WHERE vehicle_id=NEW.vehicle_id)
  OR NOT EXISTS (SELECT 1 FROM gates WHERE gate_id=NEW.entry_gate)
  OR (NEW.exit_gate IS NOT NULL AND
      NOT EXISTS (SELECT 1 FROM gates WHERE gate_id=NEW.exit_gate))
BEGIN
    SELECT RAISE(ABORT, 'entry_exit references an unknown vehicle or gate');
END;

CREATE TRIGGER IF NOT EXISTS entry_exit_integrity_update
BEFORE UPDATE OF vehicle_id, entry_gate, exit_gate ON entry_exit
WHEN NOT EXISTS (SELECT 1 FROM vehicles WHERE vehicle_id=NEW.vehicle_id)
  OR NOT EXISTS (SELECT 1 FROM gates WHERE gate_id=NEW.entry_gate)
  OR (NEW.exit_gate IS NOT NULL AND
      NOT EXISTS (SELECT 1 FROM gates WHERE gate_id=NEW.exit_gate))
BEGIN
    SELECT RAISE(ABORT, 'entry_exit references an unknown vehicle or gate');
END;

CREATE TRIGGER IF NOT EXISTS verification_logs_integrity_insert
BEFORE INSERT ON verification_logs
WHEN (NEW.vehicle_id IS NOT NULL AND
      NOT EXISTS (SELECT 1 FROM vehicles WHERE vehicle_id=NEW.vehicle_id))
  OR NOT EXISTS (SELECT 1 FROM gates WHERE gate_id=NEW.gate_id)
BEGIN
    SELECT RAISE(ABORT, 'verification_logs references an unknown vehicle or gate');
END;

CREATE TRIGGER IF NOT EXISTS verification_logs_integrity_update
BEFORE UPDATE OF vehicle_id, gate_id ON verification_logs
WHEN (NEW.vehicle_id IS NOT NULL AND
      NOT EXISTS (SELECT 1 FROM vehicles WHERE vehicle_id=NEW.vehicle_id))
  OR NOT EXISTS (SELECT 1 FROM gates WHERE gate_id=NEW.gate_id)
BEGIN
    SELECT RAISE(ABORT, 'verification_logs references an unknown vehicle or gate');
END;

CREATE TRIGGER IF NOT EXISTS vehicles_restrict_delete
BEFORE DELETE ON vehicles
WHEN EXISTS (SELECT 1 FROM entry_exit WHERE vehicle_id=OLD.vehicle_id)
  OR EXISTS (SELECT 1 FROM verification_logs WHERE vehicle_id=OLD.vehicle_id)
BEGIN
    SELECT RAISE(ABORT, 'vehicle is referenced by verification history');
END;

CREATE TRIGGER IF NOT EXISTS gates_restrict_delete
BEFORE DELETE ON gates
WHEN EXISTS (SELECT 1 FROM entry_exit
             WHERE entry_gate=OLD.gate_id OR exit_gate=OLD.gate_id)
  OR EXISTS (SELECT 1 FROM verification_logs WHERE gate_id=OLD.gate_id)
BEGIN
    SELECT RAISE(ABORT, 'gate is referenced by verification history');
END;

INSERT OR IGNORE INTO gates (gate_id, location, description, status) VALUES
    ('GATE_A', 'Main Entrance', 'Primary campus entry', 'offline'),
    ('GATE_B', 'Back Gate',     'Secondary entry/exit', 'offline'),
    ('GATE_C', 'Hostel Gate',   'Hostel access point', 'offline');