-- vhf-watch SQLite schema, version 1.
--
-- Applied by store/db.py migrations keyed on PRAGMA user_version. To change
-- the schema, add a numbered migration in db.py — never edit a shipped
-- migration or apply changes by hand (docs/definition-of-done.md).
--
-- Conventions:
--   * IDs are ULID strings (chronologically sortable).
--   * Timestamps are ISO-8601 UTC strings ("...+00:00").
--   * JSON columns hold lists/objects that only the app interprets.
--   * Records are append-only (docs/decisions.md D9): corrections are
--     `annotation` rows. Triggers below enforce the most safety-relevant
--     cases at the database layer.

-- One segmented radio transmission. PCM itself lives on disk at audio_path;
-- the SHA-256 makes the archived file verifiable from the record.
CREATE TABLE transmission (
    id            TEXT PRIMARY KEY,
    channel       TEXT NOT NULL,
    started_at    TEXT NOT NULL,
    ended_at      TEXT NOT NULL,
    duration_ms   INTEGER NOT NULL,
    sample_rate   INTEGER NOT NULL,
    rms_dbfs      REAL NOT NULL,
    peak_dbfs     REAL NOT NULL,
    est_snr_db    REAL,
    audio_path    TEXT,
    audio_sha256  TEXT,
    source        TEXT NOT NULL          -- "live" | "file:<name>"
);

CREATE INDEX idx_transmission_started_at ON transmission (started_at);

-- One ASR pass over one transmission. Separate from transmission because the
-- archive will be re-transcribed with better models and the results compared
-- (docs/architecture.md §5.8). (transmission_id, engine) is deliberately not
-- unique: re-runs of the same engine are new rows, never overwrites.
CREATE TABLE transcript (
    id              TEXT PRIMARY KEY,
    transmission_id TEXT NOT NULL REFERENCES transmission (id),
    engine          TEXT NOT NULL,       -- e.g. "mlx-whisper:small.en"
    text            TEXT NOT NULL,
    words           TEXT NOT NULL DEFAULT '[]',  -- JSON list of Word
    avg_logprob     REAL,
    no_speech_prob  REAL,
    language        TEXT,
    latency_ms      INTEGER,
    created_at      TEXT NOT NULL
);

CREATE INDEX idx_transcript_transmission ON transcript (transmission_id);

-- Stub, deliberately unused: voice must stand alone (docs/decisions.md D2).
-- Exists so a future DSC supplement is a feature, not a schema migration.
CREATE TABLE dsc_message (
    id          TEXT PRIMARY KEY,
    received_at TEXT NOT NULL,
    raw         TEXT NOT NULL,
    notes       TEXT
);

CREATE TABLE detection (
    id                TEXT PRIMARY KEY,
    transmission_id   TEXT NOT NULL REFERENCES transmission (id),
    severity          TEXT NOT NULL,     -- Severity enum value
    confidence        REAL NOT NULL,     -- 0..1
    matched_terms     TEXT NOT NULL DEFAULT '[]',  -- JSON list of strings
    tier              TEXT NOT NULL,     -- "watchword" | "llm" | "both"
    classifier_output TEXT,              -- JSON, null when LLM unavailable
    created_at        TEXT NOT NULL
);

CREATE INDEX idx_detection_transmission ON detection (transmission_id);

-- One real-world emergency (docs/decisions.md D12). Incidents are the one
-- mutable record: they stay open and accrue, severity escalates (never
-- auto-de-escalates), and acknowledgment is written in place.
CREATE TABLE incident (
    id               TEXT PRIMARY KEY,
    opened_at        TEXT NOT NULL,
    last_activity_at TEXT NOT NULL,
    closed_at        TEXT,
    status           TEXT NOT NULL,      -- "open" | "closed"
    severity         TEXT NOT NULL,
    channels         TEXT NOT NULL DEFAULT '[]',  -- JSON list of strings
    summary          TEXT,               -- JSON IncidentSummary
    confidence       REAL NOT NULL,
    alerted_at       TEXT,
    acknowledged_at  TEXT,
    acknowledged_by  TEXT
);

CREATE INDEX idx_incident_opened_at ON incident (opened_at);

CREATE TABLE incident_member (
    incident_id  TEXT NOT NULL REFERENCES incident (id),
    detection_id TEXT NOT NULL REFERENCES detection (id),
    added_at     TEXT NOT NULL,
    PRIMARY KEY (incident_id, detection_id)
);

-- Corrections and operator notes. THE append-only mechanism: anything wrong
-- in a transcript or summary gets corrected here, with authorship, never by
-- editing the original row.
CREATE TABLE annotation (
    id           TEXT PRIMARY KEY,
    subject_type TEXT NOT NULL,          -- "transmission" | "transcript" | "incident"
    subject_id   TEXT NOT NULL,
    author       TEXT NOT NULL,
    body         TEXT NOT NULL,
    created_at   TEXT NOT NULL
);

CREATE INDEX idx_annotation_subject ON annotation (subject_type, subject_id);

-- Delivery log: one row per send attempt per channel.
CREATE TABLE alert (
    id          TEXT PRIMARY KEY,
    incident_id TEXT NOT NULL REFERENCES incident (id),
    channel     TEXT NOT NULL,           -- AlertChannel.name
    is_update   INTEGER NOT NULL DEFAULT 0,
    sent_at     TEXT NOT NULL,
    ok          INTEGER NOT NULL,
    detail      TEXT                     -- error message or provider receipt
);

-- Per-recipient links in alert notifications. Only the hash is stored — a
-- leaked database must not mint working links.
CREATE TABLE share_token (
    id          TEXT PRIMARY KEY,
    incident_id TEXT NOT NULL REFERENCES incident (id),
    token_hash  TEXT NOT NULL,
    recipient   TEXT NOT NULL,
    created_at  TEXT NOT NULL,
    expires_at  TEXT NOT NULL,
    revoked_at  TEXT
);

CREATE TABLE access_log (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    token_id    TEXT REFERENCES share_token (id),
    path        TEXT NOT NULL,
    accessed_at TEXT NOT NULL,
    remote_addr TEXT
);

-- Watchdog events: heartbeat gaps, noise-floor drift, self-test results.
CREATE TABLE health_event (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    stage      TEXT NOT NULL,
    kind       TEXT NOT NULL,
    detail     TEXT,                     -- JSON
    created_at TEXT NOT NULL
);

-- Append-only enforcement (docs/decisions.md D9). An incident record from a
-- real emergency may end up in an after-action review or legal proceeding;
-- these make "never UPDATE a transcript" a database guarantee instead of a
-- convention someone can forget under deadline.
CREATE TRIGGER transcript_append_only
BEFORE UPDATE ON transcript
BEGIN
    SELECT RAISE(ABORT, 'transcript rows are append-only; add an annotation instead');
END;

CREATE TRIGGER annotation_append_only
BEFORE UPDATE ON annotation
BEGIN
    SELECT RAISE(ABORT, 'annotation rows are append-only; add another annotation');
END;

CREATE TRIGGER transmission_append_only
BEFORE UPDATE ON transmission
-- Exception: archiving fills in audio_path/audio_sha256 after the row is
-- created. Everything else about a transmission is immutable.
WHEN OLD.audio_path IS NOT NULL
BEGIN
    SELECT RAISE(ABORT, 'transmission rows are append-only once archived');
END;
