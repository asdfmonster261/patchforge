# vendored from steamarchiver/crack/unstub/ — DO NOT MODIFY.
# This implements SteamStub DRM unpacking via low-level PE/x86/RC4 surgery.
# Re-vendor the file from upstream when fixing variant bugs.

"""
Unpacker registry.

Each unpacker module in this package should define one or more subclasses of
``BaseUnpacker``.  The ``get_unpackers()`` helper collects them all so the CLI
can iterate and find the right one for a given file.
"""

from typing import List, Type

from ..base_unpacker import BaseUnpacker

# Collected registry (populated by register())
_REGISTRY: List[Type[BaseUnpacker]] = []


def register(cls: Type[BaseUnpacker]) -> Type[BaseUnpacker]:
    """Class decorator — adds an unpacker to the global registry."""
    _REGISTRY.append(cls)
    return cls


def get_unpackers() -> List[Type[BaseUnpacker]]:
    """Return all registered unpacker classes (imports modules on first call)."""
    if not _REGISTRY:
        # Lazy-import unpacker modules so their @register decorators fire.
        from . import variant20  # noqa: F401
        from . import variant21  # noqa: F401
        from . import variant30  # noqa: F401
        from . import variant31  # noqa: F401
    return list(_REGISTRY)
