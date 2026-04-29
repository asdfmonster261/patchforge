"""CLI display layer for archive-mode download progress.

Subscribes to DownloadEvent objects emitted by download.download_app() and
renders them either as a tqdm multi-bar (default) or a structured one-line-
per-event log (--no-progress).

The same event stream feeds Phase 6's GUI via Qt signals — this module is
purely a CLI implementation detail.
"""

from __future__ import annotations

import sys
import time
from typing import Optional


class TqdmSubscriber:
    """Renders DownloadEvents as a tqdm multi-bar.

    One bar per active file, plus a single aggregate bar showing total bytes
    transferred.  Bars close on file_finished/file_skipped.
    """

    def __init__(self):
        from tqdm import tqdm
        self._tqdm = tqdm
        self._bars: dict[str, object] = {}
        self._aggregate: Optional[object] = None
        self._agg_done = 0
        self._agg_total = 0
        # Reserve position 0 for the aggregate bar.  Per-file bars take the
        # next free integer position.
        self._next_position = 1
        self._free_positions: list[int] = []

    def __call__(self, ev) -> None:
        kind = ev.kind
        if kind == "file_started":
            self._start_bar(ev.name, ev.total)
            self._agg_total += ev.total
            self._ensure_aggregate()

        elif kind == "file_progress":
            self._update_bar(ev.name, ev.done)
            # Aggregate is rebuilt from per-bar deltas; updated lazily below.

        elif kind == "file_finished":
            self._finish_bar(ev.name, ev.done)
            self._agg_done += ev.done
            if self._aggregate is not None:
                self._aggregate.n = self._agg_done
                self._aggregate.refresh()

        elif kind == "file_skipped":
            self._agg_total += ev.total
            self._agg_done  += ev.done
            self._ensure_aggregate()
            if self._aggregate is not None:
                self._aggregate.total = self._agg_total
                self._aggregate.n     = self._agg_done
                self._aggregate.refresh()

        elif kind == "stage":
            # Print stage messages above the bars.
            self._tqdm.write(f"[stage]    {ev.stage_msg}")

        elif kind == "error":
            target = f" ({ev.name})" if ev.name else ""
            self._tqdm.write(f"[error]   {ev.error_msg}{target}")

    # ------------------------------------------------------------------

    def _ensure_aggregate(self) -> None:
        if self._aggregate is None and self._agg_total > 0:
            self._aggregate = self._tqdm(
                total=self._agg_total,
                position=0,
                unit="B",
                unit_scale=True,
                desc="total",
                leave=True,
                dynamic_ncols=True,
            )
        elif self._aggregate is not None:
            self._aggregate.total = self._agg_total
            self._aggregate.refresh()

    def _start_bar(self, name: str, total: int) -> None:
        position = (self._free_positions.pop()
                    if self._free_positions
                    else self._next_position)
        if not self._free_positions:
            self._next_position += 1
        bar = self._tqdm(
            total=total,
            position=position,
            unit="B",
            unit_scale=True,
            desc=_truncate(name, 28),
            leave=False,
            dynamic_ncols=True,
        )
        bar._pf_position = position   # remember slot for reuse on finish
        self._bars[name] = bar

    def _update_bar(self, name: str, done: int) -> None:
        bar = self._bars.get(name)
        if bar is None:
            return
        bar.update(done - bar.n)

    def _finish_bar(self, name: str, done: int) -> None:
        bar = self._bars.pop(name, None)
        if bar is None:
            return
        bar.update(done - bar.n)
        bar.close()
        position = getattr(bar, "_pf_position", None)
        if position is not None:
            self._free_positions.append(position)

    def close(self) -> None:
        for bar in list(self._bars.values()):
            bar.close()
        self._bars.clear()
        if self._aggregate is not None:
            self._aggregate.close()
            self._aggregate = None


class PlainLogSubscriber:
    """Structured one-line-per-event log mode (--no-progress).

    Suitable for CI, log file capture, or any non-TTY context where tqdm's
    cursor moves don't survive.  Output format: `[kind] name (n/N bytes)`.
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
        # Flush any pending lines.
        try:
            self._file.flush()
        except Exception:
            pass

    def _write(self, line: str) -> None:
        print(line, file=self._file, flush=True)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _truncate(s: str, width: int) -> str:
    if len(s) <= width:
        return s
    return "..." + s[-(width - 3):]


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

    Falls back to PlainLogSubscriber automatically when stdout is not a TTY
    or tqdm isn't available, even if plain=False was requested.
    """
    if plain or not sys.stdout.isatty():
        return PlainLogSubscriber()
    try:
        return TqdmSubscriber()
    except ImportError:
        return PlainLogSubscriber()
