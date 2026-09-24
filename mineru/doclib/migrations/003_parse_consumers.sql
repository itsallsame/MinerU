-- v003: Record every consumer of a queued parse before queued-only cancellation is exposed.
-- A protected claim represents implicit CLI/SDK or background work. Existing active
-- rows get one so an upgrade can never mistake an untracked batch for exclusive work.
CREATE TABLE IF NOT EXISTS parse_consumers (
    parse_id       INTEGER NOT NULL REFERENCES parses(id) ON DELETE CASCADE,
    consumer_key   TEXT    NOT NULL,
    protected      INTEGER NOT NULL CHECK (protected IN (0, 1)),
    created_at     INTEGER NOT NULL,
    PRIMARY KEY (parse_id, consumer_key)
);
CREATE INDEX IF NOT EXISTS idx_parse_consumers_key ON parse_consumers(consumer_key, parse_id);
INSERT OR IGNORE INTO parse_consumers (parse_id, consumer_key, protected, created_at)
SELECT id, 'system:legacy', 1, created_at FROM parses WHERE status IN ('pending', 'parsing');
