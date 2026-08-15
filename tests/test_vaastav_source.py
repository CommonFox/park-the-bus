import datetime as dt

from ptb.core import archive
from ptb.core.silver import vaastav


def test_season_label_converts_dash_form():
    assert vaastav.season_label("2024-25") == "2024/25"
    assert vaastav.season_label("2016-17") == "2016/17"


def test_to_dash_normalises_every_season_form():
    # int start year (as the CLI produces via parse_season), slash form, dash form
    assert vaastav.to_dash(2024) == "2024-25"
    assert vaastav.to_dash("2024/25") == "2024-25"
    assert vaastav.to_dash("2024-25") == "2024-25"
    assert vaastav.to_dash(1999) == "1999-00"


def test_ingest_accepts_int_seasons_from_the_cli(tmp_path, monkeypatch):
    monkeypatch.setattr(vaastav, "_fetch_csv", lambda s, url: "a,b\n1,2\n")
    keys = vaastav.ingest(
        seasons=[2024], captured_at=dt.datetime(2026, 8, 3, 12, 0, 0))
    assert any(k.startswith("vaastav/season/2024-25__") for k in keys)


def test_ingest_archives_one_payload_per_season(tmp_path, monkeypatch):
    monkeypatch.setattr(vaastav, "_fetch_csv",
                        lambda s, url: "id,code\n1,100\n" if "players_raw" in url
                        else "element,round\n1,1\n")

    keys = vaastav.ingest(
        seasons=["2023-24", "2024-25"],
        captured_at=dt.datetime(2026, 8, 3, 12, 0, 0))

    assert any(k.startswith("vaastav/season/2023-24__") for k in keys)
    assert any(k.startswith("vaastav/season/2024-25__") for k in keys)
    env = archive.read(next(k for k in keys if "2024-25__" in k))
    assert env["season"] == "2024/25"
    assert env["data"]["players_raw"].startswith("id,code")
    assert env["data"]["merged_gw"].startswith("element,round")


def test_ingest_is_incremental(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(vaastav, "_fetch_csv",
                        lambda s, url: calls.append(url) or "a,b\n1,2\n")

    vaastav.ingest(seasons=["2024-25"],
                   captured_at=dt.datetime(2026, 8, 3, 12, 0, 0))
    first = len(calls)
    vaastav.ingest(seasons=["2024-25"],
                   captured_at=dt.datetime(2026, 8, 3, 13, 0, 0))
    assert len(calls) == first  # second run fetched nothing new


def test_refetch_repulls(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(vaastav, "_fetch_csv",
                        lambda s, url: calls.append(url) or "a,b\n1,2\n")

    vaastav.ingest(seasons=["2024-25"],
                   captured_at=dt.datetime(2026, 8, 3, 12, 0, 0))
    n = len(calls)
    vaastav.ingest(seasons=["2024-25"], refetch=True,
                   captured_at=dt.datetime(2026, 8, 3, 13, 0, 0))
    assert len(calls) > n
