import pytest

from ptb.core.identity.names import name_variants, similarity


def test_variants_include_the_surname_alone():
    """Understat writes the full legal name; FPL's web_name is often just the
    surname, so the surname must be a comparable variant."""
    assert "jesus" in name_variants("Gabriel Fernando de Jesus")


def test_variants_include_the_initial_form():
    assert "b saka" in name_variants("Bukayo Saka")


def test_variants_of_a_single_word_name_is_just_itself():
    assert name_variants("Rodri") == {"rodri"}


def test_variants_of_an_empty_name_is_empty():
    assert name_variants("   ") == set()


def test_full_name_matches_surname_exactly():
    assert similarity("Gabriel Fernando de Jesus", "Jesus") == pytest.approx(1.0)


def test_initial_form_matches():
    assert similarity("Bukayo Saka", "B. Saka") == pytest.approx(1.0)


def test_diacritics_do_not_block_a_match():
    assert similarity("Martin Ødegaard", "Martin Odegaard") == pytest.approx(1.0)


def test_different_players_score_low():
    assert similarity("Bukayo Saka", "Erling Haaland") < 0.7


def test_unrelated_short_surnames_do_not_collide():
    """Jaro-Winkler is generous with short strings: comparing the bare-surname
    variants alone scores 'Ings' vs 'Mings' at 0.933, close enough to a true
    match's 1.0 to trip the ambiguity margin and flag an unrelated player as a
    false collision. Fuzzy comparison must fall back to the full names, not
    the truncated variants, once no variant matches exactly."""
    assert similarity("Danny Ings", "Tyrone Mings") < 0.80


def test_similarity_is_symmetric():
    a = similarity("Gabriel Fernando de Jesus", "Jesus")
    b = similarity("Jesus", "Gabriel Fernando de Jesus")
    assert a == pytest.approx(b)


def test_empty_name_scores_zero():
    assert similarity("", "Bukayo Saka") == 0.0


from ptb.core.identity.names import score_candidate


def test_score_with_only_a_name_is_the_name_similarity():
    assert score_candidate(
        name_similarity=0.9, team_agrees=None, birth_date_agrees=None,
    ) == pytest.approx(0.9)


def test_a_perfect_match_scores_one():
    assert score_candidate(
        name_similarity=1.0, team_agrees=True, birth_date_agrees=True,
    ) == pytest.approx(1.0)


def test_a_missing_birth_date_does_not_penalise():
    """birth_date is absent for 60% of players. Scoring an absent feature as
    zero would sink most true matches below the threshold."""
    with_dob = score_candidate(
        name_similarity=1.0, team_agrees=True, birth_date_agrees=True)
    without_dob = score_candidate(
        name_similarity=1.0, team_agrees=True, birth_date_agrees=None)
    assert without_dob == pytest.approx(with_dob)


def test_a_disagreeing_birth_date_does_penalise():
    """Absent is not the same as contradicted."""
    assert score_candidate(
        name_similarity=1.0, team_agrees=True, birth_date_agrees=False,
    ) < 0.9


def test_a_wrong_team_pulls_a_perfect_name_down():
    assert score_candidate(
        name_similarity=1.0, team_agrees=False, birth_date_agrees=None,
    ) == pytest.approx(0.60 / 0.85)
