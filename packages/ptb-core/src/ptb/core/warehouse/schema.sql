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

INSERT OR REPLACE INTO dim_competition (competition_id, country, name, tier, gender) VALUES
    ('NWSL', 'USA', 'National Womens Soccer League', 1, 'W'),
    ('MLS',  'USA', 'Major League Soccer',           1, 'M'),
    ('USLC', 'USA', 'USL Championship',              2, 'M'),
    ('USL1', 'USA', 'USL League One',                3, 'M');

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

-- -------------------------------------------------------------------- asa

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

-- -------------------------------------------------------------------- fpl

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

-- ----------------------------------------------------------------- fotmob

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

-- ------------------------------------------------------------- draftkings

CREATE TABLE IF NOT EXISTS src_draftkings_odds (
    dk_event_id     TEXT NOT NULL,
    captured_at     TIMESTAMP NOT NULL,
    season          TEXT,
    kickoff_utc     TIMESTAMP,
    home_team       TEXT NOT NULL,
    away_team       TEXT NOT NULL,
    moneyline_home  DOUBLE,
    moneyline_draw  DOUBLE,
    moneyline_away  DOUBLE,
    over_2_5        DOUBLE,
    under_2_5       DOUBLE,
    archive_key     TEXT NOT NULL,
    PRIMARY KEY (dk_event_id, captured_at)
);
