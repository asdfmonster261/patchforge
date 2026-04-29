"""Exceptions raised by archive-mode code."""


class ArchiveError(Exception):
    """Base class for archive-mode failures."""


class ExtrasNotInstalled(ArchiveError):
    """Raised when archive-mode is invoked but the optional 'archive' extras
    are not installed (steam[client], py7zr, libarchive-c, etc.)."""


class SessionDead(ArchiveError):
    """Raised when the Steam CM session is unresponsive after exhausting
    retries.  Catching this should trigger a re-login, not be silently ignored."""
