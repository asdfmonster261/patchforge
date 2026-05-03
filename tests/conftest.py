"""Shared fixtures + import-path setup for the PatchForge test suite.

Layout mirrors `src/`:

  core/engines/  — patch-engine wrappers (HDiffPatch, JojoDiff)
  core/archive/  — Steam archive-mode pipeline
  core/          — top-level patch + repack builders, exe packager, project I/O
  cli/           — CLI command handlers
  gui/           — PySide6 widgets + workers
  stub/          — pure-Python checks against the prebuilt C stubs
  integration/   — end-to-end smoke that crosses modules

`conftest.py` files at each level can add their own fixtures; this one
supplies project-wide essentials.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest


# ---------------------------------------------------------------------------
# Import path — tests use `from src.core...` so make `src/` resolvable.
# ---------------------------------------------------------------------------
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))


# ---------------------------------------------------------------------------
# GUI tests run headless — even outside CI.  Set this before any test file
# imports PySide6 so window creation succeeds without a real display.
# ---------------------------------------------------------------------------
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


# ---------------------------------------------------------------------------
# Project + credentials fixtures used by archive-mode pipeline tests
# ---------------------------------------------------------------------------

@pytest.fixture
def stub_creds():
    """Build a `Credentials`-shaped SimpleNamespace with toggleable
    notify channels.  Returns a factory so individual tests can ask
    for variants without redefining the fixture."""
    def _make(*, multiup: bool = False, telegram: bool = False, discord: bool = False):
        creds = SimpleNamespace(
            username="u", steam_id=1, client_refresh_token="t",
            web_api_key="",
            multiup=SimpleNamespace(
                username="x" if multiup else "",
                password="y" if multiup else "",
                is_set=lambda: multiup,
            ),
            privatebin=SimpleNamespace(url="", password="", is_set=lambda: False),
            telegram=SimpleNamespace(
                token="t" if telegram else "", chat_ids=["1"] if telegram else [],
                is_set=lambda: telegram,
            ),
            discord=SimpleNamespace(
                webhook_url="https://hook" if discord else "",
                mention_role_ids=[],
                is_set=lambda: discord,
            ),
        )
        creds.has_login_tokens = lambda: True
        return creds
    return _make


@pytest.fixture
def archive_project_factory():
    """Build an `ArchiveProject` with one or more apps pre-seeded.
    Used by tests that exercise runner / poll plumbing without
    rebuilding the full project bootstrap each time."""
    from src.core.archive import project as project_mod

    def _make(app_id: int = 730, current: str = "100", *, name: str = ""):
        p = project_mod.new_project(name=name)
        p.apps.append(project_mod.AppEntry(
            app_id=app_id, branch="public", current_buildid=current,
        ))
        return p
    return _make


@pytest.fixture
def archive_run_opts():
    """Build the `opts` dict that runner.run_session expects.  Exposed
    as a factory so individual tests can override single keys."""
    def _make(**overrides):
        from src.core.archive import project as project_mod
        base = dict(
            workers=4, compression=5, archive_password="",
            volume_size="", language="english", max_retries=1,
            description="", max_concurrent_uploads=1, delete_archives=False,
            experimental=False, unstub=project_mod.UnstubOptions(),
            restart_delay=0, batch_size=0, force_download=False,
        )
        base.update(overrides)
        return base
    return _make


# ---------------------------------------------------------------------------
# Qt application — single instance per session, recycled across GUI tests.
# ---------------------------------------------------------------------------

@pytest.fixture(scope="session")
def qapp():
    """Session-scoped QApplication.  Tests that need a Qt event loop
    request this fixture — building one fresh per-test is slow and
    occasionally double-frees C++ widgets across tests."""
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication(sys.argv)
    yield app
