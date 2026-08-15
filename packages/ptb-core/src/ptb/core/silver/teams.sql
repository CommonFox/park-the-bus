-- Conformed clubs, plus the bridge from each source's own spelling. See
-- teams.py; the alias table that feeds it is team_aliases.yaml.
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
