-- Results, match statistics and bookmaker odds. Column availability varies
-- enormously by era, so every optional column is nullable: 1993/94 has no
-- statistics and no odds at all, 2024/25 has 120 columns.

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
    archive_key   TEXT NOT NULL,
    PRIMARY KEY (competition, season, match_date, home_team, away_team)
);

-- ------------------------------------------------------- football-data odds
-- Curated bookmaker odds, split out of src_footballdata_match. One row per
-- match, sharing its natural key. Columns absent in older eras load NULL.

CREATE TABLE IF NOT EXISTS src_footballdata_odds (
    competition   TEXT NOT NULL,
    season        TEXT NOT NULL,
    match_date    DATE NOT NULL,
    home_team     TEXT NOT NULL,
    away_team     TEXT NOT NULL,
    b365_h DOUBLE, b365_d DOUBLE, b365_a DOUBLE,
    ps_h   DOUBLE, ps_d   DOUBLE, ps_a   DOUBLE,
    max_h  DOUBLE, max_d  DOUBLE, max_a  DOUBLE,
    avg_h  DOUBLE, avg_d  DOUBLE, avg_a  DOUBLE,
    b365c_h DOUBLE, b365c_d DOUBLE, b365c_a DOUBLE,
    psc_h   DOUBLE, psc_d   DOUBLE, psc_a   DOUBLE,
    avgc_h  DOUBLE, avgc_d  DOUBLE, avgc_a  DOUBLE,
    over25_b365 DOUBLE, under25_b365 DOUBLE,
    over25_avg  DOUBLE, under25_avg  DOUBLE,
    over25_avgc DOUBLE, under25_avgc DOUBLE,
    ah_line      DOUBLE, ah_home_avg  DOUBLE, ah_away_avg  DOUBLE,
                         ah_home_ps   DOUBLE, ah_away_ps   DOUBLE,
    ahc_line     DOUBLE, ahc_home_avg DOUBLE, ahc_away_avg DOUBLE,
                         ahc_home_ps  DOUBLE, ahc_away_ps  DOUBLE,
    archive_key   TEXT NOT NULL,
    PRIMARY KEY (competition, season, match_date, home_team, away_team)
);
