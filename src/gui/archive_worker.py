"""ArchiveWorker — QObject that drives runner.run_session on a QThread.

Translates the runner's DownloadEvent stream + log/warn/countdown
callbacks into Qt signals the live-run view subscribes to.

Threading model:
    main thread   — QApplication, ArchivePanel, live-run view
    worker thread — QThread that owns this QObject; runs the session
                    synchronously inside `run()` until done or aborted

The runner module already prefers gevent.pool.Pool over real threads
for upload concurrency (Phase 5 fix), so we don't have to worry about
nested QThread + threading.Event deadlocks here.
"""
from __future__ import annotations

import threading
from pathlib import Path

from PySide6.QtCore import QObject, Signal


class ArchiveWorker(QObject):
    # ── stream signals ────────────────────────────────────────────
    event           = Signal(object)   # DownloadEvent
    log_line        = Signal(str, str) # (message, level: "info"|"warn"|"error")
    countdown_tick  = Signal(int)      # seconds remaining until next poll cycle
    # ── lifecycle ─────────────────────────────────────────────────
    started         = Signal()
    finished        = Signal(object)   # RunResult
    failed          = Signal(str)      # exception string

    def __init__(self, *, project_obj, project_path,
                 app_ids: list[int],
                 platform: str | None = None,
                 branch: str = "public",
                 crack_mode: str | None = None,   # "coldclient" | "gse" | None
                 force_download: bool = False,
                 notify_mode_override: str | None = None,
                 output_dir: Path | None = None,
                 log_file: Path | None = None):
        super().__init__()
        self._project_obj    = project_obj
        self._project_path   = project_path
        self._app_ids        = list(app_ids)
        self._platform       = platform
        self._branch         = branch
        self._crack_mode     = crack_mode
        self._force_download = force_download
        self._notify_mode_override = notify_mode_override
        self._output_dir     = output_dir
        self._log_file       = log_file
        self._abort = threading.Event()

    # ---------------------------------------------------------- public
    def request_abort(self) -> None:
        """Tell the run loop to stop after the current poll cycle / file.

        Honoured by `_countdown_sleep` (returns False on abort) and by
        the runner's per-app loop (it checks the abort event between
        apps via the warn callback's stop semantics).  In-flight downloads
        proceed to completion — kill -9 the process if the user actually
        wants instant termination.
        """
        self._abort.set()

    # ---------------------------------------------------------- entrypoint
    def run(self) -> None:
        """Called via QThread.started → connected slot."""
        self.started.emit()

        try:
            from src.core.archive import credentials   as creds_mod
            from src.core.archive import depots_ini
            from src.core.archive import notify        as notify_mod
            from src.core.archive import upload        as upload_mod
            from src.core.archive import runner        as runner_mod
            from src.core.archive.appinfo  import login as cm_login
            from src.core.archive.compress import parse_size
        except ImportError as exc:
            self.failed.emit(f"archive extras not installed: {exc}")
            return

        try:
            creds = creds_mod.load()
            if not creds.has_login_tokens():
                raise RuntimeError(
                    "No saved Steam tokens — run `patchforge archive login` "
                    "in a terminal first."
                )

            project_obj  = self._project_obj
            project_path = self._project_path
            opts = self._build_opts(project_obj)

            # platform: explicit > project default > "windows"
            platform = (self._platform
                        or (project_obj.default_platform if project_obj else None)
                        or "windows")

            # output dir: explicit > project.output_dir > app_settings > cwd
            output_dir = self._output_dir
            if output_dir is None and project_obj and project_obj.output_dir:
                output_dir = Path(project_obj.output_dir)
            if output_dir is None:
                output_dir = Path.cwd() / "archives"
            output_dir.mkdir(parents=True, exist_ok=True)

            try:
                volume_size = parse_size(opts["volume_size"]) if opts["volume_size"] else None
            except ValueError:
                raise RuntimeError(f"Invalid volume size: {opts['volume_size']!r}")

            notify_mode = runner_mod.resolve_notify_mode(
                self._notify_mode_override,
                project_obj.notify_mode if project_obj else "",
                creds,
            )

            unstub_options = None
            if self._crack_mode and project_obj is not None:
                u = project_obj.unstub
                unstub_options = {
                    "keepbind":       u.keepbind,
                    "zerodostub":     not u.keepstub,
                    "dumppayload":    u.dumppayload,
                    "dumpdrmp":       u.dumpdrmp,
                    "realign":        u.realign,
                    "recalcchecksum": u.recalcchecksum,
                }

            crack_identity = None
            if self._crack_mode:
                from src.core.archive import project as project_mod
                crack_identity = (project_obj.crack
                                  if project_obj else project_mod.CrackIdentity())

            tokens = {
                "username":             creds.username,
                "steam_id":             creds.steam_id,
                "client_refresh_token": creds.client_refresh_token,
            }
            client, cdn = cm_login(tokens)

            depot_names = depots_ini.load()

            # Optional log-file tee — mirrors the CLI's --log flag.
            log_fh = None
            if self._log_file is not None:
                try:
                    log_fh = open(self._log_file, "a", encoding="utf-8")
                except OSError as exc:
                    self.log_line.emit(f"could not open log file {self._log_file}: {exc}", "warn")

            def _emit_log(msg: str, level: str = "info") -> None:
                self.log_line.emit(str(msg), level)
                if log_fh is not None:
                    try:
                        log_fh.write(f"[{level}] {msg}\n")
                        log_fh.flush()
                    except OSError:
                        pass

            try:
                result = runner_mod.run_session(
                    client=client, cdn=cdn,
                    project_obj=project_obj, project_path=project_path,
                    creds=creds, output_dir=output_dir,
                    app_ids=self._app_ids,
                    opts=opts,
                    platform=platform,
                    notify_mode=notify_mode,
                    branch=self._branch, crack=self._crack_mode,
                    crack_identity=crack_identity,
                    unstub_options=unstub_options,
                    volume_size=volume_size,
                    depot_names=depot_names,
                    subscriber=self._on_event,
                    upload_mod=upload_mod, notify_mod=notify_mod,
                    countdown_sleep=self._countdown_sleep,
                    relogin=lambda: cm_login(tokens),
                    log=lambda m: _emit_log(m, "info"),
                    warn=lambda m: _emit_log(m, "warn"),
                    abort=self._abort.is_set,
                )
            finally:
                try:
                    client.logout()
                except Exception:
                    pass
                if log_fh is not None:
                    try:
                        log_fh.close()
                    except Exception:
                        pass
        except Exception as exc:
            self.failed.emit(str(exc))
            return

        self.finished.emit(result)

    # ---------------------------------------------------------- bridges
    def _on_event(self, ev) -> None:
        """DownloadEvent subscriber — re-emit on the Qt signal bus.

        Qt auto-marshals across the worker→main thread boundary because
        the signal is queued by default when sender lives in a different
        thread than the receiver.
        """
        self.event.emit(ev)

    def _countdown_sleep(self, seconds: int) -> bool:
        """Replacement for cli/main._poll_countdown — emits ticks back to
        the GUI instead of redrawing a TTY line.  Returns False when the
        user clicked Stop (abort flag set).

        Uses gevent.sleep so the Steam CM heartbeat greenlet keeps
        firing during the delay — time.sleep blocks the gevent hub on
        this QThread for the entire wait, which kills the CM session
        on long restart_delay values.
        """
        if seconds <= 0:
            return True
        import gevent as _gevent
        for remaining in range(seconds, 0, -1):
            if self._abort.is_set():
                return False
            self.countdown_tick.emit(remaining)
            _gevent.sleep(1)
        self.countdown_tick.emit(0)
        return not self._abort.is_set()

    # ---------------------------------------------------------- opts
    def _build_opts(self, project_obj) -> dict:
        """Mirror cli._resolve_archive_run_options for the GUI: the
        project's stored values are the defaults, no CLI overrides.

        force_download is the one knob owned per-run rather than
        persisted, so we pass it through at construction time.
        """
        if project_obj is None:
            from src.core.archive.project import ArchiveProject
            project_obj = ArchiveProject()
        return {
            "workers":                project_obj.workers or 8,
            "compression":            (project_obj.compression
                                       if project_obj.compression is not None else 9),
            "archive_password":       project_obj.archive_password,
            "volume_size":            project_obj.volume_size,
            "language":               project_obj.language or "english",
            "max_retries":            project_obj.max_retries or 1,
            "description":            project_obj.upload_description,
            "max_concurrent_uploads": project_obj.max_concurrent_uploads or 1,
            "delete_archives":        project_obj.delete_archives,
            "experimental":           project_obj.experimental,
            "unstub":                 project_obj.unstub,
            "restart_delay":          project_obj.restart_delay or 0,
            "batch_size":             project_obj.batch_size or 0,
            "force_download":         self._force_download,
        }


__all__ = ["ArchiveWorker"]
