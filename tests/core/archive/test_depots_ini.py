"""archive.depots_ini — append-only depot-name lookup file.

Used by the live display to render `Depot 731  Counter-Strike Content`
instead of a bare numeric depot id.  Unknown depot ids accumulate in
the file; the user (or future PatchForge upgrade) can fill names in.
"""
from __future__ import annotations

from unittest import mock


def _redirect_depots_ini(tmp_path):
    from src.core.archive import depots_ini as di
    fake = tmp_path / "archive_depots.ini"
    return mock.patch.object(di, "_DEPOTS_FILE", fake)


def test_depots_ini_load_missing_returns_empty(tmp_path):
    from src.core.archive import depots_ini as di
    with _redirect_depots_ini(tmp_path):
        assert di.load() == {}


def test_depots_ini_record_unknown_creates_file(tmp_path):
    from src.core.archive import depots_ini as di
    with _redirect_depots_ini(tmp_path):
        added = di.record_unknown(["731", "732"])
        assert sorted(added) == ["731", "732"]
        assert di.load() == {"731": "", "732": ""}


def test_depots_ini_record_unknown_skips_existing(tmp_path):
    """Re-recording an id that's already in the file must not overwrite
    a name the user has filled in by hand."""
    from src.core.archive import depots_ini as di
    with _redirect_depots_ini(tmp_path):
        di.record_unknown(["731"])
        added = di.record_unknown(["731", "732"])
        assert added == ["732"]
        di._DEPOTS_FILE.write_text(
            "[depots]\n731 = Counter-Strike Content\n732 = \n",
            encoding="utf-8",
        )
        loaded = di.load()
        assert loaded["731"].lower().startswith("counter")


def test_depots_ini_record_unknown_empty_input(tmp_path):
    """Empty input must not even create the file — keeps a clean working
    tree on first runs that didn't encounter any unknown depots."""
    from src.core.archive import depots_ini as di
    with _redirect_depots_ini(tmp_path):
        assert di.record_unknown([]) == []
        assert not di.depots_path().exists()
