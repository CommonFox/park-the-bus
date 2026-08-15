-- Match- and shot-level xG for the Big 5, plus Understat's own league-season
-- roster.

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

-- Understat's own league-season roster, from the same getLeagueData payload as
-- src_understat_match. It carries every squad player who appeared at all that
-- season, including ones with zero shots (most goalkeepers, fringe subs) --
-- src_understat_shot alone only ever surfaces shot-takers, which silently
-- excludes those players from player identity resolution entirely.
CREATE TABLE IF NOT EXISTS src_understat_player (
    understat_player_id TEXT NOT NULL,
    competition          TEXT NOT NULL,
    season               TEXT NOT NULL,
    player_name          TEXT,
    team_name            TEXT,
    position             TEXT,
    games                INTEGER,
    minutes              INTEGER,
    goals                INTEGER,
    non_penalty_goals    INTEGER,
    assists              INTEGER,
    shots                INTEGER,
    key_passes           INTEGER,
    xg                   DOUBLE,
    npxg                 DOUBLE,
    xa                   DOUBLE,
    xg_buildup           DOUBLE,
    xg_chain             DOUBLE,
    yellow_cards         INTEGER,
    red_cards            INTEGER,
    archive_key          TEXT NOT NULL,
    PRIMARY KEY (understat_player_id, competition, season)
);
