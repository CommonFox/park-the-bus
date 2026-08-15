-- Conformed players, plus the bridge from each source's own player id.
--
-- FPL's `code` is stable across seasons where `element_id` is reassigned, so
-- the spine is built on code and map_player_source stores it as the FPL
-- source_player_id. vaastav needs no rows here at all: its data already keys on
-- per-season element_id, so it reaches player_id through
-- src_fpl_element (season, element_id) -> code -> map_player_source.
CREATE SEQUENCE IF NOT EXISTS seq_player_id START 1;

CREATE TABLE IF NOT EXISTS dim_player (
    player_id         BIGINT PRIMARY KEY,
    canonical_name    TEXT NOT NULL,
    -- Deliberately not UNIQUE, unlike dim_team.normalized_name: two different
    -- Danny Wards have played in the Premier League. Uniqueness is the
    -- resolver's problem, enforced by the ambiguity rules, not the schema's.
    normalized_name   TEXT NOT NULL,
    birth_date        DATE,
    nationality       TEXT,
    fpl_code          INTEGER,
    opta_code         TEXT,
    first_seen_season TEXT,
    last_seen_season  TEXT
);

CREATE TABLE IF NOT EXISTS map_player_source (
    player_id        BIGINT NOT NULL,
    source           TEXT NOT NULL,
    source_player_id TEXT NOT NULL,
    method           TEXT NOT NULL,
    confidence       DOUBLE NOT NULL,
    PRIMARY KEY (source, source_player_id)
);

-- Ambiguous players land here rather than being guessed at. `detail` carries
-- the top two candidates and their scores, which doubles as the review queue.
CREATE TABLE IF NOT EXISTS unresolved_player (
    source           TEXT NOT NULL,
    source_player_id TEXT NOT NULL,
    reason           TEXT NOT NULL,
    detail           TEXT,
    PRIMARY KEY (source, source_player_id)
);
