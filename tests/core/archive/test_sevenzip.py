"""archive.sevenzip — fetch + cache the native 7z binary on first use.

Tests stub the platform/arch detection and the actual download so the
suite stays offline-safe.
"""
from __future__ import annotations

from unittest import mock


def _redirect_bin_dir(tmp_path):
    from src.core.archive import sevenzip as sz
    fake_bin = tmp_path / "bin"
    return mock.patch.object(sz, "bin_dir", lambda: fake_bin)


def test_sevenzip_returns_none_on_unsupported_platform(tmp_path):
    from src.core.archive import sevenzip as sz
    with _redirect_bin_dir(tmp_path), \
         mock.patch.object(sz, "_detect", return_value=(None, None)):
        sz.reset_cache()
        assert sz.get_7zip() is None


def test_sevenzip_cache_hit_skips_download(tmp_path):
    """When a 7z binary already exists in bin_dir for this arch the
    download path must NOT be invoked."""
    from src.core.archive import sevenzip as sz
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    (fake_bin / "7z").write_bytes(b"\x7fELF...")
    download_calls: list = []
    with mock.patch.object(sz, "bin_dir", lambda: fake_bin), \
         mock.patch.object(sz, "_detect", return_value=("linux", "x86_64")), \
         mock.patch.object(sz, "_download_to",
                           side_effect=lambda *a, **kw: download_calls.append(a)):
        sz.reset_cache()
        path = sz.get_7zip()
        assert path == fake_bin / "7z"
        assert download_calls == []


def test_sevenzip_download_failure_returns_none(tmp_path):
    """Network failure during the first-use download must clean up the
    partial file and return None — caller falls back to py7zr."""
    from src.core.archive import sevenzip as sz
    fake_bin = tmp_path / "bin"
    with mock.patch.object(sz, "bin_dir", lambda: fake_bin), \
         mock.patch.object(sz, "_detect", return_value=("windows", "x86_64")), \
         mock.patch.object(sz, "_download_to",
                           side_effect=OSError("network down")):
        sz.reset_cache()
        assert sz.get_7zip() is None
        assert not (fake_bin / "7zr.exe").exists()


def test_sevenzip_no_url_for_arch_returns_none(tmp_path):
    """Archs without a known download URL (ppc64 etc.) should silently
    bail, not crash."""
    from src.core.archive import sevenzip as sz
    with _redirect_bin_dir(tmp_path), \
         mock.patch.object(sz, "_detect", return_value=("linux", "ppc64")):
        sz.reset_cache()
        assert sz.get_7zip() is None
