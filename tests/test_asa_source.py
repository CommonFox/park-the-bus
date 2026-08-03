import datetime as dt

import pytest

from ptb.core.archive import LocalBackend, RawArchive
from ptb.core.sources import asa


def test_leagues_map_to_competitions():
    assert asa.LEAGUE_TO_COMPETITION == {
        "nwsl": "NWSL", "mls": "MLS", "uslc": "USLC", "usl1": "USL1",
    }


def test_ingest_archives_reference_and_seasonal_resources(tmp_path, monkeypatch):
    archive = RawArchive(LocalBackend(tmp_path))

    def fake_get(session, league, resource, season):
        return [{"stub": True, "league": league, "resource": resource, "season": season}]

    monkeypatch.setattr(asa, "_fetch", fake_get)

    keys = asa.AsaSource().ingest(
        archive, leagues=["nwsl"], seasons=[2024],
        captured_at=dt.datetime(2026, 8, 3, 12, 0, 0),
    )

    assert any(k.startswith("asa/nwsl/teams/") for k in keys)
    assert any(k.startswith("asa/nwsl/players/") for k in keys)
    assert any(k.startswith("asa/nwsl/games/2024__") for k in keys)
    assert any(k.startswith("asa/nwsl/games-xgoals/2024__") for k in keys)
    assert any(k.startswith("asa/nwsl/players-goals-added/2024__") for k in keys)

    env = archive.read(next(k for k in keys if "/games/2024__" in k))
    assert env["league"] == "nwsl"
    assert env["resource"] == "games"
    assert env["season"] == "2024"


def test_seasonal_ingest_is_incremental(tmp_path, monkeypatch):
    archive = RawArchive(LocalBackend(tmp_path))
    calls = []
    monkeypatch.setattr(asa, "_fetch",
                        lambda s, lg, res, season: calls.append((res, season)) or [])

    asa.AsaSource().ingest(archive, leagues=["nwsl"], seasons=[2024],
                           captured_at=dt.datetime(2026, 8, 3, 12, 0, 0))
    first = list(calls)
    asa.AsaSource().ingest(archive, leagues=["nwsl"], seasons=[2024],
                           captured_at=dt.datetime(2026, 8, 3, 13, 0, 0))

    seasonal_second = [c for c in calls[len(first):] if c[1] is not None]
    assert seasonal_second == []


def test_refetch_repulls_seasonal(tmp_path, monkeypatch):
    archive = RawArchive(LocalBackend(tmp_path))
    calls = []
    monkeypatch.setattr(asa, "_fetch",
                        lambda s, lg, res, season: calls.append((res, season)) or [])

    asa.AsaSource().ingest(archive, leagues=["nwsl"], seasons=[2024],
                           captured_at=dt.datetime(2026, 8, 3, 12, 0, 0))
    n = len(calls)
    asa.AsaSource().ingest(archive, leagues=["nwsl"], seasons=[2024], refetch=True,
                           captured_at=dt.datetime(2026, 8, 3, 13, 0, 0))
    seasonal_second = [c for c in calls[n:] if c[1] is not None]
    assert ("games", "2024") in seasonal_second


def test_ingest_rejects_unknown_league(tmp_path):
    archive = RawArchive(LocalBackend(tmp_path))
    with pytest.raises(ValueError, match="eredivisie"):
        asa.AsaSource().ingest(archive, leagues=["eredivisie"], seasons=[2024])
