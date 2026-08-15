-- Conformed fixtures, plus the bridge from each source's own match id.
-- See matches.py for the +/-36h resolution window.
CREATE SEQUENCE IF NOT EXISTS seq_match_id START 1;

CREATE TABLE IF NOT EXISTS dim_match (
    match_id     BIGINT PRIMARY KEY,
    competition  TEXT NOT NULL,
    season       TEXT NOT NULL,
    kickoff_utc  TIMESTAMP NOT NULL,
    home_team_id BIGINT NOT NULL,
    away_team_id BIGINT NOT NULL,
    match_key    TEXT NOT NULL  -- human-readable debugging aid, never a join key
);

CREATE TABLE IF NOT EXISTS map_match_source (
    match_id        BIGINT NOT NULL,
    source          TEXT NOT NULL,
    source_match_id TEXT NOT NULL,
    method          TEXT NOT NULL,
    confidence      DOUBLE NOT NULL,
    PRIMARY KEY (source, source_match_id)
);

-- Resolution failures land here rather than being dropped. `ptb verify`
-- reports them, and the underlying src_ rows are untouched.
CREATE TABLE IF NOT EXISTS unresolved_match (
    source          TEXT NOT NULL,
    source_match_id TEXT NOT NULL,
    reason          TEXT NOT NULL,
    detail          TEXT,
    PRIMARY KEY (source, source_match_id)
);
