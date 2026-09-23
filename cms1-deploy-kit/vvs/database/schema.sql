CREATE TABLE IF NOT EXISTS vehicles (
    vehicle_id       INTEGER PRIMARY KEY AUTOINCREMENT,
    vid              TEXT UNIQUE NOT NULL,
    owner_name       TEXT NOT NULL,
    registration_no  TEXT UNIQUE NOT NULL,
    vehicle_type     TEXT CHECK(vehicle_type IN ('bike','car','truck','bus')) NOT NULL,
    qr_token         TEXT UNIQUE NOT NULL,
    valid_from       DATE NOT NULL,
    valid_to         DATE NOT NULL,
    is_active        INTEGER DEFAULT 1,
    created_at       TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS gates (
    gate_id     TEXT PRIMARY KEY,
    location    TEXT NOT NULL,
    description TEXT,
    status      TEXT DEFAULT 'offline',
    last_seen   TIMESTAMP,
    created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS verification_logs (
    log_id              INTEGER PRIMARY KEY AUTOINCREMENT,
    vehicle_id          INTEGER,
    gate_id             TEXT NOT NULL,
    qr_token            TEXT NOT NULL,
    scan_time           TIMESTAMP NOT NULL,
    verification_result TEXT NOT NULL,
    denial_reason       TEXT,
    image_path          TEXT,
    synced              INTEGER DEFAULT 0,
    FOREIGN KEY (vehicle_id) REFERENCES vehicles(vehicle_id)
);

CREATE TABLE IF NOT EXISTS entry_exit (
    record_id        INTEGER PRIMARY KEY AUTOINCREMENT,
    vehicle_id       INTEGER NOT NULL,
    entry_gate       TEXT NOT NULL,
    entry_time       TIMESTAMP NOT NULL,
    exit_gate        TEXT,
    exit_time        TIMESTAMP,
    duration_minutes REAL
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
CREATE INDEX IF NOT EXISTS idx_lora_test     ON lora_metrics(test_id);
CREATE INDEX IF NOT EXISTS idx_lora_dist     ON lora_metrics(distance_m);
CREATE INDEX IF NOT EXISTS idx_op_test       ON operational_metrics(test_id);
CREATE INDEX IF NOT EXISTS idx_power_state   ON power_metrics(state);
CREATE INDEX IF NOT EXISTS idx_queue_status  ON offline_queue(status);

INSERT OR IGNORE INTO gates (gate_id, location, description, status) VALUES
    ('GATE_A', 'Main Entrance', 'Primary campus entry', 'offline'),
    ('GATE_B', 'Back Gate',     'Secondary entry/exit', 'offline'),
    ('GATE_C', 'Hostel Gate',   'Hostel access point', 'offline');