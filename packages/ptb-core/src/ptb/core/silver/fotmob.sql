-- FotMob season stat-table leaderboards, one row per player/team per stat.

CREATE TABLE IF NOT EXISTS src_fotmob_player_stat (
    league_id        BIGINT NOT NULL,
    league_name      TEXT,
    season           TEXT NOT NULL,
    season_id        BIGINT,
    stat_name        TEXT NOT NULL,
    stat_title       TEXT,
    stat_category    TEXT,
    fotmob_player_id BIGINT NOT NULL,
    fotmob_team_id   BIGINT,
    player_name      TEXT,
    position_code    INTEGER,
    value            DOUBLE,
    substat_value    DOUBLE,
    rank             INTEGER,
    archive_key      TEXT NOT NULL,
    PRIMARY KEY (league_id, season, stat_name, fotmob_player_id)
);

CREATE TABLE IF NOT EXISTS src_fotmob_team_stat (
    league_id      BIGINT NOT NULL,
    league_name    TEXT,
    season         TEXT NOT NULL,
    season_id      BIGINT,
    stat_name      TEXT NOT NULL,
    stat_title     TEXT,
    stat_category  TEXT,
    fotmob_team_id BIGINT NOT NULL,
    team_name      TEXT,
    value          DOUBLE,
    substat_value  DOUBLE,
    rank           INTEGER,
    archive_key    TEXT NOT NULL,
    PRIMARY KEY (league_id, season, stat_name, fotmob_team_id)
);
