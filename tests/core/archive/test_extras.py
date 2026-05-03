"""archive._extras — soft-fail import probe for the optional `archive` extra.

PatchForge core ships without steam[client] etc.  archive-mode pulls in
heavy deps, declared as `[project.optional-dependencies] archive`.  These
helpers let CLI commands raise a clean error when those deps are missing
instead of an opaque `ImportError` deep in some submodule.
"""
from __future__ import annotations

import pytest


def test_missing_extras_returns_list():
    from src.core.archive._extras import missing_extras
    assert isinstance(missing_extras(), list)


def test_require_extras_raises_when_missing(monkeypatch):
    from src.core.archive import _extras
    from src.core.archive.errors import ExtrasNotInstalled
    monkeypatch.setattr(_extras, "missing_extras", lambda: ["fake-dist"])
    with pytest.raises(ExtrasNotInstalled, match="fake-dist"):
        _extras.require_extras()


def test_require_extras_silent_when_satisfied(monkeypatch):
    from src.core.archive import _extras
    monkeypatch.setattr(_extras, "missing_extras", lambda: [])
    _extras.require_extras()  # must not raise
