import datetime as dt

import pytest

from ptb.core.archive import LocalBackend, RawArchive


@pytest.fixture
def archive(tmp_path):
    return RawArchive(LocalBackend(tmp_path))


def test_write_returns_key_and_roundtrips(archive):
    stamp = dt.datetime(2026, 8, 2, 10, 15, 0)
    key = archive.write("footballdata", "season", {"csv": "a,b\n1,2\n"},
                        label="E0__2024-25", captured_at=stamp)

    assert key == "footballdata/season/E0__2024-25__20260802T101500Z.json.gz"
    assert archive.read(key) == {"csv": "a,b\n1,2\n"}


def test_key_omits_label_when_absent(archive):
    stamp = dt.datetime(2026, 8, 2, 10, 15, 0)
    key = archive.write("fotmob", "matches", {"x": 1}, captured_at=stamp)
    assert key == "fotmob/matches/20260802T101500Z.json.gz"


def test_keys_filters_by_source_and_endpoint(archive):
    stamp = dt.datetime(2026, 8, 2, 10, 15, 0)
    archive.write("footballdata", "season", {"n": 1}, label="E0", captured_at=stamp)
    archive.write("footballdata", "season", {"n": 2}, label="SP1", captured_at=stamp)
    archive.write("fotmob", "matches", {"n": 3}, captured_at=stamp)

    assert len(archive.keys()) == 3
    assert len(archive.keys(source="footballdata")) == 2
    assert len(archive.keys(source="footballdata", endpoint="season")) == 2
    assert len(archive.keys(source="fotmob")) == 1


def test_keys_are_returned_sorted(archive):
    early = dt.datetime(2026, 8, 1, 9, 0, 0)
    late = dt.datetime(2026, 8, 3, 9, 0, 0)
    archive.write("footballdata", "season", {"n": 2}, label="E0", captured_at=late)
    archive.write("footballdata", "season", {"n": 1}, label="E0", captured_at=early)

    keys = archive.keys(source="footballdata")
    assert keys == sorted(keys)
    assert archive.read(keys[0]) == {"n": 1}


def test_stamp_of_recovers_capture_time(archive):
    stamp = dt.datetime(2026, 8, 2, 10, 15, 0)
    key = archive.write("footballdata", "season", {"n": 1}, label="E0", captured_at=stamp)
    assert archive.stamp_of(key) == stamp


def test_stamp_of_rejects_a_non_archive_key(archive):
    with pytest.raises(ValueError):
        archive.stamp_of("footballdata/season/not-a-stamp.json.gz")


def test_public_api_takes_no_paths(archive):
    """The archive is addressed by key so an object-store backend can drop in."""
    stamp = dt.datetime(2026, 8, 2, 10, 15, 0)
    key = archive.write("footballdata", "season", {"n": 1}, captured_at=stamp)
    assert isinstance(key, str)
    assert all(isinstance(k, str) for k in archive.keys())
