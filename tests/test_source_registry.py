import pytest

from ptb.core.sources import registry


@pytest.fixture(autouse=True)
def clean_registry():
    saved = dict(registry._REGISTRY)
    registry._REGISTRY.clear()
    yield
    registry._REGISTRY.clear()
    registry._REGISTRY.update(saved)


def test_register_and_get_a_source():
    @registry.register_source
    class Dummy:
        name = "dummy"

        def ingest(self, archive, **options):
            return []

    assert isinstance(registry.get_source("dummy"), Dummy)
    assert registry.list_sources() == ["dummy"]


def test_list_sources_is_sorted():
    for source_name in ("zulu", "alpha", "mike"):
        @registry.register_source
        class _S:
            name = source_name

            def ingest(self, archive, **options):
                return []

    assert registry.list_sources() == ["alpha", "mike", "zulu"]


def test_unknown_source_raises_with_available_names():
    @registry.register_source
    class Dummy:
        name = "dummy"

        def ingest(self, archive, **options):
            return []

    with pytest.raises(registry.UnknownSourceError) as excinfo:
        registry.get_source("nope")
    assert "dummy" in str(excinfo.value)


def test_duplicate_registration_is_rejected():
    @registry.register_source
    class First:
        name = "dupe"

        def ingest(self, archive, **options):
            return []

    with pytest.raises(ValueError):
        @registry.register_source
        class Second:
            name = "dupe"

            def ingest(self, archive, **options):
                return []


def test_source_without_a_name_is_rejected():
    with pytest.raises(ValueError):
        @registry.register_source
        class Nameless:
            def ingest(self, archive, **options):
                return []
