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


def test_similarity_is_symmetric():
    a = similarity("Gabriel Fernando de Jesus", "Jesus")
    b = similarity("Jesus", "Gabriel Fernando de Jesus")
    assert a == pytest.approx(b)


def test_empty_name_scores_zero():
    assert similarity("", "Bukayo Saka") == 0.0
