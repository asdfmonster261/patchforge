"""Live-run view shown while ArchiveWorker is executing.

Subscribes to ArchiveWorker's signals and renders:
  - Stage timeline strip (Poll → Download → Compress → Crack → Upload
    → Paste → Notify dots, mirroring the mockup)
  - Aggregate progress bar
  - Per-file progress list (capped, scrollable)
  - Upload queue list (status + paste URL on completion)
  - Polling countdown widget (M:SS / Ns)
  - Log panel (timestamped, color-coded by level)
  - Stop button → ArchiveWorker.request_abort()
"""
from __future__ import annotations

import time
from datetime import datetime

from PySide6.QtCore import Qt
from PySide6.QtGui import QTextCursor
from PySide6.QtWidgets import (
    QFrame, QGroupBox, QHBoxLayout, QLabel, QListWidget, QListWidgetItem,
    QPlainTextEdit, QProgressBar, QPushButton, QSplitter, QVBoxLayout,
    QWidget,
)

from .theme import ACCENT, BORDER, ERROR, SUCCESS, TEXT_DIM, WARN


# ── Stage strip ───────────────────────────────────────────────────────────

STAGES = ["Poll", "Download", "Compress", "Crack", "Upload", "Paste", "Notify"]

_KIND_TO_STAGE = {
    "file_started":       "Download",
    "file_progress":      "Download",
    "file_finished":      "Download",
    "file_skipped":       "Download",
    "compress_started":   "Compress",
    "compress_progress":  "Compress",
    "compress_finished":  "Compress",
    "crack_started":      "Crack",
    "crack_finished":     "Crack",
    "upload_started":     "Upload",
    "upload_progress":    "Upload",
    "upload_finished":    "Upload",
    "paste_created":      "Paste",
}


class StageStrip(QWidget):
    """Horizontal row of stage dots.  Dots colour-cycle:
       grey (idle) → blue (active) → green (done)."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._dots: dict[str, QLabel] = {}
        self._state: dict[str, str] = {s: "idle" for s in STAGES}
        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 6, 8, 6)
        for i, name in enumerate(STAGES):
            col = QVBoxLayout()
            dot = QLabel("●")
            dot.setAlignment(Qt.AlignCenter)
            dot.setStyleSheet(f"color: {TEXT_DIM}; font-size: 16px;")
            label = QLabel(name)
            label.setAlignment(Qt.AlignCenter)
            label.setObjectName("dim")
            col.addWidget(dot)
            col.addWidget(label)
            layout.addLayout(col)
            if i < len(STAGES) - 1:
                sep = QFrame()
                sep.setFrameShape(QFrame.HLine)
                sep.setStyleSheet(f"background: {BORDER};")
                sep.setFixedHeight(2)
                layout.addWidget(sep, 1)
            self._dots[name] = dot

    def set_state(self, stage: str, state: str) -> None:
        if stage not in self._dots:
            return
        self._state[stage] = state
        col = {"idle": TEXT_DIM, "active": ACCENT, "done": SUCCESS}.get(state, TEXT_DIM)
        self._dots[stage].setStyleSheet(f"color: {col}; font-size: 16px;")

    def mark_active(self, stage: str) -> None:
        # Mark all earlier stages done, current active, later idle.
        try:
            idx = STAGES.index(stage)
        except ValueError:
            return
        for i, name in enumerate(STAGES):
            if i < idx:
                self.set_state(name, "done")
            elif i == idx:
                self.set_state(name, "active")
            else:
                self.set_state(name, "idle")


# ── Live-run view ─────────────────────────────────────────────────────────

class ArchiveRunView(QWidget):
    """Embed in ArchivePanel below the editor when a run is active."""

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        # ── stage strip ───────────────────────────────────────────
        self.stage_strip = StageStrip()
        layout.addWidget(self.stage_strip)

        split = QSplitter(Qt.Horizontal)

        # left: per-file progress + aggregate
        left = QWidget()
        lleft = QVBoxLayout(left)
        lleft.setContentsMargins(0, 0, 0, 0)

        self.files_grp = QGroupBox("Active downloads")
        fg = QVBoxLayout(self.files_grp)
        self.files_list = QListWidget()
        self.files_list.setStyleSheet("QListWidget::item { padding: 2px; }")
        fg.addWidget(self.files_list)
        lleft.addWidget(self.files_grp, 1)

        self.agg_grp = QGroupBox("Overall")
        ag = QVBoxLayout(self.agg_grp)
        self.agg_label = QLabel("(idle)")
        self.agg_bar   = QProgressBar()
        self.agg_bar.setRange(0, 1000)
        self.agg_bar.setValue(0)
        self.agg_bar.setFormat("")
        ag.addWidget(self.agg_label)
        ag.addWidget(self.agg_bar)
        lleft.addWidget(self.agg_grp)

        split.addWidget(left)

        # right: upload queue + countdown + log
        right = QWidget()
        rright = QVBoxLayout(right)
        rright.setContentsMargins(0, 0, 0, 0)

        self.upload_grp = QGroupBox("Upload queue")
        ug = QVBoxLayout(self.upload_grp)
        self.upload_list = QListWidget()
        ug.addWidget(self.upload_list)
        rright.addWidget(self.upload_grp, 1)

        self.poll_grp = QGroupBox("Polling")
        pg = QVBoxLayout(self.poll_grp)
        self.countdown_label = QLabel("idle")
        self.countdown_label.setAlignment(Qt.AlignCenter)
        self.countdown_label.setStyleSheet(
            f"color: {ACCENT}; font-family: 'Cascadia Mono', monospace; font-size: 24px;"
        )
        pg.addWidget(self.countdown_label)
        rright.addWidget(self.poll_grp)

        self.log_grp = QGroupBox("Log")
        lg = QVBoxLayout(self.log_grp)
        self.log_view = QPlainTextEdit()
        self.log_view.setReadOnly(True)
        self.log_view.setMaximumBlockCount(2000)
        lg.addWidget(self.log_view)
        rright.addWidget(self.log_grp, 1)

        split.addWidget(right)
        split.setStretchFactor(0, 6)
        split.setStretchFactor(1, 5)
        layout.addWidget(split, 1)

        # bottom row: stop button + summary
        bottom = QHBoxLayout()
        self.summary_label = QLabel("idle")
        self.summary_label.setObjectName("dim")
        bottom.addWidget(self.summary_label, 1)
        self.btn_stop = QPushButton("Stop")
        self.btn_stop.setEnabled(False)
        bottom.addWidget(self.btn_stop)
        layout.addLayout(bottom)

        self._file_rows: dict[str, QListWidgetItem] = {}
        self._upload_rows: dict[str, QListWidgetItem] = {}
        self._agg_total = 0
        self._agg_done  = 0
        self._run_start = 0.0

    # ---------------------------------------------------------- lifecycle
    def attach(self, worker) -> None:
        """Connect signals from an ArchiveWorker.  Reset UI state first
        so re-runs in the same panel start clean."""
        self._reset()
        worker.event.connect(self._on_event)
        worker.log_line.connect(self._on_log)
        worker.countdown_tick.connect(self._on_countdown)
        worker.started.connect(self._on_started)
        worker.finished.connect(self._on_finished)
        worker.failed.connect(self._on_failed)
        self.btn_stop.clicked.connect(worker.request_abort)
        self.btn_stop.setEnabled(True)

    def _reset(self) -> None:
        for s in STAGES:
            self.stage_strip.set_state(s, "idle")
        self.files_list.clear()
        self.upload_list.clear()
        self._file_rows.clear()
        self._upload_rows.clear()
        self._agg_total = 0
        self._agg_done  = 0
        self.agg_bar.setValue(0)
        self.agg_label.setText("(starting…)")
        self.countdown_label.setText("running")
        self.log_view.clear()
        self.summary_label.setText("running…")

    # ---------------------------------------------------------- slots
    def _on_started(self) -> None:
        self._run_start = time.monotonic()
        self.stage_strip.mark_active("Poll")
        self._on_log("--- run started ---", "info")

    def _on_finished(self, result) -> None:
        self.btn_stop.setEnabled(False)
        elapsed = int(time.monotonic() - self._run_start)
        n = len(getattr(result, "archives", []) or [])
        unk = len(getattr(result, "unknown_depot_ids", []) or [])
        self.summary_label.setText(
            f"done — {n} archive(s)" + (f" • {unk} unknown depot ID(s)" if unk else "")
            + f" • {elapsed}s"
        )
        for s in STAGES:
            if self.stage_strip._state.get(s) == "active":
                self.stage_strip.set_state(s, "done")
        self.countdown_label.setText("done")

    def _on_failed(self, msg: str) -> None:
        self.btn_stop.setEnabled(False)
        self.summary_label.setText(f"failed: {msg}")
        self._on_log(f"FAILED: {msg}", "error")

    def _on_log(self, msg: str, level: str = "info") -> None:
        ts = datetime.now().strftime("%H:%M:%S")
        col = {"warn": WARN, "error": ERROR}.get(level, "")
        if col:
            self.log_view.appendHtml(
                f"<span style='color:{TEXT_DIM}'>{ts}</span> "
                f"<span style='color:{col}'>{_html_escape(msg)}</span>"
            )
        else:
            self.log_view.appendPlainText(f"{ts}  {msg}")
        self.log_view.moveCursor(QTextCursor.End)

    def _on_countdown(self, seconds: int) -> None:
        if seconds <= 0:
            self.countdown_label.setText("running")
            return
        mins, secs = divmod(seconds, 60)
        self.countdown_label.setText(
            f"{mins}:{secs:02d}" if mins else f"{secs}s"
        )

    # ---------------------------------------------------------- DownloadEvent
    def _on_event(self, ev) -> None:
        kind = ev.kind
        stage = _KIND_TO_STAGE.get(kind)
        if stage:
            self.stage_strip.mark_active(stage)

        if kind == "stage":
            self.summary_label.setText(ev.stage_msg or "")
            self._on_log(ev.stage_msg or "", "info")
            return

        if kind == "error":
            self._on_log(f"{ev.name or '-'}: {ev.error_msg}", "error")
            return

        if kind in ("file_started", "compress_started"):
            self._update_file_row(ev.name, 0, ev.total or 0)
            return

        if kind in ("file_progress", "compress_progress"):
            self._update_file_row(ev.name, ev.done, ev.total)
            return

        if kind in ("file_finished", "compress_finished"):
            self._update_file_row(ev.name, ev.total, ev.total, complete=True)
            self._agg_done  += ev.total
            self._agg_total = max(self._agg_total, self._agg_done)
            self._refresh_agg()
            return

        if kind == "file_skipped":
            self._update_file_row(ev.name, 0, 0, complete=True, label="(skipped)")
            return

        if kind == "upload_started":
            self._update_upload_row(ev.name, 0, ev.total or 0, "uploading")
            return

        if kind == "upload_progress":
            self._update_upload_row(ev.name, ev.done, ev.total, "uploading")
            return

        if kind == "upload_finished":
            self._update_upload_row(ev.name, ev.total, ev.total, "uploaded")
            return

        if kind == "paste_created":
            self._update_upload_row(ev.name, 0, 0, ev.stage_msg or "paste ready")
            return

        if kind in ("crack_started", "crack_finished"):
            return

    # ---------------------------------------------------------- row helpers
    def _update_file_row(self, name: str, done: int, total: int, *,
                         complete: bool = False, label: str = "") -> None:
        item = self._file_rows.get(name)
        if item is None:
            item = QListWidgetItem()
            self.files_list.addItem(item)
            self._file_rows[name] = item
        pct = int(100 * done / total) if total > 0 else (100 if complete else 0)
        suffix = label or (
            f"{_fmt(done)} / {_fmt(total)}" if total else "")
        item.setText(f"{name:<60} {pct:>3}%  {suffix}")
        if complete and len(self._file_rows) > 100:
            # garbage-collect old finished rows so the list stays bounded
            stale = [k for k, v in self._file_rows.items() if v.text().endswith("100%")]
            for k in stale[:50]:
                self.files_list.takeItem(self.files_list.row(self._file_rows[k]))
                del self._file_rows[k]

    def _update_upload_row(self, name: str, done: int, total: int,
                           state: str) -> None:
        item = self._upload_rows.get(name)
        if item is None:
            item = QListWidgetItem()
            self.upload_list.addItem(item)
            self._upload_rows[name] = item
        if total > 0:
            pct = int(100 * done / total)
            item.setText(f"{name:<60} {pct:>3}%  {state}")
        else:
            item.setText(f"{name:<60}        {state}")

    def _refresh_agg(self) -> None:
        if self._agg_total <= 0:
            self.agg_bar.setValue(0)
            return
        frac = self._agg_done / self._agg_total
        self.agg_bar.setValue(int(frac * 1000))
        self.agg_label.setText(
            f"{int(frac * 100)}%  •  {_fmt(self._agg_done)} / {_fmt(self._agg_total)}"
        )


# ── helpers ────────────────────────────────────────────────────────────

def _fmt(n: int) -> str:
    """Compact byte count rendering (no external dep)."""
    units = ("B", "KB", "MB", "GB", "TB")
    n = float(n)
    for u in units:
        if n < 1024 or u == units[-1]:
            return f"{n:.1f}{u}" if u != "B" else f"{int(n)}B"
        n /= 1024
    return f"{n:.1f}TB"


def _html_escape(s: str) -> str:
    return (s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


__all__ = ["ArchiveRunView", "StageStrip"]
