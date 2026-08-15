import pytest

from ptb.core import config


@pytest.fixture(autouse=True)
def isolated_archive(tmp_path, monkeypatch):
    """Point the archive at this test's own tmp_path.

    Autouse, so no test can read or write the real `data/raw` even by
    accident. This works without threading a root through every call
    because archive.py resolves config.RAW_DIR per call rather than at
    import -- which also means these tests exercise the same default path
    the CLI uses, instead of a test-only injection seam.
    """
    raw = tmp_path / "raw"
    raw.mkdir()
    monkeypatch.setattr(config, "RAW_DIR", raw)
    return raw
