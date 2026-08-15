"""The silver registry, and the contract every source module owes it."""
import inspect

import pytest

from ptb.core import silver

EXPECTED_SOURCES = {
    "asa", "draftkings", "footballdata", "fotmob", "fpl", "understat", "vaastav",
}


def test_every_source_is_registered():
    assert set(silver.SOURCES) == EXPECTED_SOURCES


def test_source_names_is_sorted():
    assert silver.source_names() == sorted(EXPECTED_SOURCES)


def test_lookup_returns_the_module():
    assert silver.source("understat") is silver.understat


def test_unknown_source_raises_with_available_names():
    with pytest.raises(silver.UnknownSourceError) as excinfo:
        silver.source("nope")
    assert "understat" in str(excinfo.value)


@pytest.mark.parametrize("name", sorted(EXPECTED_SOURCES))
def test_module_name_matches_its_registry_key(name):
    assert silver.SOURCES[name].NAME == name


@pytest.mark.parametrize("name", sorted(EXPECTED_SOURCES))
def test_every_source_has_both_halves(name):
    """ingest and load are the whole contract -- `rebuild` calls load by name,
    and the CLI calls ingest by name."""
    module = silver.SOURCES[name]
    assert callable(module.ingest)
    assert callable(module.load)


@pytest.mark.parametrize("name", sorted(EXPECTED_SOURCES))
def test_ingest_accepts_a_root_and_swallows_unknown_options(name):
    """`ptb ingest all` passes one option set to every source, so each must
    tolerate flags meant for another."""
    signature = inspect.signature(silver.SOURCES[name].ingest)
    assert "root" in signature.parameters
    assert any(p.kind is inspect.Parameter.VAR_KEYWORD
               for p in signature.parameters.values())


@pytest.mark.parametrize("name", sorted(EXPECTED_SOURCES))
def test_identity_hooks_are_callable_when_present(name):
    """Both are optional -- FotMob reports no fixtures, football-data no
    players -- but a hook that exists has to be callable, since rebuild
    dispatches on presence alone."""
    module = silver.SOURCES[name]
    for hook in ("resolve_matches", "resolve_players"):
        assert callable(getattr(module, hook, None)) or not hasattr(module, hook)
