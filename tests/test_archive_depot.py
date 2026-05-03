"""Tests for the `archive depot` subcommand, download_manifest core,
and ManifestRecord persistence in .xarchive files.

download_manifest is the DepotDownloader-style historical-pull entry
point — fetches one (app, depot, manifest_gid) triple, persists the
manifest to depotcache/, and writes per-file content into a flat dest
directory.  ManifestRecord captures (depot, manifest_gid) tuples after
each `archive download` so users can replay old builds via
`archive depot` later.  These tests stub _import_steam so we don't drag
in steam[client] / gevent for unit work.
"""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace
from unittest import mock
from unittest.mock import MagicMock, patch

import pytest

# Match the `src.` import style the rest of the suite uses.
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


# ---------------------------------------------------------------------------
# Fakes used across tests
# ---------------------------------------------------------------------------

class _FakeDepotFile:
    def __init__(self, filename, content=b"", is_dir=False, is_sym=False,
                 link="", is_exec=False):
        self.filename       = filename
        self.size           = len(content)
        self.is_directory   = is_dir
        self.is_symlink     = is_sym
        self.is_executable  = is_exec
        self.linktarget     = link
        self._buf           = content
        self._pos           = 0

    def read(self, n):
        chunk = self._buf[self._pos:self._pos + n]
        self._pos += len(chunk)
        return chunk


class _FakeManifest:
    def __init__(self, depot_id, gid, files):
        self.depot_id  = depot_id
        self.gid       = gid
        self.name      = ""
        self.metadata  = mock.Mock()
        self.signature = mock.Mock()
        self.payload   = mock.Mock()
        self.payload.SerializeToString = lambda: b"payload-bytes"
        self._files    = files

    def __iter__(self):
        return iter(self._files)

    def serialize(self, compress=False):
        return b"serialized-manifest-bytes"


class _FakePool:
    """Minimal gevent.pool.Pool stand-in — runs serial imap_unordered."""
    def __init__(self, size=8):
        self.size = size

    def imap_unordered(self, fn, items):
        return [fn(x) for x in items]

    def kill(self):
        pass


def _stub_import_steam(monkeypatch, dl_mod, EResult=None):
    """Replace _import_steam so download_manifest never imports gevent.

    EResult tuple slot must support `.OK` / `.Timeout` attributes — supply a
    SimpleNamespace if the test exercises branch-password handling.
    """
    if EResult is None:
        EResult = mock.Mock()
        EResult.OK = "OK"
        EResult.Timeout = "TIMEOUT"
    monkeypatch.setattr(
        dl_mod, "_import_steam",
        lambda: (_FakePool, Exception, object(), object(), EResult, Exception),
    )


# ---------------------------------------------------------------------------
# download_manifest core
# ---------------------------------------------------------------------------

def test_download_manifest_writes_files_and_persists_manifest(monkeypatch, tmp_path):
    from src.core.archive import download as dl_mod
    _stub_import_steam(monkeypatch, dl_mod)

    files = [
        _FakeDepotFile("subdir",        is_dir=True),
        _FakeDepotFile("subdir/a.bin",  content=b"hello world"),
        _FakeDepotFile("readme.txt",    content=b"docs"),
    ]
    manifest = _FakeManifest(depot_id=4048391, gid=5520155637093182018, files=files)

    cdn = mock.Mock()
    cdn.get_manifest_request_code.return_value = 9991
    cdn.get_manifest.return_value              = manifest

    out = dl_mod.download_manifest(
        client=mock.Mock(), cdn=cdn,
        app_id=4048390, depot_id=4048391, manifest_gid=5520155637093182018,
        output_dir=tmp_path,
    )

    # Dest layout: tmp_path/<app>_<depot>_<gid>/{subdir/a.bin, readme.txt}
    expected_root = tmp_path / "4048390_4048391_5520155637093182018"
    assert out == expected_root
    assert (expected_root / "subdir").is_dir()
    assert (expected_root / "subdir" / "a.bin").read_bytes() == b"hello world"
    assert (expected_root / "readme.txt").read_bytes()       == b"docs"

    # Manifest serialised into depotcache/<depot>_<gid>.manifest
    cache_path = tmp_path / "depotcache" / "4048391_5520155637093182018.manifest"
    assert cache_path.read_bytes() == b"serialized-manifest-bytes"


def test_download_manifest_threads_request_code_into_get_manifest(monkeypatch, tmp_path):
    """get_manifest must receive the code returned by get_manifest_request_code."""
    from src.core.archive import download as dl_mod
    _stub_import_steam(monkeypatch, dl_mod)

    cdn = mock.Mock()
    cdn.get_manifest_request_code.return_value = 424242
    cdn.get_manifest.return_value = _FakeManifest(1, 2, [])

    dl_mod.download_manifest(
        client=mock.Mock(), cdn=cdn,
        app_id=730, depot_id=731, manifest_gid=99,
        output_dir=tmp_path,
    )

    cdn.get_manifest_request_code.assert_called_once()
    args, kwargs = cdn.get_manifest_request_code.call_args
    assert args[:3] == (730, 731, 99)
    assert kwargs.get("branch") == "public"
    assert kwargs.get("branch_password_hash") is None

    cdn.get_manifest.assert_called_once()
    args, kwargs = cdn.get_manifest.call_args
    assert args[:3] == (730, 731, 99)
    assert kwargs.get("manifest_request_code") == 424242


def test_download_manifest_skips_password_check_for_public(monkeypatch, tmp_path):
    """branch=public must never call check_beta_password, even if password
    accidentally provided — Steam rejects the request and there's no
    encrypted manifest to unlock."""
    from src.core.archive import download as dl_mod
    _stub_import_steam(monkeypatch, dl_mod)

    cdn = mock.Mock()
    cdn.get_manifest_request_code.return_value = 1
    cdn.get_manifest.return_value = _FakeManifest(1, 2, [])

    dl_mod.download_manifest(
        client=mock.Mock(), cdn=cdn,
        app_id=730, depot_id=731, manifest_gid=99,
        output_dir=tmp_path,
        branch="public", branch_password="ignored",
    )
    cdn.check_beta_password.assert_not_called()


def test_download_manifest_unlocks_encrypted_branch(monkeypatch, tmp_path):
    """For non-public branches with --branch-password, must call
    check_beta_password and forward the resulting hash bytes (hex-encoded)
    to get_manifest_request_code."""
    from src.core.archive import download as dl_mod
    EResult = mock.Mock()
    EResult.OK      = "OK"
    EResult.Timeout = "TIMEOUT"
    _stub_import_steam(monkeypatch, dl_mod, EResult=EResult)

    cdn = mock.Mock()
    cdn.check_beta_password.return_value = "OK"
    cdn.beta_passwords = {(730, "beta"): bytes.fromhex("deadbeef")}
    cdn.get_manifest_request_code.return_value = 5
    cdn.get_manifest.return_value = _FakeManifest(1, 2, [])

    dl_mod.download_manifest(
        client=mock.Mock(), cdn=cdn,
        app_id=730, depot_id=731, manifest_gid=99,
        output_dir=tmp_path,
        branch="beta", branch_password="hunter2",
    )

    cdn.check_beta_password.assert_called_once_with(730, "hunter2")
    _, kwargs = cdn.get_manifest_request_code.call_args
    assert kwargs.get("branch") == "beta"
    assert kwargs.get("branch_password_hash") == "deadbeef"


def test_download_manifest_raises_when_password_check_fails(monkeypatch, tmp_path):
    from src.core.archive import download as dl_mod
    EResult = mock.Mock()
    EResult.OK      = "OK"
    EResult.Timeout = "TIMEOUT"
    _stub_import_steam(monkeypatch, dl_mod, EResult=EResult)

    cdn = mock.Mock()
    cdn.check_beta_password.return_value = "INVALID_PASSWORD"

    with pytest.raises(ValueError, match="password"):
        dl_mod.download_manifest(
            client=mock.Mock(), cdn=cdn,
            app_id=730, depot_id=731, manifest_gid=99,
            output_dir=tmp_path,
            branch="beta", branch_password="wrong",
        )
    cdn.get_manifest.assert_not_called()


def test_download_manifest_skips_existing_files(monkeypatch, tmp_path):
    """Re-running against a populated dest must not re-write files whose
    size already matches — keeps repeated runs cheap."""
    from src.core.archive import download as dl_mod
    _stub_import_steam(monkeypatch, dl_mod)

    dest = tmp_path / "4048390_4048391_99"
    dest.mkdir(parents=True)
    (dest / "a.bin").write_bytes(b"hello world")  # 11 bytes — matches size

    df = _FakeDepotFile("a.bin", content=b"hello world")
    cdn = mock.Mock()
    cdn.get_manifest_request_code.return_value = 1
    cdn.get_manifest.return_value = _FakeManifest(4048391, 99, [df])

    dl_mod.download_manifest(
        client=mock.Mock(), cdn=cdn,
        app_id=4048390, depot_id=4048391, manifest_gid=99,
        output_dir=tmp_path,
    )
    # depot_file.read should NOT have advanced — file skipped on size match.
    assert df._pos == 0


def test_download_manifest_emits_events(monkeypatch, tmp_path):
    from src.core.archive import download as dl_mod
    _stub_import_steam(monkeypatch, dl_mod)

    cdn = mock.Mock()
    cdn.get_manifest_request_code.return_value = 1
    cdn.get_manifest.return_value = _FakeManifest(
        1, 2, [_FakeDepotFile("a.bin", content=b"abc")],
    )

    events = []
    dl_mod.download_manifest(
        client=mock.Mock(), cdn=cdn,
        app_id=730, depot_id=731, manifest_gid=99,
        output_dir=tmp_path, on_event=events.append,
    )

    kinds = [e.kind for e in events]
    assert "stage" in kinds
    assert "file_started"  in kinds
    assert "file_finished" in kinds


# ---------------------------------------------------------------------------
# CLI: archive depot
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# ManifestRecord schema + roundtrip
# ---------------------------------------------------------------------------

def test_manifest_record_roundtrip(tmp_path):
    """save() / load() preserves manifest_history list contents."""
    from src.core.archive import project as project_mod

    proj = project_mod.new_project()
    entry = project_mod.AppEntry(app_id=730, current_buildid="200")
    entry.manifest_history.extend([
        project_mod.ManifestRecord(
            buildid="200", branch="public", platform="windows",
            depot_id=731, depot_name="csgo", manifest_gid="111",
            timeupdated=1000,
        ),
        project_mod.ManifestRecord(
            buildid="200", branch="public", platform="linux",
            depot_id=731, depot_name="csgo", manifest_gid="111",
            timeupdated=1000,
        ),
    ])
    proj.apps.append(entry)

    path = tmp_path / "p.xarchive"
    project_mod.save(proj, path)

    loaded = project_mod.load(path)
    assert len(loaded.apps[0].manifest_history) == 2
    rec = loaded.apps[0].manifest_history[0]
    assert rec.buildid      == "200"
    assert rec.platform     == "windows"
    assert rec.depot_id     == 731
    assert rec.manifest_gid == "111"
    assert rec.timeupdated  == 1000


def test_manifest_record_load_drops_unknown_fields(tmp_path):
    """Forward compat: a future field on ManifestRecord just gets ignored
    when read by an older PatchForge build."""
    import json
    from src.core.archive import project as project_mod

    raw = {
        "schema_version": 1,
        "apps": [{
            "app_id": 730,
            "manifest_history": [{
                "buildid": "200", "branch": "public", "platform": "windows",
                "depot_id": 731, "depot_name": "csgo",
                "manifest_gid": "111", "timeupdated": 1000,
                "future_field": "ignored",
            }],
        }],
    }
    path = tmp_path / "p.xarchive"
    path.write_text(json.dumps(raw), encoding="utf-8")

    proj = project_mod.load(path)
    assert len(proj.apps[0].manifest_history) == 1
    rec = proj.apps[0].manifest_history[0]
    assert rec.depot_id == 731
    assert not hasattr(rec, "future_field")


def test_manifest_record_load_handles_missing_history():
    """Apps written by an older PatchForge that didn't have
    manifest_history must load with an empty list, not crash."""
    from src.core.archive.project import _load_app_entry

    entry = _load_app_entry({"app_id": 730, "current_buildid": "100"})
    assert entry.manifest_history == []


# ---------------------------------------------------------------------------
# runner.run_one_app — manifest_history append
# ---------------------------------------------------------------------------

def _stub_creds_no_notify():
    creds = SimpleNamespace(
        username="u", steam_id=1, client_refresh_token="t", web_api_key="",
        multiup    = SimpleNamespace(username="", password="", is_set=lambda: False),
        privatebin = SimpleNamespace(url="", password="", is_set=lambda: False),
        telegram   = SimpleNamespace(token="", chat_ids=[], is_set=lambda: False),
        discord    = SimpleNamespace(webhook_url="", mention_role_ids=[],
                                      is_set=lambda: False),
    )
    creds.has_login_tokens = lambda: True
    return creds


def _opts_default():
    from src.core.archive import project as project_mod
    return dict(
        workers=4, compression=5, archive_password="",
        volume_size="", language="english", max_retries=1,
        description="", max_concurrent_uploads=1, delete_archives=False,
        experimental=False, unstub=project_mod.UnstubOptions(),
        restart_delay=0, batch_size=0, force_download=False,
    )


def test_runner_appends_manifest_history(tmp_path):
    """A successful download must append one ManifestRecord per
    (platform, depot_id, manifest_gid) into entry.manifest_history."""
    from src.core.archive import project as project_mod
    from src.core.archive import runner as runner_mod

    proj = project_mod.new_project()
    proj.apps.append(project_mod.AppEntry(
        app_id=730, branch="public", current_buildid="100",
    ))

    download_app = MagicMock(return_value=(
        [tmp_path / "g.7z"],
        {"windows": [(731, "main", "GID-A"), (732, "wbins", "GID-B")]},
        {"appid": 730, "name": "Foo", "buildid": "200", "timeupdated": 5000},
    ))

    with patch("src.core.archive.download.download_app", download_app):
        runner_mod.run_session(
            client=MagicMock(), cdn=MagicMock(),
            project_obj=proj, project_path=tmp_path / "p.xarchive",
            creds=_stub_creds_no_notify(), output_dir=tmp_path,
            app_ids=[730], opts=_opts_default(),
            platform="windows", notify_mode="none",
            branch="public", crack=False,
            crack_identity=None, unstub_options=None,
            volume_size=None, depot_names={},
            subscriber=None,
            upload_mod=MagicMock(), notify_mod=MagicMock(),
            log=lambda m: None, warn=lambda m: None,
        )

    history = proj.apps[0].manifest_history
    assert len(history) == 2
    by_depot = {r.depot_id: r for r in history}
    assert by_depot[731].manifest_gid == "GID-A"
    assert by_depot[731].buildid      == "200"
    assert by_depot[731].branch       == "public"
    assert by_depot[731].platform     == "windows"
    assert by_depot[731].timeupdated  == 5000
    assert by_depot[732].manifest_gid == "GID-B"


def test_runner_dedups_repeat_manifest_history(tmp_path):
    """Re-running with the same buildid must NOT bloat manifest_history —
    dedup on (buildid, branch, platform, depot, gid)."""
    from src.core.archive import project as project_mod
    from src.core.archive import runner as runner_mod

    proj = project_mod.new_project()
    proj.apps.append(project_mod.AppEntry(
        app_id=730, branch="public", current_buildid="200",
    ))
    proj.apps[0].manifest_history.append(project_mod.ManifestRecord(
        buildid="200", branch="public", platform="windows",
        depot_id=731, depot_name="main", manifest_gid="GID-A",
    ))

    download_app = MagicMock(return_value=(
        [tmp_path / "g.7z"],
        {"windows": [(731, "main", "GID-A")]},   # identical to existing
        {"appid": 730, "name": "Foo", "buildid": "200", "timeupdated": 0},
    ))

    with patch("src.core.archive.download.download_app", download_app):
        runner_mod.run_session(
            client=MagicMock(), cdn=MagicMock(),
            project_obj=proj, project_path=tmp_path / "p.xarchive",
            creds=_stub_creds_no_notify(), output_dir=tmp_path,
            app_ids=[730], opts=_opts_default(),
            platform="windows", notify_mode="none",
            branch="public", crack=False,
            crack_identity=None, unstub_options=None,
            volume_size=None, depot_names={},
            subscriber=None,
            upload_mod=MagicMock(), notify_mod=MagicMock(),
            log=lambda m: None, warn=lambda m: None,
        )

    assert len(proj.apps[0].manifest_history) == 1


def test_runner_records_per_platform_under_platform_all(tmp_path):
    """When download_app returns dict with multiple platform keys
    (the --platform all path), each platform yields its own row.  The
    literal 'all' must never appear in stored records."""
    from src.core.archive import project as project_mod
    from src.core.archive import runner as runner_mod

    proj = project_mod.new_project()
    proj.apps.append(project_mod.AppEntry(
        app_id=730, branch="public", current_buildid="100",
    ))

    download_app = MagicMock(return_value=(
        [],
        {
            "windows": [(731, "shared", "GID-A"), (732, "wbins", "GID-B")],
            "linux":   [(731, "shared", "GID-A"), (733, "lbins", "GID-C")],
        },
        {"appid": 730, "name": "Foo", "buildid": "200", "timeupdated": 0},
    ))

    with patch("src.core.archive.download.download_app", download_app):
        runner_mod.run_session(
            client=MagicMock(), cdn=MagicMock(),
            project_obj=proj, project_path=tmp_path / "p.xarchive",
            creds=_stub_creds_no_notify(), output_dir=tmp_path,
            app_ids=[730], opts=_opts_default(),
            platform="all", notify_mode="none",
            branch="public", crack=False,
            crack_identity=None, unstub_options=None,
            volume_size=None, depot_names={},
            subscriber=None,
            upload_mod=MagicMock(), notify_mod=MagicMock(),
            log=lambda m: None, warn=lambda m: None,
        )

    history = proj.apps[0].manifest_history
    platforms = {r.platform for r in history}
    assert "all" not in platforms
    assert platforms == {"windows", "linux"}

    # Shared depot 731 appears under both windows AND linux — same
    # depot_id + gid, different platform, so two distinct rows.
    shared_rows = [r for r in history if r.depot_id == 731]
    assert len(shared_rows) == 2
    assert {r.platform for r in shared_rows} == {"windows", "linux"}

    # Total count: shared (2) + windows-only (1) + linux-only (1)
    assert len(history) == 4


def test_runner_records_current_buildid_timeupdated(tmp_path):
    """A successful download must stash app_meta.timeupdated into
    AppEntry.current_buildid_timeupdated."""
    from src.core.archive import project as project_mod
    from src.core.archive import runner as runner_mod

    proj = project_mod.new_project()
    proj.apps.append(project_mod.AppEntry(
        app_id=730, branch="public", current_buildid="100",
    ))

    download_app = MagicMock(return_value=(
        [], {"windows": [(731, "main", "GID")]},
        {"appid": 730, "name": "Foo", "buildid": "200",
         "timeupdated": 1700000000},
    ))

    with patch("src.core.archive.download.download_app", download_app):
        runner_mod.run_session(
            client=MagicMock(), cdn=MagicMock(),
            project_obj=proj, project_path=tmp_path / "p.xarchive",
            creds=_stub_creds_no_notify(), output_dir=tmp_path,
            app_ids=[730], opts=_opts_default(),
            platform="windows", notify_mode="none",
            branch="public", crack=False,
            crack_identity=None, unstub_options=None,
            volume_size=None, depot_names={},
            subscriber=None,
            upload_mod=MagicMock(), notify_mod=MagicMock(),
            log=lambda m: None, warn=lambda m: None,
        )

    assert proj.apps[0].current_buildid.timeupdated == 1700000000


def test_runner_shifts_timeupdated_alongside_buildid(tmp_path):
    """When current_buildid moves, previous_buildid.timeupdated must
    capture the previous current_buildid.timeupdated."""
    from src.core.archive import project as project_mod
    from src.core.archive import runner as runner_mod

    proj = project_mod.new_project()
    proj.apps.append(project_mod.AppEntry(
        app_id=730, branch="public",
        current_buildid=project_mod.BuildIdRecord(
            buildid="100", timeupdated=1600000000,
        ),
    ))

    download_app = MagicMock(return_value=(
        [], {"windows": [(731, "main", "GID")]},
        {"appid": 730, "name": "Foo", "buildid": "200",
         "timeupdated": 1700000000},
    ))

    with patch("src.core.archive.download.download_app", download_app):
        runner_mod.run_session(
            client=MagicMock(), cdn=MagicMock(),
            project_obj=proj, project_path=tmp_path / "p.xarchive",
            creds=_stub_creds_no_notify(), output_dir=tmp_path,
            app_ids=[730], opts=_opts_default(),
            platform="windows", notify_mode="none",
            branch="public", crack=False,
            crack_identity=None, unstub_options=None,
            volume_size=None, depot_names={},
            subscriber=None,
            upload_mod=MagicMock(), notify_mod=MagicMock(),
            log=lambda m: None, warn=lambda m: None,
        )

    assert proj.apps[0].current_buildid.buildid      == "200"
    assert proj.apps[0].current_buildid.timeupdated  == 1700000000
    assert proj.apps[0].previous_buildid.buildid     == "100"
    assert proj.apps[0].previous_buildid.timeupdated == 1600000000


def test_runner_preserves_previous_ts_on_force_redownload(tmp_path):
    """--force-download against an unchanged buildid must NOT shift
    previous_buildid.timeupdated — same guard as previous_buildid."""
    from src.core.archive import project as project_mod
    from src.core.archive import runner as runner_mod

    proj = project_mod.new_project()
    proj.apps.append(project_mod.AppEntry(
        app_id=730, branch="public",
        current_buildid=project_mod.BuildIdRecord(
            buildid="200", timeupdated=1700000000,
        ),
        previous_buildid=project_mod.BuildIdRecord(
            buildid="100", timeupdated=1600000000,
        ),
    ))

    download_app = MagicMock(return_value=(
        [], {"windows": [(731, "main", "GID")]},
        {"appid": 730, "name": "Foo", "buildid": "200",
         "timeupdated": 1700000000},
    ))

    with patch("src.core.archive.download.download_app", download_app):
        runner_mod.run_session(
            client=MagicMock(), cdn=MagicMock(),
            project_obj=proj, project_path=tmp_path / "p.xarchive",
            creds=_stub_creds_no_notify(), output_dir=tmp_path,
            app_ids=[730], opts=_opts_default(),
            platform="windows", notify_mode="none",
            branch="public", crack=False,
            crack_identity=None, unstub_options=None,
            volume_size=None, depot_names={},
            subscriber=None,
            upload_mod=MagicMock(), notify_mod=MagicMock(),
            log=lambda m: None, warn=lambda m: None,
        )

    assert proj.apps[0].previous_buildid.buildid     == "100"
    assert proj.apps[0].previous_buildid.timeupdated == 1600000000


def test_poll_first_seen_seeds_current_timeupdated(monkeypatch):
    """detect_changes first-seen seeding must record timeupdated alongside
    the buildid so users see when a freshly-added app was last built
    without needing to run a download."""
    from src.core.archive import poll as poll_mod
    from src.core.archive import project as project_mod

    fake_results = [
        (730, {"name": "Foo", "buildid": "200", "oslist": "windows",
               "timeupdated": 1700000000, "installdir": "Foo"}),
    ]
    monkeypatch.setattr(
        poll_mod, "query_app_info_batch",
        lambda *a, **kw: iter(fake_results),
    )

    entry = project_mod.AppEntry(app_id=730, current_buildid="")
    apps_by_id = {730: entry}
    changes = poll_mod.detect_changes(
        client=None, cdn=None, apps_by_id=apps_by_id,
    )
    assert changes == []  # silent seed
    assert entry.current_buildid.buildid      == "200"
    assert entry.current_buildid.timeupdated  == 1700000000


def test_appentry_timeupdated_roundtrip(tmp_path):
    """save() / load() preserves both nested BuildIdRecord fields."""
    from src.core.archive import project as project_mod

    proj = project_mod.new_project()
    proj.apps.append(project_mod.AppEntry(
        app_id=730,
        current_buildid=project_mod.BuildIdRecord(
            buildid="200", timeupdated=1700000000,
        ),
        previous_buildid=project_mod.BuildIdRecord(
            buildid="100", timeupdated=1600000000,
        ),
    ))
    path = tmp_path / "p.xarchive"
    project_mod.save(proj, path)

    loaded = project_mod.load(path)
    e = loaded.apps[0]
    assert e.current_buildid.buildid      == "200"
    assert e.current_buildid.timeupdated  == 1700000000
    assert e.previous_buildid.buildid     == "100"
    assert e.previous_buildid.timeupdated == 1600000000


def test_appentry_legacy_flat_format_loads(tmp_path):
    """Pre-nesting .xarchive files stored buildid as bare string +
    timeupdated as separate top-level field.  Loader must lift them into
    the new BuildIdRecord shape transparently."""
    import json
    from src.core.archive import project as project_mod

    raw = {
        "schema_version": 1,
        "apps": [{
            "app_id": 730,
            "current_buildid":  "200",
            "previous_buildid": "100",
            "current_buildid_timeupdated":  1700000000,
            "previous_buildid_timeupdated": 1600000000,
        }],
    }
    path = tmp_path / "p.xarchive"
    path.write_text(json.dumps(raw), encoding="utf-8")

    proj = project_mod.load(path)
    e = proj.apps[0]
    assert isinstance(e.current_buildid,  project_mod.BuildIdRecord)
    assert isinstance(e.previous_buildid, project_mod.BuildIdRecord)
    assert e.current_buildid.buildid      == "200"
    assert e.current_buildid.timeupdated  == 1700000000
    assert e.previous_buildid.buildid     == "100"
    assert e.previous_buildid.timeupdated == 1600000000


def test_runner_separates_branches_in_manifest_history(tmp_path):
    """Recording the same buildid on two different branches keeps both
    rows (dedup key includes branch)."""
    from src.core.archive import project as project_mod
    from src.core.archive import runner as runner_mod

    proj = project_mod.new_project()
    proj.apps.append(project_mod.AppEntry(
        app_id=730, branch="beta", current_buildid="200",
    ))
    proj.apps[0].manifest_history.append(project_mod.ManifestRecord(
        buildid="200", branch="public", platform="windows",
        depot_id=731, depot_name="main", manifest_gid="GID-A",
    ))

    download_app = MagicMock(return_value=(
        [],
        {"windows": [(731, "main", "GID-A")]},
        {"appid": 730, "name": "Foo", "buildid": "200", "timeupdated": 0},
    ))

    with patch("src.core.archive.download.download_app", download_app):
        runner_mod.run_session(
            client=MagicMock(), cdn=MagicMock(),
            project_obj=proj, project_path=tmp_path / "p.xarchive",
            creds=_stub_creds_no_notify(), output_dir=tmp_path,
            app_ids=[730], opts=_opts_default(),
            platform="windows", notify_mode="none",
            branch="beta", crack=False,
            crack_identity=None, unstub_options=None,
            volume_size=None, depot_names={},
            subscriber=None,
            upload_mod=MagicMock(), notify_mod=MagicMock(),
            log=lambda m: None, warn=lambda m: None,
        )

    history = proj.apps[0].manifest_history
    branches = {r.branch for r in history}
    assert branches == {"public", "beta"}
    assert len(history) == 2
