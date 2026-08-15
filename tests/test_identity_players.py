import datetime as dt

import pytest

from ptb.core import warehouse
from ptb.core.silver import fotmob, players, understat


@pytest.fixture
def con(tmp_path):
    connection = warehouse.connect(tmp_path / "test.duckdb")
    yield connection
    connection.close()


def _fpl_team(con, season, team_id, name):
    con.execute(
        "INSERT OR REPLACE INTO src_fpl_team (season, team_id, name, archive_key) "
        "VALUES (?, ?, ?, 'k')", [season, team_id, name])


def _fpl_element(con, season, element_id, code, first, second, team,
                 birth_date=None, opta_code=None):
    con.execute(
        "INSERT OR REPLACE INTO src_fpl_element (season, element_id, code, web_name, "
        "first_name, second_name, team, element_type, birth_date, opta_code, archive_key) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, 3, ?, ?, 'k')",
        [season, element_id, code, second, first, second, team, birth_date, opta_code])


def test_spine_creates_one_player_per_fpl_code(con):
    _fpl_team(con, "2024/25", 1, "Arsenal")
    _fpl_element(con, "2024/25", 11, 223094, "Bukayo", "Saka", 1)
    assert players.build_fpl_spine(con) == 1
    assert con.execute("SELECT count(*) FROM dim_player").fetchone()[0] == 1


def test_one_player_across_two_seasons_is_one_row(con):
    """element_id is reassigned between seasons; code is not. Keying on
    element_id would make one footballer into two players."""
    _fpl_team(con, "2023/24", 1, "Arsenal")
    _fpl_team(con, "2024/25", 1, "Arsenal")
    _fpl_element(con, "2023/24", 7, 223094, "Bukayo", "Saka", 1)
    _fpl_element(con, "2024/25", 11, 223094, "Bukayo", "Saka", 1)

    assert players.build_fpl_spine(con) == 1
    row = con.execute(
        "SELECT canonical_name, fpl_code, first_seen_season, last_seen_season "
        "FROM dim_player").fetchone()
    assert row == ("Bukayo Saka", 223094, "2023/24", "2024/25")


def test_spine_maps_the_fpl_code_not_the_element_id(con):
    _fpl_team(con, "2024/25", 1, "Arsenal")
    _fpl_element(con, "2024/25", 11, 223094, "Bukayo", "Saka", 1)
    players.build_fpl_spine(con)
    row = con.execute(
        "SELECT source, source_player_id, method, confidence FROM map_player_source"
    ).fetchone()
    assert row == ("fpl", "223094", "fpl_code", 1.0)


def test_spine_carries_birth_date_and_opta_code(con):
    """Both arrive only in recent seasons, so the latest non-null wins rather
    than the latest season's value."""
    _fpl_team(con, "2023/24", 1, "Arsenal")
    _fpl_team(con, "2024/25", 1, "Arsenal")
    _fpl_element(con, "2023/24", 7, 223094, "Bukayo", "Saka", 1)
    _fpl_element(con, "2024/25", 11, 223094, "Bukayo", "Saka", 1,
                 birth_date=dt.date(2001, 9, 5), opta_code="p223094")
    players.build_fpl_spine(con)
    row = con.execute("SELECT birth_date, opta_code FROM dim_player").fetchone()
    assert row == (dt.date(2001, 9, 5), "p223094")


def _fpl_element_season(con, season, element_id, code, first, second, team=None):
    con.execute(
        "INSERT OR REPLACE INTO src_fpl_element_season (season, element_id, code, "
        "first_name, second_name, web_name, element_type, team, archive_key) "
        "VALUES (?, ?, ?, ?, ?, ?, 3, ?, 'k')",
        [season, element_id, code, first, second, second, team])


def test_spine_includes_a_player_seen_only_in_vaastav_history(con):
    """src_fpl_element only ever holds the live bootstrap's current season. A
    player who left the league before that season -- so never appears there --
    still needs a spine row, or every historical Understat/FotMob appearance of
    theirs is forced into 'created' for lack of any candidate to match against.
    """
    _fpl_element_season(con, "2019/20", 7, 98765, "Wayne", "Rooney")
    assert players.build_fpl_spine(con) == 1
    row = con.execute("SELECT canonical_name, fpl_code FROM dim_player").fetchone()
    assert row == ("Wayne Rooney", 98765)


def test_build_fpl_spine_is_idempotent(con):
    _fpl_team(con, "2024/25", 1, "Arsenal")
    _fpl_element(con, "2024/25", 11, 223094, "Bukayo", "Saka", 1)

    assert players.build_fpl_spine(con) == 1
    assert players.build_fpl_spine(con) == 0
    assert con.execute("SELECT count(*) FROM dim_player").fetchone()[0] == 1
    assert con.execute("SELECT count(*) FROM map_player_source").fetchone()[0] == 1


def _candidate(player_id=1, name="Bukayo Saka", birth_date=None,
               opta_code=None, team_name="Arsenal"):
    return players.Candidate(player_id, name, birth_date, opta_code, team_name)


def test_opta_code_wins_outright():
    result = players.match_player(
        [_candidate(opta_code="p223094"), _candidate(player_id=2, name="Someone Else")],
        name="Totally Different", team_name=None,
        birth_date=None, opta_code="p223094")
    assert (result.player_id, result.method, result.confidence) == (1, "opta_code", 1.00)


def test_name_plus_birth_date_matches():
    result = players.match_player(
        [_candidate(birth_date=dt.date(2001, 9, 5))],
        name="Bukayo Saka", team_name=None,
        birth_date=dt.date(2001, 9, 5), opta_code=None)
    assert (result.method, result.confidence) == ("name_dob", 0.99)


def test_name_plus_team_matches():
    result = players.match_player(
        [_candidate()], name="Bukayo Saka", team_name="Arsenal",
        birth_date=None, opta_code=None)
    assert (result.method, result.confidence) == ("name_team_season", 0.95)


def test_two_players_of_the_same_name_at_the_same_club_are_ambiguous():
    """The homonym case. Two Danny Wards at one club cannot be separated by
    name and team, so the tier must not fire and the fallback must refuse."""
    result = players.match_player(
        [_candidate(player_id=1, name="Danny Ward"),
         _candidate(player_id=2, name="Danny Ward")],
        name="Danny Ward", team_name="Arsenal",
        birth_date=None, opta_code=None)
    assert result.player_id is None
    assert result.reason == players.AMBIGUOUS


def test_two_players_of_the_same_name_split_by_birth_date():
    """Same names, same club, but a birth date separates them cleanly."""
    result = players.match_player(
        [_candidate(player_id=1, name="Danny Ward", birth_date=dt.date(1993, 6, 22)),
         _candidate(player_id=2, name="Danny Ward", birth_date=dt.date(1990, 12, 1))],
        name="Danny Ward", team_name="Arsenal",
        birth_date=dt.date(1990, 12, 1), opta_code=None)
    assert result.player_id == 2
    assert result.method == "name_dob"


def _understat_match(con, match_id, season, competition="E0"):
    con.execute(
        "INSERT OR REPLACE INTO src_understat_match (understat_match_id, competition, "
        "season, home_team, away_team, archive_key) VALUES (?, ?, ?, 'Arsenal', 'Wolves', 'k')",
        [match_id, competition, season])


def _understat_shot(con, shot_id, match_id, player_id, player, team):
    con.execute(
        "INSERT OR REPLACE INTO src_understat_shot (understat_shot_id, understat_match_id, "
        "minute, player, player_id, team, home_away, archive_key) "
        "VALUES (?, ?, 10, ?, ?, ?, 'h', 'k')",
        [shot_id, match_id, player, player_id, team])


def _understat_player(con, understat_player_id, name, team, season, competition="E0"):
    con.execute(
        "INSERT OR REPLACE INTO src_understat_player (understat_player_id, competition, "
        "season, player_name, team_name, archive_key) VALUES (?, ?, ?, ?, ?, 'k')",
        [understat_player_id, competition, season, name, team])


def test_understat_player_matches_the_fpl_spine(con):
    _fpl_team(con, "2024/25", 1, "Arsenal")
    _fpl_element(con, "2024/25", 11, 223094, "Bukayo", "Saka", 1)
    players.build_fpl_spine(con)

    _understat_match(con, "1001", "2024/25")
    _understat_shot(con, "s1", "1001", "647", "Bukayo Saka", "Arsenal")

    assert understat.resolve_players(con) == 1
    row = con.execute(
        "SELECT player_id, method FROM map_player_source "
        "WHERE source = 'understat' AND source_player_id = '647'").fetchone()
    fpl_player_id = con.execute(
        "SELECT player_id FROM dim_player WHERE fpl_code = 223094").fetchone()[0]
    assert row[0] == fpl_player_id
    assert con.execute("SELECT count(*) FROM dim_player").fetchone()[0] == 1


def test_a_shortened_fpl_name_still_matches(con):
    """FPL stores 'Gabriel Jesus'; Understat stores the full legal name."""
    _fpl_team(con, "2024/25", 1, "Arsenal")
    _fpl_element(con, "2024/25", 12, 205651, "Gabriel", "Jesus", 1)
    players.build_fpl_spine(con)

    _understat_match(con, "1001", "2024/25")
    _understat_shot(con, "s1", "1001", "700", "Gabriel Fernando de Jesus", "Arsenal")

    assert understat.resolve_players(con) == 1


def test_a_reversed_fpl_name_still_matches(con):
    """FPL's first_name/second_name are reversed relative to Understat for
    some East Asian players, e.g. the real Wataru Endo: FPL's canonical name
    is 'Endo Wataru', Understat's is 'Wataru Endo'. Without the swapped-order
    variant this silently creates a duplicate dim_player instead of matching
    the real spine row."""
    _fpl_team(con, "2024/25", 1, "Liverpool")
    _fpl_element(con, "2024/25", 15, 400400, "Endo", "Wataru", 1)
    players.build_fpl_spine(con)

    _understat_match(con, "1001", "2024/25")
    _understat_shot(con, "s1", "1001", "800", "Wataru Endo", "Liverpool")

    assert understat.resolve_players(con) == 1
    fpl_player_id = con.execute(
        "SELECT player_id FROM dim_player WHERE fpl_code = 400400").fetchone()[0]
    row = con.execute(
        "SELECT player_id FROM map_player_source "
        "WHERE source = 'understat' AND source_player_id = '800'").fetchone()
    assert row[0] == fpl_player_id
    assert con.execute("SELECT count(*) FROM dim_player").fetchone()[0] == 1


def test_a_player_absent_from_fpl_gets_a_new_row(con):
    """A La Liga player has no FPL code. Creating the row is how the dimension
    grows beyond the Premier League -- it is not a resolution failure."""
    _understat_match(con, "2001", "2024/25", competition="SP1")
    _understat_shot(con, "s1", "2001", "900", "Robert Lewandowski", "Barcelona")

    assert understat.resolve_players(con) == 0
    row = con.execute(
        "SELECT p.canonical_name, m.method FROM dim_player p "
        "JOIN map_player_source m ON m.player_id = p.player_id "
        "WHERE m.source = 'understat'").fetchone()
    assert row == ("Robert Lewandowski", "created")


def test_an_ambiguous_understat_player_is_recorded_not_guessed(con):
    _fpl_team(con, "2024/25", 1, "Arsenal")
    _fpl_element(con, "2024/25", 20, 111111, "Danny", "Ward", 1)
    _fpl_element(con, "2024/25", 21, 222222, "Danny", "Ward", 1)
    players.build_fpl_spine(con)

    _understat_match(con, "1001", "2024/25")
    _understat_shot(con, "s1", "1001", "800", "Danny Ward", "Arsenal")

    assert understat.resolve_players(con) == 0
    assert con.execute(
        "SELECT reason FROM unresolved_player WHERE source = 'understat'"
    ).fetchone()[0] == players.AMBIGUOUS
    assert con.execute(
        "SELECT count(*) FROM map_player_source WHERE source = 'understat'"
    ).fetchone()[0] == 0


def test_a_player_in_two_seasons_maps_once(con):
    _fpl_team(con, "2023/24", 1, "Arsenal")
    _fpl_team(con, "2024/25", 1, "Arsenal")
    _fpl_element(con, "2023/24", 7, 223094, "Bukayo", "Saka", 1)
    _fpl_element(con, "2024/25", 11, 223094, "Bukayo", "Saka", 1)
    players.build_fpl_spine(con)

    _understat_match(con, "1001", "2023/24")
    _understat_match(con, "1002", "2024/25")
    _understat_shot(con, "s1", "1001", "647", "Bukayo Saka", "Arsenal")
    _understat_shot(con, "s2", "1002", "647", "Bukayo Saka", "Arsenal")

    understat.resolve_players(con)
    assert con.execute(
        "SELECT count(*) FROM map_player_source WHERE source = 'understat'"
    ).fetchone()[0] == 1


def test_a_historical_season_matches_via_vaastav_even_with_no_team_name(con):
    """src_fpl_team is likewise current-season-only, so a historical candidate
    carries no team name -- name (and birth_date, where the spine has it) is
    all there is to match on for a season src_fpl_element never covered."""
    _fpl_element_season(con, "2019/20", 7, 98765, "Wayne", "Rooney")
    players.build_fpl_spine(con)

    _understat_match(con, "5001", "2019/20")
    _understat_shot(con, "s1", "5001", "555", "Wayne Rooney", "Derby")

    assert understat.resolve_players(con) == 1
    fpl_player_id = con.execute(
        "SELECT player_id FROM dim_player WHERE fpl_code = 98765").fetchone()[0]
    row = con.execute(
        "SELECT player_id, method FROM map_player_source "
        "WHERE source = 'understat' AND source_player_id = '555'").fetchone()
    assert row == (fpl_player_id, "scored")


def test_a_player_who_transferred_into_england_still_matches(con):
    """Understat history predates an English candidate existing at all for a
    player who transferred in from another Big-5 league. Deciding on the
    earliest appearance alone -- Ligue 1, with no candidate pool since
    candidates only ever exist for E0 -- would permanently foreclose a real
    match that only becomes possible once their Premier League appearance
    (and FPL candidate) exists. Every block must be tried, not just the
    first."""
    _understat_match(con, "3001", "2019/20", competition="F1")
    _understat_shot(con, "s1", "3001", "999", "Jean Test", "Marseille")

    _fpl_team(con, "2023/24", 1, "Arsenal")
    _fpl_element(con, "2023/24", 30, 555555, "Jean", "Test", 1)
    players.build_fpl_spine(con)

    _understat_match(con, "3002", "2023/24", competition="E0")
    _understat_shot(con, "s2", "3002", "999", "Jean Test", "Arsenal")

    assert understat.resolve_players(con) == 1
    fpl_player_id = con.execute(
        "SELECT player_id FROM dim_player WHERE fpl_code = 555555").fetchone()[0]
    row = con.execute(
        "SELECT player_id, method FROM map_player_source "
        "WHERE source = 'understat' AND source_player_id = '999'").fetchone()
    assert row == (fpl_player_id, "name_team_season")


def test_a_zero_shot_understat_player_still_matches_the_fpl_spine(con):
    """src_understat_shot alone never surfaces a player with zero shots -- most
    goalkeepers and many fringe subs. src_understat_player is Understat's own
    league-season roster and must be enough on its own to bring such a player
    into identity resolution."""
    _fpl_team(con, "2024/25", 1, "Fulham")
    _fpl_element(con, "2024/25", 40, 300300, "Bernd", "Leno", 1)
    players.build_fpl_spine(con)

    _understat_player(con, "181", "Bernd Leno", "Fulham", "2024/25")

    assert understat.resolve_players(con) == 1
    fpl_player_id = con.execute(
        "SELECT player_id FROM dim_player WHERE fpl_code = 300300").fetchone()[0]
    row = con.execute(
        "SELECT player_id, method FROM map_player_source "
        "WHERE source = 'understat' AND source_player_id = '181'").fetchone()
    assert row == (fpl_player_id, "name_team_season")


def test_shot_derived_team_wins_over_the_roster_row_for_the_same_block(con):
    """A player can move club mid-season; the modal team across their shots is
    the one that should decide the match, not the roster row's season-aggregate
    club. If the roster team won instead, 'Some Other Club' vs FPL's 'Arsenal'
    would sink the score below threshold and this player would go unmatched."""
    _fpl_team(con, "2024/25", 1, "Arsenal")
    _fpl_element(con, "2024/25", 11, 223094, "Bukayo", "Saka", 1)
    players.build_fpl_spine(con)

    _understat_match(con, "1001", "2024/25")
    _understat_shot(con, "s1", "1001", "647", "Bukayo Saka", "Arsenal")
    _understat_player(con, "647", "Bukayo Saka", "Some Other Club", "2024/25")

    assert understat.resolve_players(con) == 1
    fpl_player_id = con.execute(
        "SELECT player_id FROM dim_player WHERE fpl_code = 223094").fetchone()[0]
    row = con.execute(
        "SELECT player_id, method FROM map_player_source "
        "WHERE source = 'understat' AND source_player_id = '647'").fetchone()
    assert row == (fpl_player_id, "name_team_season")
    assert con.execute(
        "SELECT count(*) FROM map_player_source WHERE source = 'understat'"
    ).fetchone()[0] == 1


def test_resolve_understat_players_is_idempotent_for_a_created_player(con):
    """A player with no FPL candidate (non-E0) takes the 'created' branch.

    Rerunning must not mint a second dim_player row for the same source
    player -- the same check-before-create contract as build_fpl_spine, this
    time covering the created outcome rather than only the matched one.
    """
    _understat_match(con, "2001", "2024/25", competition="SP1")
    _understat_shot(con, "s1", "2001", "900", "Robert Lewandowski", "Barcelona")

    assert understat.resolve_players(con) == 0
    assert understat.resolve_players(con) == 0
    assert con.execute("SELECT count(*) FROM dim_player").fetchone()[0] == 1
    assert con.execute(
        "SELECT count(*) FROM map_player_source WHERE source = 'understat'"
    ).fetchone()[0] == 1


def _fotmob_player(con, player_id, name, team_id, season="2024/25",
                   league_id=47, stat="expected_goals"):
    con.execute(
        "INSERT OR REPLACE INTO src_fotmob_player_stat (league_id, season, stat_name, "
        "fotmob_player_id, fotmob_team_id, player_name, value, archive_key) "
        "VALUES (?, ?, ?, ?, ?, ?, 1.0, 'k')",
        [league_id, season, stat, player_id, team_id, name])


def _fotmob_team(con, team_id, name, season="2024/25", league_id=47):
    con.execute(
        "INSERT OR REPLACE INTO src_fotmob_team_stat (league_id, season, stat_name, "
        "fotmob_team_id, team_name, value, archive_key) "
        "VALUES (?, ?, 'expected_goals', ?, ?, 1.0, 'k')",
        [league_id, season, team_id, name])


def test_fotmob_player_matches_the_fpl_spine(con):
    _fpl_team(con, "2024/25", 1, "Arsenal")
    _fpl_element(con, "2024/25", 11, 223094, "Bukayo", "Saka", 1)
    players.build_fpl_spine(con)

    _fotmob_team(con, 9825, "Arsenal")
    _fotmob_player(con, 737066, "Bukayo Saka", 9825)

    assert fotmob.resolve_players(con) == 1
    fpl_player_id = con.execute(
        "SELECT player_id FROM dim_player WHERE fpl_code = 223094").fetchone()[0]
    assert con.execute(
        "SELECT player_id FROM map_player_source WHERE source = 'fotmob'"
    ).fetchone()[0] == fpl_player_id


def test_fotmob_player_appearing_in_many_stat_boards_maps_once(con):
    """One player appears on every leaderboard for their league and season."""
    _fpl_team(con, "2024/25", 1, "Arsenal")
    _fpl_element(con, "2024/25", 11, 223094, "Bukayo", "Saka", 1)
    players.build_fpl_spine(con)

    _fotmob_team(con, 9825, "Arsenal")
    _fotmob_player(con, 737066, "Bukayo Saka", 9825, stat="expected_goals")
    _fotmob_player(con, 737066, "Bukayo Saka", 9825, stat="goals")
    _fotmob_player(con, 737066, "Bukayo Saka", 9825, stat="assists")

    assert fotmob.resolve_players(con) == 1
    assert con.execute(
        "SELECT count(*) FROM map_player_source WHERE source = 'fotmob'"
    ).fetchone()[0] == 1


def test_championship_players_are_resolved_separately(con):
    """League 48 is the Championship. Its players have no Premier League FPL
    candidate, so they enter the dimension as new rows."""
    _fotmob_team(con, 8678, "Leeds", league_id=48)
    _fotmob_player(con, 999001, "Some Championship Player", 8678, league_id=48)

    assert fotmob.resolve_players(con) == 0
    assert con.execute(
        "SELECT count(*) FROM map_player_source WHERE source = 'fotmob'"
    ).fetchone()[0] == 1


def test_resolve_players_runs_every_source(con):
    _fpl_team(con, "2024/25", 1, "Arsenal")
    _fpl_element(con, "2024/25", 11, 223094, "Bukayo", "Saka", 1)
    _understat_match(con, "1001", "2024/25")
    _understat_shot(con, "s1", "1001", "647", "Bukayo Saka", "Arsenal")
    _fotmob_team(con, 9825, "Arsenal")
    _fotmob_player(con, 737066, "Bukayo Saka", 9825)

    players.resolve_players(con)

    assert con.execute("SELECT count(*) FROM dim_player").fetchone()[0] == 1
    sources = {r[0] for r in con.execute(
        "SELECT DISTINCT source FROM map_player_source").fetchall()}
    assert sources == {"fpl", "understat", "fotmob"}


def test_resolve_players_is_idempotent(con):
    """Two rebuilds must produce byte-identical tables, including player_ids --
    that is what makes the warehouse disposable."""
    _fpl_team(con, "2024/25", 1, "Arsenal")
    _fpl_element(con, "2024/25", 11, 223094, "Bukayo", "Saka", 1)
    _understat_match(con, "1001", "2024/25")
    _understat_shot(con, "s1", "1001", "647", "Bukayo Saka", "Arsenal")

    players.resolve_players(con)
    first = con.execute(
        "SELECT * FROM dim_player ORDER BY player_id").fetchall()
    first_map = con.execute(
        "SELECT * FROM map_player_source ORDER BY source, source_player_id").fetchall()

    players.resolve_players(con)
    assert con.execute(
        "SELECT * FROM dim_player ORDER BY player_id").fetchall() == first
    assert con.execute(
        "SELECT * FROM map_player_source ORDER BY source, source_player_id"
    ).fetchall() == first_map


def test_an_override_forces_a_mapping(con, tmp_path):
    _fpl_team(con, "2024/25", 1, "Arsenal")
    _fpl_element(con, "2024/25", 11, 223094, "Bukayo", "Saka", 1)
    players.build_fpl_spine(con)

    overrides = tmp_path / "overrides.yaml"
    overrides.write_text("understat:\n  '12345': 223094\n", encoding="utf-8")

    assert players.apply_overrides(con, overrides) == 1
    row = con.execute(
        "SELECT method, confidence FROM map_player_source "
        "WHERE source = 'understat' AND source_player_id = '12345'").fetchone()
    assert row == ("override", 1.0)


def test_an_override_can_force_a_non_match(con, tmp_path):
    """A null override is the only way to undo a confidently wrong scored
    match without loosening the threshold for everyone."""
    _fpl_team(con, "2024/25", 1, "Arsenal")
    _fpl_element(con, "2024/25", 11, 223094, "Bukayo", "Saka", 1)
    players.build_fpl_spine(con)
    _understat_match(con, "1001", "2024/25")
    _understat_shot(con, "s1", "1001", "647", "Bukayo Saka", "Arsenal")
    understat.resolve_players(con)

    overrides = tmp_path / "overrides.yaml"
    overrides.write_text("understat:\n  '647': null\n", encoding="utf-8")
    players.apply_overrides(con, overrides)

    assert con.execute(
        "SELECT count(*) FROM map_player_source WHERE source = 'understat'"
    ).fetchone()[0] == 0


def test_rebuild_resolves_players_after_every_source_loads(con, tmp_path):
    """Player matching is cross-source, so it cannot run inside the per-source
    branches the way match resolution does -- the spine must already exist."""
    from ptb.core import archive
    from ptb.core import warehouse

    payload = {
        "source": "fpl", "endpoint": "bootstrap", "season": "2024/25",
        "data": {
            "teams": [{"id": 1, "name": "Arsenal", "short_name": "ARS"}],
            "element_types": [], "events": [],
            "elements": [{"id": 11, "code": 223094, "web_name": "Saka",
                          "first_name": "Bukayo", "second_name": "Saka",
                          "team": 1, "element_type": 3}],
        },
    }
    archive.write("fpl", "bootstrap", payload)

    warehouse.rebuild(con)
    assert con.execute("SELECT count(*) FROM dim_player").fetchone()[0] == 1
