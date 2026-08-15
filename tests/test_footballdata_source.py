import datetime as dt

import pytest

from ptb.core import archive
from ptb.core.silver import footballdata as fd


def test_season_code_maps_start_year_to_four_digits():
    assert fd.season_code(2024) == "2425"
    assert fd.season_code(1993) == "9394"
    assert fd.season_code(1999) == "9900"
    assert fd.season_code(2000) == "0001"


def test_season_label_uses_the_warehouse_convention():
    assert fd.season_label(2024) == "2024/25"
    assert fd.season_label(1993) == "1993/94"
    assert fd.season_label(1999) == "1999/00"


def test_parse_season_is_the_inverse_of_season_label():
    for year in (1993, 1999, 2000, 2024):
        assert fd.parse_season(fd.season_label(year)) == year


def test_parse_season_tolerates_dash_form():
    """The CLI feeds --season through parse_season for every source, so it must
    also accept vaastav's dash form."""
    assert fd.parse_season("2024-25") == 2024
    assert fd.parse_season("2024/25") == 2024


def test_big_5_codes_are_all_known_competitions():
    for code in fd.BIG_5:
        assert code in fd.COMPETITIONS


def test_decode_strips_the_utf8_bom():
    """Every football-data file starts with EF BB BF, which would otherwise
    turn the first column name into '\\ufeffDiv' and break lookups."""
    raw = b"\xef\xbb\xbfDiv,Date\nE0,16/08/2024\n"
    assert fd.decode_csv(raw).startswith("Div,Date")


def test_decode_falls_back_to_latin1_for_stray_bytes():
    """Older files are latin-1 and carry accented referee names."""
    raw = "Div,Referee\nE0,M Ju\xe1rez\n".encode("latin-1")
    assert "Ju\xe1rez" in fd.decode_csv(raw)


def test_ingest_archives_one_envelope_per_competition_season(tmp_path, monkeypatch):
    monkeypatch.setattr(fd, "_fetch_csv", lambda url: "Div,Date\nE0,16/08/2024\n")

    keys = fd.ingest(
        competitions=["E0", "SP1"],
        seasons=[2024],
        captured_at=dt.datetime(2026, 8, 2, 10, 15, 0),
    )

    assert keys == [
        "footballdata/season/E0__2024-25__20260802T101500Z.json.gz",
        "footballdata/season/SP1__2024-25__20260802T101500Z.json.gz",
    ]
    envelope = archive.read(keys[0])
    assert envelope["source"] == "footballdata"
    assert envelope["competition"] == "E0"
    assert envelope["season"] == "2024/25"
    assert envelope["csv"].startswith("Div,Date")
    assert "mmz4281/2425/E0.csv" in envelope["url"]


def test_ingest_skips_a_season_the_site_has_not_published(tmp_path, monkeypatch):

    def fake_fetch(url):
        if "2526" in url:
            raise fd.NotPublishedError(url)
        return "Div,Date\nE0,16/08/2024\n"

    monkeypatch.setattr(fd, "_fetch_csv", fake_fetch)

    keys = fd.ingest(
        competitions=["E0"], seasons=[2024, 2025],
        captured_at=dt.datetime(2026, 8, 2, 10, 15, 0),
    )

    assert len(keys) == 1
    assert "2024-25" in keys[0]


def test_ingest_rejects_an_unknown_competition(tmp_path):
    with pytest.raises(ValueError, match="ZZ9"):
        fd.ingest(competitions=["ZZ9"], seasons=[2024])
