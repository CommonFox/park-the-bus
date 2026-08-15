-- American Soccer Analysis: reference resources (teams, players), games, and
-- the per-player-season stat tables (goals-added, xgoals, xpass).

CREATE TABLE IF NOT EXISTS src_asa_team (
    league            TEXT NOT NULL,
    team_id           TEXT NOT NULL,
    team_name         TEXT NOT NULL,
    team_short_name   TEXT,
    team_abbreviation TEXT,
    PRIMARY KEY (league, team_id)
);

CREATE TABLE IF NOT EXISTS src_asa_player (
    league                   TEXT NOT NULL,
    player_id                TEXT NOT NULL,
    season                   TEXT NOT NULL,
    player_name              TEXT,
    birth_date               DATE,
    nationality              TEXT,
    primary_general_position TEXT,
    PRIMARY KEY (league, player_id, season)
);

CREATE TABLE IF NOT EXISTS src_asa_game (
    game_id      TEXT PRIMARY KEY,
    league       TEXT NOT NULL,
    season       TEXT NOT NULL,
    kickoff_utc  TIMESTAMP,
    home_team_id TEXT NOT NULL,
    away_team_id TEXT NOT NULL,
    home_score   INTEGER,
    away_score   INTEGER,
    matchday     INTEGER,
    status       TEXT,
    archive_key  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS src_asa_game_xgoals (
    game_id            TEXT PRIMARY KEY,
    league             TEXT NOT NULL,
    season             TEXT NOT NULL,
    home_team_id       TEXT,
    away_team_id       TEXT,
    home_goals         INTEGER,
    away_goals         INTEGER,
    home_team_xgoals   DOUBLE,
    away_team_xgoals   DOUBLE,
    home_player_xgoals DOUBLE,
    away_player_xgoals DOUBLE,
    home_xpoints       DOUBLE,
    away_xpoints       DOUBLE,
    archive_key        TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS src_asa_player_goals_added (
    league                TEXT NOT NULL,
    season                TEXT NOT NULL,
    player_id             TEXT NOT NULL,
    team_id               TEXT,
    general_position      TEXT,
    minutes_played        INTEGER,
    action_type           TEXT NOT NULL,
    goals_added_raw       DOUBLE,
    goals_added_above_avg DOUBLE,
    count_actions         INTEGER,
    archive_key           TEXT NOT NULL,
    PRIMARY KEY (league, season, player_id, action_type)
);

CREATE TABLE IF NOT EXISTS src_asa_player_xgoals (
    league                     TEXT NOT NULL,
    season                     TEXT NOT NULL,
    player_id                  TEXT NOT NULL,
    team_id                    TEXT,
    general_position           TEXT,
    minutes_played             INTEGER,
    shots                      INTEGER,
    shots_on_target            INTEGER,
    goals                      INTEGER,
    xgoals                     DOUBLE,
    xplace                     DOUBLE,
    key_passes                 INTEGER,
    primary_assists            INTEGER,
    xassists                   DOUBLE,
    goals_plus_primary_assists INTEGER,
    xgoals_plus_xassists       DOUBLE,
    points_added               DOUBLE,
    xpoints_added              DOUBLE,
    archive_key                TEXT NOT NULL,
    PRIMARY KEY (league, season, player_id)
);

CREATE TABLE IF NOT EXISTS src_asa_player_xpass (
    league                              TEXT NOT NULL,
    season                              TEXT NOT NULL,
    player_id                           TEXT NOT NULL,
    team_id                             TEXT,
    general_position                    TEXT,
    minutes_played                      INTEGER,
    attempted_passes                    INTEGER,
    pass_completion_percentage          DOUBLE,
    xpass_completion_percentage         DOUBLE,
    passes_completed_over_expected      DOUBLE,
    passes_completed_over_expected_p100 DOUBLE,
    avg_distance_yds                    DOUBLE,
    avg_vertical_distance_yds           DOUBLE,
    share_team_touches                  DOUBLE,
    count_games                         INTEGER,
    archive_key                         TEXT NOT NULL,
    PRIMARY KEY (league, season, player_id)
);
