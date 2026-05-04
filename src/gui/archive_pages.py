"""Archive-mode sub-pages — placeholder skeleton.

Phase 6 task #22 wires the sidebar nav.  Tasks #23–#26 fill in real
forms here:
  - apps   → table editor for project.apps
  - crack  → form for project.crack (CrackIdentity)
  - bbcode → split-view editor for project.bbcode_template
  - run    → form for run-time knobs (workers, compression, …)
  - poll   → form for restart_delay + batch_size
  - creds  → opens the Credentials dialog (separate file)
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtWidgets import (
    QHBoxLayout, QLabel, QVBoxLayout, QWidget,
)

if TYPE_CHECKING:
    from .archive_panel import ArchivePanel


class ArchivePageBase(QWidget):
    """Common protocol for sub-pages.  `refresh()` repopulates from
    panel.project(); `flush()` writes widget values back into project
    fields just before save."""

    def __init__(self, panel: "ArchivePanel"):
        super().__init__()
        self._panel = panel

    def refresh(self) -> None: ...   # noqa: D401  (default no-op)
    def flush(self)   -> None: ...


class _PlaceholderPage(ArchivePageBase):
    def __init__(self, panel: "ArchivePanel", label: str):
        super().__init__(panel)
        layout = QVBoxLayout(self)
        layout.addStretch(1)
        row = QHBoxLayout()
        row.addStretch(1)
        msg = QLabel(f"{label} — coming up next phase task")
        msg.setObjectName("dim")
        row.addWidget(msg)
        row.addStretch(1)
        layout.addLayout(row)
        layout.addStretch(1)


def build_page(key: str, *, panel: "ArchivePanel") -> ArchivePageBase:
    """Factory — returns the right page widget for a sidebar key."""
    if key == "apps":
        from .archive_pages_apps import AppsPage
        return AppsPage(panel)
    if key == "crack":
        from .archive_pages_simple import CrackIdentityPage
        return CrackIdentityPage(panel)
    if key == "run":
        from .archive_pages_simple import RunOptionsPage
        return RunOptionsPage(panel)
    if key == "poll":
        from .archive_pages_simple import PollingPage
        return PollingPage(panel)
    if key == "bbcode":
        from .archive_pages_bbcode import BBCodePage
        return BBCodePage(panel)
    if key == "history":
        from .archive_pages_history import ManifestHistoryPage
        return ManifestHistoryPage(panel)
    if key == "creds":
        from .archive_pages_creds import CredentialsPage
        return CredentialsPage(panel)
    return _PlaceholderPage(panel, key)


__all__ = ["ArchivePageBase", "build_page"]
