"""archive.crack.unstub — vendored SteamStub unpacker tree.

The actual unpacking is delegated to vendored binary-surgery code that
needs real protected .exe samples to test meaningfully — that's a
manual smoke gate, not CI work.  The tests here just check the
import surface stays sane.
"""
from __future__ import annotations


def test_unstub_unpackers_register():
    from src.core.archive.crack.unstub.unpackers import get_unpackers
    classes = get_unpackers()
    names = sorted(c.__name__ for c in classes)
    assert names == [
        "Variant20Unpacker", "Variant21Unpacker",
        "Variant30Unpacker", "Variant31Unpacker",
    ]


def test_unstub_base_unpacker_importable():
    """Sanity check: vendored base class imports without dragging in
    anything from the SteamArchiver package."""
    from src.core.archive.crack.unstub.base_unpacker import BaseUnpacker
    assert hasattr(BaseUnpacker, "process")
    assert hasattr(BaseUnpacker, "can_process")
