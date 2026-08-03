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
