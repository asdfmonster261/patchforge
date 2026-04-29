"""Helpers for checking that the optional 'archive' extras are installed.

Archive-mode pulls in a heavy dependency set (steam[client] from a git fork,
py7zr, libarchive-c, requests-toolbelt, origamibot, discord-webhook,
privatebinapi, zstandard, qrcode).  These are declared as
[project.optional-dependencies] archive in pyproject.toml so users opt in
via `pip install patchforge[archive]`.

Code in this package should call require_extras() before importing any
archive-only library, so users without the extras get a friendly error
instead of an opaque ImportError.
"""

from __future__ import annotations

from .errors import ExtrasNotInstalled


_REQUIRED_MODULES = (
    ("steam.webauth",       "steam[client]"),
    ("steam.client",        "steam[client]"),
    ("steam.client.cdn",    "steam[client]"),
    ("qrcode",              "qrcode"),
)


def missing_extras() -> list[str]:
    """Return the distribution names of any required modules that fail to import."""
    missing: list[str] = []
    for mod, dist in _REQUIRED_MODULES:
        try:
            __import__(mod)
        except ImportError:
            if dist not in missing:
                missing.append(dist)
    return missing


def require_extras() -> None:
    """Raise ExtrasNotInstalled if any required archive extra is unavailable."""
    missing = missing_extras()
    if missing:
        raise ExtrasNotInstalled(
            "archive-mode requires optional dependencies that are not installed: "
            + ", ".join(missing)
            + ".\n  Install with: pip install patchforge[archive]"
        )


_monkey_patched = False


def patch_steam_monkey() -> None:
    """Apply gevent socket/ssl monkey-patches required by steam[client].

    Must be called before any steam.client / steam.client.cdn import touches
    the network.  Idempotent — calling twice is a no-op.  Safe to call when
    the archive extras are not installed (silently returns).
    """
    global _monkey_patched
    if _monkey_patched:
        return
    try:
        import steam.monkey  # type: ignore
        steam.monkey.patch_minimal()
    except ImportError:
        return
    _monkey_patched = True
