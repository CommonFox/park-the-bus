import datetime as dt

import pytest

from ptb.core import archive


def test_write_returns_key_and_roundtrips():
    stamp = dt.datetime(2026, 8, 2, 10, 15, 0)
    key = archive.write("footballdata", "season", {"csv": "a,b\n1,2\n"},
                        label="E0__2024-25", captured_at=stamp)

    assert key == "footballdata/season/E0__2024-25__20260802T101500Z.json.gz"
    assert archive.read(key) == {"csv": "a,b\n1,2\n"}


def test_key_omits_label_when_absent():
    stamp = dt.datetime(2026, 8, 2, 10, 15, 0)
    key = archive.write("fotmob", "matches", {"x": 1}, captured_at=stamp)
    assert key == "fotmob/matches/20260802T101500Z.json.gz"


def test_keys_filters_by_source_and_endpoint():
    stamp = dt.datetime(2026, 8, 2, 10, 15, 0)
    archive.write("footballdata", "season", {"n": 1}, label="E0", captured_at=stamp)
    archive.write("footballdata", "season", {"n": 2}, label="SP1", captured_at=stamp)
    archive.write("fotmob", "matches", {"n": 3}, captured_at=stamp)

    assert len(archive.keys()) == 3
    assert len(archive.keys(source="footballdata")) == 2
    assert len(archive.keys(source="footballdata", endpoint="season")) == 2
    assert len(archive.keys(source="fotmob")) == 1


def test_keys_are_returned_sorted():
    early = dt.datetime(2026, 8, 1, 9, 0, 0)
    late = dt.datetime(2026, 8, 3, 9, 0, 0)
    archive.write("footballdata", "season", {"n": 2}, label="E0", captured_at=late)
    archive.write("footballdata", "season", {"n": 1}, label="E0", captured_at=early)

    keys = archive.keys(source="footballdata")
    assert keys == sorted(keys)
    assert archive.read(keys[0]) == {"n": 1}


def test_exists_reports_written_keys():
    stamp = dt.datetime(2026, 8, 2, 10, 15, 0)
    key = archive.write("fpl", "bootstrap", {"n": 1}, captured_at=stamp)
    assert archive.exists(key)
    assert not archive.exists("fpl/bootstrap/20000101T000000Z.json.gz")


def test_stamp_of_recovers_capture_time():
    stamp = dt.datetime(2026, 8, 2, 10, 15, 0)
    key = archive.write("footballdata", "season", {"n": 1}, label="E0", captured_at=stamp)
    assert archive.stamp_of(key) == stamp


def test_stamp_of_rejects_a_non_archive_key():
    with pytest.raises(ValueError):
        archive.stamp_of("footballdata/season/not-a-stamp.json.gz")


def test_a_key_cannot_escape_the_archive_root():
    """Keys are strings from payload metadata, so traversal has to be refused
    rather than trusted."""
    with pytest.raises(ValueError, match="escapes"):
        archive.read("../../etc/passwd")


def test_root_overrides_the_configured_archive(tmp_path):
    """Every entry point takes an explicit root, so one call can be pointed
    somewhere else without touching global config."""
    other = tmp_path / "elsewhere"
    stamp = dt.datetime(2026, 8, 2, 10, 15, 0)
    key = archive.write("fotmob", "matches", {"n": 7}, captured_at=stamp, root=other)

    assert archive.read(key, root=other) == {"n": 7}
    assert archive.keys(root=other) == [key]
    assert archive.keys() == []


def test_public_api_takes_no_paths():
    """The archive is addressed by key so an object-store backend can drop in."""
    stamp = dt.datetime(2026, 8, 2, 10, 15, 0)
    key = archive.write("footballdata", "season", {"n": 1}, captured_at=stamp)
    assert isinstance(key, str)
    assert all(isinstance(k, str) for k in archive.keys())
