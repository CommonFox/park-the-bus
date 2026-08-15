-- DraftKings forward odds, snapshotted: one row per event per capture.

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
