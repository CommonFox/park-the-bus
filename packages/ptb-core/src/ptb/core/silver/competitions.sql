-- The one dimension that is seeded rather than resolved: a fixed, hand-kept
-- list, since competition codes are this warehouse's own vocabulary (E0, NWSL)
-- and no source publishes them.
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
