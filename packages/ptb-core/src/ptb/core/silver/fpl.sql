-- The official FPL API's entities. src_fpl_player_gw and src_fpl_element_season
-- are shared with vaastav, which backfills the same shapes for historical
-- seasons the API no longer serves (see vaastav.py) -- hence no vaastav.sql.

CREATE TABLE IF NOT EXISTS src_fpl_team (
    season                 TEXT NOT NULL,
    team_id                INTEGER NOT NULL,
    name                   TEXT NOT NULL,
    short_name             TEXT,
    strength               INTEGER,
    strength_overall_home  INTEGER,
    strength_overall_away  INTEGER,
    strength_attack_home   INTEGER,
    strength_attack_away   INTEGER,
    strength_defence_home  INTEGER,
    strength_defence_away  INTEGER,
    archive_key            TEXT NOT NULL,
    PRIMARY KEY (season, team_id)
);

CREATE TABLE IF NOT EXISTS src_fpl_position (
    position_id  INTEGER PRIMARY KEY,
    singular_name TEXT,
    short_name    TEXT
);

CREATE TABLE IF NOT EXISTS src_fpl_event (
    season              TEXT NOT NULL,
    event_id            INTEGER NOT NULL,
    name                TEXT,
    deadline_time       TIMESTAMP,
    finished            BOOLEAN,
    is_current          BOOLEAN,
    is_next             BOOLEAN,
    average_entry_score INTEGER,
    highest_score       INTEGER,
    archive_key         TEXT NOT NULL,
    PRIMARY KEY (season, event_id)
);

CREATE TABLE IF NOT EXISTS src_fpl_element (
    season                     TEXT NOT NULL,
    element_id                 INTEGER NOT NULL,
    code                       INTEGER,
    web_name                   TEXT,
    first_name                 TEXT,
    second_name                TEXT,
    team                       INTEGER,
    element_type               INTEGER,
    now_cost                   INTEGER,
    total_points               INTEGER,
    form                       DOUBLE,
    selected_by_percent        DOUBLE,
    status                     TEXT,
    minutes                    INTEGER,
    goals_scored               INTEGER,
    assists                    INTEGER,
    clean_sheets               INTEGER,
    bonus                      INTEGER,
    bps                        INTEGER,
    expected_goals             DOUBLE,
    expected_assists           DOUBLE,
    expected_goal_involvements DOUBLE,
    birth_date                 DATE,
    opta_code                  TEXT,
    archive_key                TEXT NOT NULL,
    PRIMARY KEY (season, element_id)
);

CREATE TABLE IF NOT EXISTS src_fpl_fixture (
    season            TEXT NOT NULL,
    fixture_id        INTEGER NOT NULL,
    code              INTEGER,
    event             INTEGER,
    kickoff_time      TIMESTAMP,
    team_h            INTEGER,
    team_a            INTEGER,
    team_h_score      INTEGER,
    team_a_score      INTEGER,
    finished          BOOLEAN,
    team_h_difficulty INTEGER,
    team_a_difficulty INTEGER,
    archive_key       TEXT NOT NULL,
    PRIMARY KEY (season, fixture_id)
);

CREATE TABLE IF NOT EXISTS src_fpl_player_gw (
    season                     TEXT NOT NULL,
    source                     TEXT NOT NULL,
    element_id                 INTEGER NOT NULL,
    event                      INTEGER NOT NULL,
    fixture                    INTEGER,
    opponent_team              INTEGER,
    minutes                    INTEGER,
    total_points               INTEGER,
    goals_scored               INTEGER,
    assists                    INTEGER,
    clean_sheets               INTEGER,
    goals_conceded             INTEGER,
    own_goals                  INTEGER,
    penalties_saved            INTEGER,
    penalties_missed           INTEGER,
    yellow_cards               INTEGER,
    red_cards                  INTEGER,
    saves                      INTEGER,
    bonus                      INTEGER,
    bps                        INTEGER,
    influence                  DOUBLE,
    creativity                 DOUBLE,
    threat                     DOUBLE,
    ict_index                  DOUBLE,
    expected_goals             DOUBLE,
    expected_assists           DOUBLE,
    expected_goal_involvements DOUBLE,
    expected_goals_conceded    DOUBLE,
    value                      INTEGER,
    selected                   INTEGER,
    transfers_balance          INTEGER,
    transfers_in               INTEGER,
    transfers_out              INTEGER,
    was_home                   BOOLEAN,
    kickoff_time               TIMESTAMP,
    team_h_score               INTEGER,
    team_a_score               INTEGER,
    starts                     INTEGER,
    player_name                TEXT,
    archive_key                TEXT NOT NULL,
    PRIMARY KEY (season, source, element_id, event)
);

CREATE TABLE IF NOT EXISTS src_fpl_element_season (
    season       TEXT NOT NULL,
    element_id   INTEGER NOT NULL,
    code         INTEGER,
    first_name   TEXT,
    second_name  TEXT,
    web_name     TEXT,
    element_type INTEGER,
    team         INTEGER,
    team_code    INTEGER,
    now_cost     INTEGER,
    total_points INTEGER,
    minutes      INTEGER,
    archive_key  TEXT NOT NULL,
    PRIMARY KEY (season, element_id)
);
