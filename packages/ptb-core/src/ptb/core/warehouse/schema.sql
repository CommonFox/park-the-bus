-- Bookkeeping: which archive keys have been folded into this warehouse.
-- Makes every loader idempotent and lets `rebuild` skip finished work.
CREATE TABLE IF NOT EXISTS meta_archive_loaded (
    archive_key TEXT PRIMARY KEY,
    source      TEXT NOT NULL,
    loaded_at   TIMESTAMP NOT NULL
);

-- ---------------------------------------------------------------- dimensions

CREATE TABLE IF NOT EXISTS dim_competition (
    competition_id TEXT PRIMARY KEY,
    country        TEXT NOT NULL,
    name           TEXT NOT NULL,
    tier           INTEGER,
    gender         TEXT NOT NULL DEFAULT 'M'
);

INSERT OR REPLACE INTO dim_competition (competition_id, country, name, tier, gender) VALUES
    ('E0',  'England', 'Premier League',   1, 'M'),
    ('E1',  'England', 'Championship',     2, 'M'),
    ('SP1', 'Spain',   'La Liga',          1, 'M'),
    ('SP2', 'Spain',   'Segunda Division', 2, 'M'),
    ('I1',  'Italy',   'Serie A',          1, 'M'),
    ('I2',  'Italy',   'Serie B',          2, 'M'),
    ('D1',  'Germany', 'Bundesliga',       1, 'M'),
    ('D2',  'Germany', '2. Bundesliga',    2, 'M'),
    ('F1',  'France',  'Ligue 1',          1, 'M'),
    ('F2',  'France',  'Ligue 2',          2, 'M');

-- ------------------------------------------------------------- source-faithful
-- One table per source per entity. Never merged, never reconciled: where two
-- sources disagree, both values survive here and a conformed view picks one.

CREATE TABLE IF NOT EXISTS src_footballdata_match (
    competition   TEXT NOT NULL,
    season        TEXT NOT NULL,
    match_date    DATE NOT NULL,
    kickoff_time  TIME,
    home_team     TEXT NOT NULL,
    away_team     TEXT NOT NULL,
    home_goals    INTEGER,
    away_goals    INTEGER,
    result        TEXT,
    ht_home_goals INTEGER,
    ht_away_goals INTEGER,
    ht_result     TEXT,
    home_shots    INTEGER,
    away_shots    INTEGER,
    home_sot      INTEGER,
    away_sot      INTEGER,
    home_fouls    INTEGER,
    away_fouls    INTEGER,
    home_corners  INTEGER,
    away_corners  INTEGER,
    home_yellows  INTEGER,
    away_yellows  INTEGER,
    home_reds     INTEGER,
    away_reds     INTEGER,
    referee       TEXT,
    odds_home     DOUBLE,
    odds_draw     DOUBLE,
    odds_away     DOUBLE,
    archive_key   TEXT NOT NULL,
    PRIMARY KEY (competition, season, match_date, home_team, away_team)
);

-- ----------------------------------------------------------------- identity

CREATE SEQUENCE IF NOT EXISTS seq_team_id START 1;

CREATE TABLE IF NOT EXISTS dim_team (
    team_id         BIGINT PRIMARY KEY,
    canonical_name  TEXT NOT NULL,
    normalized_name TEXT NOT NULL UNIQUE,
    country         TEXT,
    gender          TEXT NOT NULL DEFAULT 'M'
);

CREATE TABLE IF NOT EXISTS map_team_source (
    team_id          BIGINT NOT NULL,
    source           TEXT NOT NULL,
    source_team_name TEXT NOT NULL,
    PRIMARY KEY (source, source_team_name)
);

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

-- --------------------------------------------------------------- understat

CREATE TABLE IF NOT EXISTS src_understat_match (
    understat_match_id TEXT PRIMARY KEY,
    competition   TEXT NOT NULL,
    season        TEXT NOT NULL,
    kickoff       TIMESTAMP,
    home_team     TEXT NOT NULL,
    away_team     TEXT NOT NULL,
    home_goals    INTEGER,
    away_goals    INTEGER,
    home_xg       DOUBLE,
    away_xg       DOUBLE,
    forecast_w    DOUBLE,
    forecast_d    DOUBLE,
    forecast_l    DOUBLE,
    archive_key   TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS src_understat_shot (
    understat_shot_id  TEXT PRIMARY KEY,
    understat_match_id TEXT NOT NULL,
    minute        INTEGER,
    player        TEXT,
    player_id     TEXT,
    team          TEXT,
    home_away     TEXT,
    xg            DOUBLE,
    result        TEXT,
    situation     TEXT,
    shot_type     TEXT,
    x             DOUBLE,
    y             DOUBLE,
    assist_player TEXT,
    last_action   TEXT,
    archive_key   TEXT NOT NULL
);
