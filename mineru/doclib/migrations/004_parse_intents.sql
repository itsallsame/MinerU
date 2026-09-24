-- v004: A released work-intent key is a durable tombstone. Queue registration
-- must reject it inside the same write transaction as batch selection.
CREATE TABLE IF NOT EXISTS parse_intents (
    consumer_key TEXT PRIMARY KEY NOT NULL,
    state        TEXT NOT NULL CHECK (state IN ('active', 'released')),
    created_at   INTEGER NOT NULL,
    released_at  INTEGER,
    result_json  TEXT
);
ALTER TABLE parse_consumers ADD COLUMN released_at INTEGER;
ALTER TABLE parse_consumers ADD COLUMN release_outcome TEXT;
ALTER TABLE parse_consumers ADD COLUMN release_status TEXT;
