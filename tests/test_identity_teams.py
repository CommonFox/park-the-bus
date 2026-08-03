import pytest

from ptb.core.identity import teams, text
from ptb.core.warehouse import db


@pytest.fixture
def con(tmp_path):
    connection = db.connect(tmp_path / "test.duckdb")
    yield connection
    connection.close()


def test_normalize_lowercases_and_collapses_space():
    assert text.normalize_name("  Manchester   United  ") == "manchester united"


def test_normalize_strips_diacritics():
    assert text.normalize_name("Atlético Madrid") == "atletico madrid"
    assert text.normalize_name("Beşiktaş") == "besiktas"


def test_normalize_strips_punctuation():
    """M'gladbach and Nott'm Forest both appear in football-data files."""
    assert text.normalize_name("M'gladbach") == "mgladbach"
    assert text.normalize_name("Nott'm Forest") == "nottm forest"
    assert text.normalize_name("St. Pauli") == "st pauli"


def test_normalize_is_idempotent():
    once = text.normalize_name("Atlético Madrid")
    assert text.normalize_name(once) == once


def test_aliases_map_variants_to_one_canonical_name():
    aliases = teams.load_aliases()
    assert teams.canonical_name("Man United", aliases) == "Manchester United"
    assert teams.canonical_name("Man Utd", aliases) == "Manchester United"
    assert teams.canonical_name("Manchester United", aliases) == "Manchester United"


def test_canonical_name_passes_through_unknown_clubs():
    assert teams.canonical_name("Fulham", {}) == "Fulham"


def test_resolve_team_creates_then_reuses_one_id(con):
    first = teams.resolve_team(con, "Man United", source="footballdata")
    second = teams.resolve_team(con, "Man United", source="footballdata")
    assert first == second
    assert con.execute("SELECT count(*) FROM dim_team").fetchone()[0] == 1


def test_variants_across_sources_resolve_to_one_team(con):
    a = teams.resolve_team(con, "Man United", source="footballdata")
    b = teams.resolve_team(con, "Man Utd", source="fotmob")
    assert a == b
    assert con.execute("SELECT count(*) FROM dim_team").fetchone()[0] == 1
    assert con.execute("SELECT count(*) FROM map_team_source").fetchone()[0] == 2


def test_map_records_each_source_spelling(con):
    teams.resolve_team(con, "Man United", source="footballdata")
    rows = con.execute(
        "SELECT source, source_team_name FROM map_team_source"
    ).fetchall()
    assert rows == [("footballdata", "Man United")]


def test_distinct_clubs_get_distinct_ids(con):
    a = teams.resolve_team(con, "Man United", source="footballdata")
    b = teams.resolve_team(con, "Man City", source="footballdata")
    assert a != b
