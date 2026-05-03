"""cli — `archive depot` subcommand (DepotDownloader-style historical pull).

Argparse round-trip + handler dispatch to download_manifest().  The
download_manifest() core itself is unit-tested in
core/archive/test_download.py.
"""
from __future__ import annotations

from unittest import mock

import pytest


def test_archive_depot_argparser_accepts_required_flags():
    """argparse round-trip: --app/--depot/--manifest are required ints,
    --branch defaults to public, --output-dir defaults to cwd."""
    from src.cli.main import _build_parser
    parser = _build_parser()
    ns = parser.parse_args([
        "archive", "depot",
        "--app",      "4048390",
        "--depot",    "4048391",
        "--manifest", "5520155637093182018",
    ])
    assert ns.app_id       == 4048390
    assert ns.depot_id     == 4048391
    assert ns.manifest_gid == 5520155637093182018
    assert ns.branch       == "public"
    assert ns.output_dir   == "."
    assert ns.workers      == 8
    assert ns.max_retries  == 1


def test_archive_depot_argparser_rejects_missing_required():
    """All three of --app/--depot/--manifest required — omitting any errors."""
    from src.cli.main import _build_parser
    parser = _build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args([
            "archive", "depot",
            "--app", "1",
            "--depot", "2",
            # missing --manifest
        ])


def test_cmd_archive_depot_dispatches_to_download_manifest(monkeypatch, tmp_path):
    """Handler must thread CLI args into download_manifest and clean up
    the Steam client even on success."""
    from src.cli import main as cli_mod

    monkeypatch.setattr(cli_mod, "_archive_require_extras_or_die", lambda: None)

    fake_creds = mock.Mock()
    fake_creds.has_login_tokens.return_value = True
    fake_creds.username             = "u"
    fake_creds.steam_id             = 1
    fake_creds.client_refresh_token = "tok"

    # Patch BOTH the sys.modules entry AND the package attribute.
    # `from src.core.archive import credentials as creds_mod` resolves
    # via the package's bound attribute, which doesn't update when
    # sys.modules changes — so once credentials has been imported by
    # any other test in this run, swapping sys.modules alone is not
    # enough.  Patch the package's `credentials` attribute too.
    fake_creds_mod = mock.Mock()
    fake_creds_mod.load.return_value = fake_creds
    monkeypatch.setitem(__import__("sys").modules,
                        "src.core.archive.credentials", fake_creds_mod)
    import src.core.archive as _archive_pkg
    monkeypatch.setattr(_archive_pkg, "credentials", fake_creds_mod,
                        raising=False)

    fake_client = mock.Mock()
    fake_cdn    = mock.Mock()
    fake_appinfo = mock.Mock()
    fake_appinfo.login = mock.Mock(return_value=(fake_client, fake_cdn))
    monkeypatch.setitem(__import__("sys").modules,
                        "src.core.archive.appinfo", fake_appinfo)

    fake_progress = mock.Mock()
    fake_progress.build_subscriber = mock.Mock(return_value=mock.Mock())
    monkeypatch.setitem(__import__("sys").modules,
                        "src.core.archive.cli_progress", fake_progress)

    fake_dl = mock.Mock()
    fake_dl.download_manifest = mock.Mock(
        return_value=tmp_path / "out_dir",
    )
    monkeypatch.setitem(__import__("sys").modules,
                        "src.core.archive.download", fake_dl)

    args = mock.Mock(
        app_id=730, depot_id=731, manifest_gid=42,
        branch="public", branch_password=None,
        output_dir=str(tmp_path), workers=4, max_retries=2,
        no_progress=True,
    )
    cli_mod._cmd_archive_depot(args)

    fake_dl.download_manifest.assert_called_once()
    _, kwargs = fake_dl.download_manifest.call_args
    assert kwargs["app_id"]       == 730
    assert kwargs["depot_id"]     == 731
    assert kwargs["manifest_gid"] == 42
    assert kwargs["branch"]       == "public"
    assert kwargs["workers"]      == 4
    assert kwargs["max_retries"]  == 2

    fake_client.logout.assert_called_once()
