-- v005: Keep one durable first response for each consumer submission generation.
-- A repeated force request cannot create another batch after the first one completes.
ALTER TABLE parse_intents ADD COLUMN latest_attempt INTEGER NOT NULL DEFAULT 0;
CREATE TABLE IF NOT EXISTS parse_submissions (
    consumer_key TEXT NOT NULL REFERENCES parse_intents(consumer_key) ON DELETE CASCADE,
    attempt INTEGER NOT NULL CHECK (attempt > 0),
    request_fingerprint TEXT NOT NULL,
    response_json TEXT NOT NULL,
    created_at INTEGER NOT NULL,
    PRIMARY KEY (consumer_key, attempt)
);
