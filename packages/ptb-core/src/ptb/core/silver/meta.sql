-- Bookkeeping: which archive keys have been folded into this warehouse.
-- Makes every loader idempotent and lets `rebuild` skip finished work.
CREATE TABLE IF NOT EXISTS meta_archive_loaded (
    archive_key TEXT PRIMARY KEY,
    source      TEXT NOT NULL,
    loaded_at   TIMESTAMP NOT NULL
);
