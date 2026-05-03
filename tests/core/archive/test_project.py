"""archive.project — .xarchive JSON I/O, schema versioning, dataclass shapes."""
from __future__ import annotations

import json

import pytest


# ---------------------------------------------------------------------------
# Roundtrip + schema enforcement
# ---------------------------------------------------------------------------

def test_project_roundtrip(tmp_path):
    from src.core.archive import project as pm
    p = pm.new_project("test")
    p.apps.append(pm.AppEntry(app_id=730, current_buildid="12345"))
    p.apps.append(pm.AppEntry(app_id=570, branch="beta", platform="linux"))
    p.crack.steam_id    = 76561198000000000
    p.crack.username    = "alice"
    p.crack.listen_port = 47585

    out = tmp_path / "x.xarchive"
    pm.save(p, out)
    loaded = pm.load(out)
    assert loaded.name == "test"
    assert len(loaded.apps) == 2
    assert loaded.apps[0].app_id == 730
    assert loaded.apps[0].current_buildid.buildid == "12345"
    assert loaded.apps[1].branch == "beta"
    assert loaded.crack.steam_id == 76561198000000000
    assert loaded.crack.username == "alice"
    assert loaded.crack.listen_port == 47585
    # Default BBCode template is non-empty.
    assert "{APP_NAME}" in loaded.bbcode_template


def test_project_load_drops_unknown_fields(tmp_path):
    """Older PatchForge reading a newer .xarchive must ignore fields it
    doesn't know rather than crashing — same compat policy as
    credentials.json."""
    from src.core.archive import project as pm
    out = tmp_path / "x.xarchive"
    out.write_text(
        json.dumps({
            "schema_version": 1,
            "name": "x",
            "ghost_top_field": 1,
            "apps": [{"app_id": 1, "ghost_app_field": 1}],
            "crack": {"steam_id": 99, "ghost_crack_field": 1},
        }),
        encoding="utf-8",
    )
    p = pm.load(out)
    assert p.name == "x"
    assert p.apps[0].app_id == 1
    assert p.crack.steam_id == 99


def test_project_rejects_future_schema(tmp_path):
    """A schema_version newer than this build's MAX_SCHEMA must fail loud
    instead of silently mis-interpreting fields whose layout changed."""
    from src.core.archive import project as pm
    out = tmp_path / "x.xarchive"
    out.write_text(json.dumps({"schema_version": 9999}), encoding="utf-8")
    with pytest.raises(ValueError, match="newer PatchForge"):
        pm.load(out)


def test_project_default_template_loads():
    from src.core.archive import project as pm
    s = pm.default_bbcode_template()
    assert "{APP_NAME}" in s
    assert "{BUILDID}" in s


# ---------------------------------------------------------------------------
# Per-project polling knobs
# ---------------------------------------------------------------------------

def test_project_roundtrips_restart_delay_and_batch_size(tmp_path):
    from src.core.archive import project as pm
    p = pm.new_project("polling")
    p.restart_delay = 30
    p.batch_size    = 5
    out = tmp_path / "p.xarchive"
    pm.save(p, out)
    loaded = pm.load(out)
    assert loaded.restart_delay == 30
    assert loaded.batch_size    == 5


def test_project_default_restart_delay_is_zero():
    """A fresh project must default to single-pass mode (restart_delay=0)
    so accidentally enabling poll-mode requires an explicit setting."""
    from src.core.archive import project as pm
    p = pm.new_project()
    assert p.restart_delay == 0
    assert p.batch_size    == 0


# ---------------------------------------------------------------------------
# Notify mode + run-time options
# ---------------------------------------------------------------------------

def test_project_notify_mode_field_roundtrips(tmp_path):
    from src.core.archive import project as pm
    p = pm.new_project("nm")
    p.notify_mode = "both"
    out = tmp_path / "p.xarchive"
    pm.save(p, out)
    assert pm.load(out).notify_mode == "both"


def test_project_run_time_knobs_roundtrip(tmp_path):
    """compression / language / max_concurrent_uploads / etc are all
    persisted through the .xarchive — stored as a single block so the
    GUI and CLI settings stay aligned across runs."""
    from src.core.archive import project as pm
    p = pm.new_project("rt")
    p.workers                = 4
    p.compression            = 9
    p.archive_password       = "hunter2"
    p.volume_size            = "1g"
    p.language               = "english"
    p.max_retries            = 3
    p.upload_description     = "test"
    p.max_concurrent_uploads = 2
    p.delete_archives        = True
    p.experimental           = True
    out = tmp_path / "p.xarchive"
    pm.save(p, out)
    loaded = pm.load(out)
    assert loaded.workers                == 4
    assert loaded.compression            == 9
    assert loaded.archive_password       == "hunter2"
    assert loaded.volume_size            == "1g"
    assert loaded.language               == "english"
    assert loaded.max_retries            == 3
    assert loaded.upload_description     == "test"
    assert loaded.max_concurrent_uploads == 2
    assert loaded.delete_archives        is True
    assert loaded.experimental           is True


# ---------------------------------------------------------------------------
# Hardening — credential leakage guard
# ---------------------------------------------------------------------------

def test_archive_project_does_not_carry_credentials():
    """ArchiveProject must not declare any field that smells like a
    credential.  Steam tokens / API keys / passwords belong in
    archive_credentials.json (chmod 600) — having them in the project
    file lets users accidentally check them into VCS or share them in a
    .xarchive screenshot.  Catches future drift if someone forgets the
    per-project / global split."""
    from src.core.archive import project as pm
    forbidden = {"client_refresh_token", "refresh_token", "access_token",
                 "web_api_key", "api_key", "password"}
    fields = set(pm.ArchiveProject.__dataclass_fields__)
    fields |= set(pm.CrackIdentity.__dataclass_fields__)
    fields |= set(pm.AppEntry.__dataclass_fields__)
    leaks = forbidden.intersection(fields)
    # branch_password is allowed (Steam beta-branch passwords are not
    # user-account credentials and were per-project in SteamArchiver too).
    leaks.discard("branch_password")
    assert not leaks, f"ArchiveProject contains credential-like fields: {leaks}"


# ---------------------------------------------------------------------------
# AppEntry.previous_buildid + BuildIdRecord shape
# ---------------------------------------------------------------------------

def test_app_entry_previous_buildid_roundtrips(tmp_path):
    from src.core.archive import project as pm
    proj = pm.new_project(name="t")
    proj.apps.append(pm.AppEntry(
        app_id=1, current_buildid="100", previous_buildid="50",
    ))
    path = tmp_path / "p.xarchive"
    pm.save(proj, path)
    loaded = pm.load(path)
    assert loaded.apps[0].previous_buildid.buildid == "50"
    assert loaded.apps[0].current_buildid.buildid  == "100"


def test_app_entry_previous_buildid_default_blank():
    from src.core.archive.project import AppEntry
    e = AppEntry(app_id=1, current_buildid="123")
    assert e.previous_buildid.buildid == ""


def test_archive_project_persists_crack_mode_field(tmp_path):
    """ArchiveProject.crack_mode round-trips through save/load —
    saved per-project so users don't need to re-pick coldclient/gse
    every run."""
    from src.core.archive import project as project_mod
    proj = project_mod.new_project(name="t")
    proj.crack_mode = "coldclient"
    target = tmp_path / "p.xarchive"
    project_mod.save(proj, target)
    loaded = project_mod.load(target)
    assert loaded.crack_mode == "coldclient"
