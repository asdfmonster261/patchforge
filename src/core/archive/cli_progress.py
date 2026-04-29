"""CLI display layer for archive-mode download progress.

Subscribes to DownloadEvent objects emitted by download.download_app() and
renders them as a multi-line live display ported from SteamArchiver, or as
a structured one-line-per-event log (--no-progress) for CI / log capture.

The same event stream feeds Phase 6's GUI via Qt signals — this module is
purely a CLI implementation detail.
"""

from __future__ import annotations

import os
import sys
import time
from collections import deque


# ---------------------------------------------------------------------------
# ANSI codes
# ---------------------------------------------------------------------------

_TTY = hasattr(sys.stdout, "isatty") and sys.stdout.isatty()


def _c(*codes: int) -> str:
    return f"\033[{';'.join(map(str, codes))}m" if _TTY else ""


_RESET  = _c(0)
_BOLD   = _c(1)
_DIM    = _c(2)
_GREEN  = _c(32)
_CYAN   = _c(36)


# ---------------------------------------------------------------------------
# Live multi-line display (default subscriber, ported from SteamArchiver)
# ---------------------------------------------------------------------------

class LiveDisplaySubscriber:
    """Multi-line ANSI live display: per-active-file lines + aggregate footer.

    Spawns a gevent greenlet that redraws every _REFRESH seconds.  Per-file
    speed comes from a rolling window of (timestamp, bytes_done) samples.

    Falls back to a single-line aggregate summary when stdout is not a TTY.
    """

    _WINDOW  = 8.0    # rolling speed window in seconds
    _REFRESH = 0.25   # redraw interval in seconds

    def __init__(self):
        self._files: dict[str, dict] = {}
        self._downloaded = 0
        self._uploaded   = 0
        self._skipped    = 0
        self._prev_lines = 0
        self._greenlet   = None
        self._closed     = False
        # "download" vs "upload" toggles the per-file dict semantics and the
        # footer label.  upload_started flips to "upload"; the next
        # download (rare, but possible if a future caller chains pipelines)
        # would need to flip back explicitly.
        self._phase: str = "download"
        # Compression state.  Set by compress_started; cleared by
        # compress_finished.  While set, _redraw renders a single-line
        # "Compressing X% [bar]" status instead of per-file download rows.
        self._compress_name: str | None = None
        self._compress_pct:  int        = 0
        # Crack-phase quiet flag.  The crack step prints diagnostics
        # directly to stdout (achievement fetch, DLL processing, prompts);
        # the redraw greenlet must stay out of its way or the cursor-up
        # math fights the prints and the divider line accumulates.
        self._crack_active: bool = False

    def __call__(self, ev) -> None:
        kind = ev.kind
        if kind == "file_started":
            self._ensure_started()
            self._files[ev.name] = {
                "total":   ev.total,
                "done":    0,
                "samples": deque(),
                "active":  True,
            }

        elif kind == "file_progress":
            f = self._files.get(ev.name)
            if f is None:
                return
            delta = ev.done - f["done"]
            if delta < 0:
                delta = 0
            f["done"] = ev.done
            self._downloaded += delta
            now = time.monotonic()
            f["samples"].append((now, f["done"]))
            cutoff = now - self._WINDOW
            while f["samples"] and f["samples"][0][0] < cutoff:
                f["samples"].popleft()

        elif kind == "file_finished":
            f = self._files.get(ev.name)
            if f is not None:
                f["active"] = False
                # Catch up any final bytes the progress events missed.
                missed = ev.done - f["done"]
                if missed > 0:
                    f["done"] = ev.done
                    self._downloaded += missed

        elif kind == "file_skipped":
            self._skipped += ev.total

        elif kind == "stage":
            self._erase()
            sys.stdout.write(f"\n{_BOLD} >  {_RESET}{ev.stage_msg}\n")
            sys.stdout.flush()

        elif kind == "error":
            self._erase()
            target = f"  ({ev.name})" if ev.name else ""
            sys.stdout.write(f"{_BOLD}!  {_RESET}{ev.error_msg}{target}\n")
            sys.stdout.flush()

        elif kind == "compress_started":
            # Transition from download phase to compression phase.  Drop the
            # per-file download rows so the live block doesn't keep
            # redrawing stale "0 active 999MB downloaded" between stages.
            self._erase()
            self._files.clear()
            self._compress_name = ev.name
            self._compress_pct  = 0
            self._ensure_started()

        elif kind == "compress_progress":
            if self._compress_name is not None:
                self._compress_pct = int(ev.done)

        elif kind == "compress_finished":
            self._erase()
            self._compress_name = None
            self._compress_pct  = 0

        elif kind == "crack_started":
            # Crack uses print() directly — drop our footer and stop
            # redrawing until crack_finished so we don't fight its output.
            self._erase()
            self._files.clear()
            self._crack_active = True

        elif kind == "crack_finished":
            self._crack_active = False

        elif kind == "upload_started":
            # First upload event after compress_finished — switch phase and
            # start tracking the new per-file rows in self._files.
            if self._phase != "upload":
                self._erase()
                self._files.clear()
                self._phase = "upload"
            self._ensure_started()
            self._files[ev.name] = {
                "total":   ev.total,
                "done":    0,
                "samples": deque(),
                "active":  True,
            }

        elif kind == "upload_progress":
            f = self._files.get(ev.name)
            if f is None:
                return
            delta = ev.done - f["done"]
            if delta < 0:
                delta = 0
            f["done"] = ev.done
            self._uploaded += delta
            now = time.monotonic()
            f["samples"].append((now, f["done"]))
            cutoff = now - self._WINDOW
            while f["samples"] and f["samples"][0][0] < cutoff:
                f["samples"].popleft()

        elif kind == "upload_finished":
            f = self._files.get(ev.name)
            if f is not None:
                f["active"] = False
                missed = ev.done - f["done"]
                if missed > 0:
                    f["done"] = ev.done
                    self._uploaded += missed

        elif kind == "paste_created":
            self._erase()
            sys.stdout.write(
                f"  {_BOLD}[paste]{_RESET} {ev.name}  →  {ev.stage_msg}\n"
            )
            sys.stdout.flush()

    # ------------------------------------------------------------------

    def _ensure_started(self) -> None:
        if self._greenlet is not None or self._closed:
            return
        try:
            import gevent
        except ImportError:
            return
        # Disable terminal auto-wrap for the duration of the live block.
        # If the line-width math is slightly off, this truncates instead of
        # wrapping — wrapped lines break the cursor-up redraw math and make
        # each refresh append below the previous block instead of replacing it.
        if _TTY:
            sys.stdout.write("\033[?7l")
            sys.stdout.flush()
        self._greenlet = gevent.spawn(self._loop)

    def _loop(self) -> None:
        import gevent
        try:
            while True:
                gevent.sleep(self._REFRESH)
                self._redraw()
        except gevent.GreenletExit:
            return

    def _phase_counters(self) -> tuple[int, str]:
        """Return (cumulative-bytes, footer-label) for the current phase."""
        if self._phase == "upload":
            return self._uploaded, "uploaded"
        return self._downloaded, "downloaded"

    def _file_speed(self, samples: deque) -> float:
        if len(samples) < 2:
            return 0.0
        t0, b0 = samples[0]
        t1, b1 = samples[-1]
        dt = t1 - t0
        return (b1 - b0) / dt if dt > 0 else 0.0

    def _erase(self) -> None:
        if not _TTY:
            return
        if self._prev_lines:
            sys.stdout.write(f"\033[{self._prev_lines}A\033[J")
            sys.stdout.flush()
            self._prev_lines = 0

    def _redraw(self) -> None:
        if self._crack_active:
            return
        if self._compress_name is not None:
            self._redraw_compression()
            return

        active = [(n, f) for n, f in self._files.items() if f["active"]]
        total_speed = sum(self._file_speed(f["samples"]) for _, f in active)

        moved_bytes, moved_label = self._phase_counters()

        if not _TTY:
            # Single-line summary, overwriting itself with \r.
            summary = (
                f"\r  {len(active)} active   "
                f"{_fmt_size(moved_bytes)} {moved_label}   "
                f"{_fmt_size(self._skipped)} skipped   "
                f"{_fmt_size(total_speed)}/s"
            )
            sys.stdout.write(summary)
            sys.stdout.flush()
            return

        try:
            width = os.get_terminal_size().columns
        except OSError:
            width = 100

        # Per-file row visible budget:
        #   2 + name_w + 2 + 8 + 3 + 8 + 2 + 8 + 2 + 2 + 5 + 1
        # = name_w + 41.  Leave 4 cols of slack so wider _fmt_size strings
        # (e.g. "1023.9 GB" = 9 chars under :>8) never push us past the
        # terminal width.  If a line wraps the cursor-up math undershoots
        # and every redraw appends instead of overwriting.
        name_w = max(20, width - 50)
        lines: list[str] = []

        for name, f in active:
            speed = self._file_speed(f["samples"])
            done  = f["done"]
            total = f["total"]
            pct   = done / total * 100 if total else 100.0
            disp  = name if len(name) <= name_w else "..." + name[-(name_w - 3):]
            pct_col = _GREEN if pct >= 100.0 else _CYAN
            lines.append(
                f"  {_DIM}{disp:<{name_w}}{_RESET}  "
                f"{_fmt_size(done):>9} / {_fmt_size(total):<9}  "
                f"{_CYAN}{_fmt_size(speed):>9}/s{_RESET}  "
                f"{pct_col}{pct:5.1f}%{_RESET}"
            )

        lines.append(f"  {_DIM}{'-' * min(width - 4, 74)}{_RESET}")
        lines.append(
            f"  {_BOLD}{len(active)} active{_RESET}   "
            f"{_GREEN}{_fmt_size(moved_bytes)} {moved_label}{_RESET}   "
            f"{_fmt_size(self._skipped)} skipped   "
            f"{_CYAN}{_fmt_size(total_speed)}/s{_RESET}"
        )

        out = (f"\033[{self._prev_lines}A\033[J" if self._prev_lines else "") \
              + "\n".join(lines) + "\n"
        sys.stdout.write(out)
        sys.stdout.flush()
        self._prev_lines = len(lines)

    def _redraw_compression(self) -> None:
        name = self._compress_name or ""
        pct  = max(0, min(100, self._compress_pct))

        if not _TTY:
            sys.stdout.write(f"\r  Compressing {name}  {pct:>3}%")
            sys.stdout.flush()
            return

        try:
            width = os.get_terminal_size().columns
        except OSError:
            width = 100

        bar_w = max(20, min(40, width // 3))
        name_w = max(20, width - bar_w - 14)
        disp = name if len(name) <= name_w else "..." + name[-(name_w - 3):]
        filled = int(round(pct / 100 * bar_w))
        bar = "#" * filled + "-" * (bar_w - filled)

        line = (
            f"  {_DIM}{disp:<{name_w}}{_RESET}  "
            f"{_CYAN}[{bar}]{_RESET}  "
            f"{_BOLD}{pct:>3}%{_RESET}"
        )
        out = (f"\033[{self._prev_lines}A\033[J" if self._prev_lines else "") \
              + line + "\n"
        sys.stdout.write(out)
        sys.stdout.flush()
        self._prev_lines = 1

    def close(self) -> None:
        self._closed = True
        if self._greenlet is not None:
            try:
                self._greenlet.kill()
            except Exception:
                pass
            self._greenlet = None
        self._erase()
        # Re-enable terminal auto-wrap for any subsequent output.
        if _TTY:
            sys.stdout.write("\033[?7h")
            sys.stdout.flush()
        # Final summary line so the user sees the total without scrolling.
        parts: list[str] = []
        if self._downloaded:
            parts.append(f"{_GREEN}{_fmt_size(self._downloaded)} downloaded{_RESET}")
        if self._uploaded:
            parts.append(f"{_GREEN}{_fmt_size(self._uploaded)} uploaded{_RESET}")
        if self._skipped:
            parts.append(f"{_fmt_size(self._skipped)} skipped")
        if parts:
            sys.stdout.write("  " + "   ".join(parts) + "\n")
            sys.stdout.flush()


# ---------------------------------------------------------------------------
# Plain log subscriber (--no-progress, non-TTY fallback)
# ---------------------------------------------------------------------------

class PlainLogSubscriber:
    """Structured one-line-per-event log mode.

    Suitable for CI, log file capture, or any non-TTY context where ANSI
    cursor moves don't survive.
    """

    def __init__(self, file=None):
        self._file = file or sys.stdout
        self._started: dict[str, float] = {}

    def __call__(self, ev) -> None:
        kind = ev.kind
        if kind == "file_started":
            self._started[ev.name] = time.monotonic()
            self._write(f"[start]    {ev.name} ({_fmt_size(ev.total)})")

        elif kind == "file_finished":
            t0 = self._started.pop(ev.name, time.monotonic())
            elapsed = max(time.monotonic() - t0, 0.001)
            speed = ev.done / elapsed
            self._write(
                f"[done]     {ev.name} "
                f"({_fmt_size(ev.done)} in {elapsed:.1f}s, "
                f"{_fmt_size(speed)}/s)"
            )

        elif kind == "file_skipped":
            self._write(f"[skip]     {ev.name} ({_fmt_size(ev.total)} already present)")

        elif kind == "stage":
            self._write(f"[stage]    {ev.stage_msg}")

        elif kind == "error":
            target = f" ({ev.name})" if ev.name else ""
            self._write(f"[error]   {ev.error_msg}{target}")

        # file_progress is intentionally dropped — too noisy for a log.

    def close(self) -> None:
        try:
            self._file.flush()
        except Exception:
            pass

    def _write(self, line: str) -> None:
        print(line, file=self._file, flush=True)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _fmt_size(n: float) -> str:
    n = float(n)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024:
            return f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} PB"


# ---------------------------------------------------------------------------
# Factory used by the CLI
# ---------------------------------------------------------------------------

def build_subscriber(plain: bool = False):
    """Return an event subscriber appropriate for the current TTY/flag state.

    Always falls back to PlainLogSubscriber when stdout is not a TTY, even
    if plain=False was requested — ANSI cursor moves don't survive a pipe
    or log capture.
    """
    if plain or not sys.stdout.isatty():
        return PlainLogSubscriber()
    return LiveDisplaySubscriber()
