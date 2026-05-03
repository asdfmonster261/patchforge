"""Archive run orchestrator — shared between CLI and GUI.

Owns the per-app pipeline (pre-notify → download → upload → bbcode → post-
notify) and the polling-loop driver.  Extracted from cli/main.py so the
PySide6 GUI can drive the same pipeline without spawning a subprocess.

Concerns kept *out* of this module:
  * argparse / argument resolution (CLI's job)
  * TTY-specific countdown rendering (caller passes a `countdown_sleep`
    callback — CLI uses `\\r`-driven line, GUI emits a Qt signal)
  * stdout printing of progress (caller passes a DownloadEvent
    `subscriber` and a `log`/`warn` text-line pair)

Public surface:
  * RunResult (dataclass — what to display when the run finishes)
  * run_pre_notify, run_post_pipeline, run_one_app  (single-app helpers,
    exported mostly for testing)
  * run_session (top-level driver — single-pass + polling modes both
    handled here)
  * resolve_notify_mode (pure helper, shared by CLI arg-resolve)
  * build_notify_data, send_notifications (notify primitives, shared)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from . import project as project_mod
from .download import DownloadEvent  # type-only import; keep it lazy-friendly


LogFn = Callable[[str], None]
SubscriberFn = Callable[[DownloadEvent], None]
CountdownFn = Callable[[int], bool]   # returns False on abort


@dataclass
class RunResult:
    archives:           list[Path]    = field(default_factory=list)
    unknown_depot_ids:  set[str]      = field(default_factory=set)
    project_dirty:      bool          = False     # caller persists


# ---------------------------------------------------------------------------
# Notify primitives
# ---------------------------------------------------------------------------

def resolve_notify_mode(cli_flag: str | None,
                        project_field: str,
                        creds) -> str:
    """Pick active notify mode for one run.

    Priority: CLI/explicit flag > project field > auto-default.  Auto
    picks "delay" when MultiUp upload creds are present (so the post-
    upload notification carries links) and "pre" otherwise.  Returns
    "none" when no notify creds are set at all.
    """
    if not (creds.discord.is_set() or creds.telegram.is_set()):
        return "none"
    for source in (cli_flag, project_field):
        if source in ("pre", "delay", "both"):
            return source
    return "delay" if creds.multiup.is_set() else "pre"


def build_notify_data(app_meta: dict, previous_buildid: str) -> dict:
    return {
        "appid":            app_meta.get("appid"),
        "name":             app_meta.get("name", ""),
        "previous_buildid": previous_buildid or "",
        "current_buildid":  app_meta.get("buildid", ""),
        "timeupdated":      app_meta.get("timeupdated", 0),
    }


def send_notifications(notify_data: dict,
                       upload_links: dict | None,
                       creds, *, notify_mod,
                       force_download: bool = False,
                       warn: LogFn = print) -> None:
    """Fire Discord + Telegram for one notify event.  Per-channel failures
    are warnings; they don't abort the run."""
    if creds.discord.is_set():
        try:
            notify_mod.send_discord_notification(
                creds.discord.webhook_url,
                notify_data,
                mention_role_ids=creds.discord.mention_role_ids or None,
                upload_links=upload_links,
                force_download=force_download,
            )
        except Exception as exc:
            warn(f"Discord notify failed: {exc}")
    if creds.telegram.is_set():
        try:
            notify_mod.send_telegram_notification(
                creds.telegram.token,
                creds.telegram.chat_ids,
                notify_data,
                upload_links=upload_links,
                force_download=force_download,
            )
        except Exception as exc:
            warn(f"Telegram notify failed: {exc}")


# ---------------------------------------------------------------------------
# Per-app pipeline
# ---------------------------------------------------------------------------

def run_pre_notify(app_meta: dict, previous_buildid: str, creds, *,
                   notify_mode: str, notify_mod,
                   force_download: bool = False,
                   warn: LogFn = print) -> None:
    """Pre-download notification (no upload links).  Skipped unless mode
    is 'pre' or 'both'."""
    if notify_mode not in ("pre", "both"):
        return
    if not app_meta:
        return
    send_notifications(
        build_notify_data(app_meta, previous_buildid),
        upload_links=None,
        creds=creds, notify_mod=notify_mod,
        force_download=force_download,
        warn=warn,
    )


def run_post_pipeline(archives, app_meta, previous_buildid, creds, *,
                      upload_mod, notify_mod, output_dir: Path,
                      subscriber: SubscriberFn | None,
                      notify_mode: str = "delay",
                      description: str | None = None,
                      max_concurrent: int = 1,
                      delete_archives: bool = False,
                      force_download: bool = False,
                      manifests: dict | None = None,
                      bbcode_template: str = "",
                      log: LogFn = print,
                      warn: LogFn = print) -> None:
    """Upload archives → render BBCode post → fire post-upload notify.

    Upload always runs when MultiUp creds are set, regardless of
    notify_mode — notify_mode only gates whether a *post* notify fires.
    """
    if not archives or not app_meta:
        return

    # ---- upload --------------------------------------------------------
    stem_to_url: dict[str, str] = {}
    if creds.multiup.is_set():
        try:
            stem_to_url = upload_mod.upload_archives(
                archives,
                username=creds.multiup.username or None,
                password=creds.multiup.password or None,
                description=description or str(app_meta.get("name", "")) or None,
                max_concurrent=max_concurrent,
                links_dir=output_dir,
                bin_url=creds.privatebin.url or None,
                bin_pass=creds.privatebin.password or None,
                delete_archives=delete_archives,
                on_event=subscriber,
            )
        except Exception as exc:
            import traceback
            warn(f"Upload failed for app {app_meta.get('appid')}: {exc}")
            for tb_line in traceback.format_exc().splitlines():
                warn(tb_line)

    # platform -> url map for notify + bbcode rendering
    from . import notify as _notify_helpers  # for _platform_from_archive_stem
    platform_links: dict[str, str] = {}
    for stem, url in stem_to_url.items():
        plat = _platform_from_archive_stem(stem)
        if plat:
            platform_links[plat] = url

    # ---- bbcode post --------------------------------------------------
    if stem_to_url and bbcode_template and bbcode_template.strip():
        try:
            from . import bbcode as bbcode_mod
            from .notify import _steam_image_url
            data = bbcode_mod.build_data(
                name=str(app_meta.get("name", "")),
                appid=app_meta.get("appid", ""),
                buildid=str(app_meta.get("buildid", "")),
                previous_buildid=previous_buildid or "",
                timeupdated=app_meta.get("timeupdated", 0),
                upload_links=platform_links or None,
                manifests=manifests or {},
                header_image=_steam_image_url(app_meta.get("appid", "")),
            )
            rendered = bbcode_mod.render(bbcode_template, data)
            sname = bbcode_mod.safe_name(
                str(app_meta.get("name", "")) or str(app_meta.get("appid", ""))
            )
            buildid = str(app_meta.get("buildid", "")) or "build"
            out_path = Path(output_dir) / f"{sname}.{buildid}.post.txt"
            out_path.write_text(rendered, encoding="utf-8")
            log(f"BBCode post: {out_path.name}")
            for stem in stem_to_url:
                sidecar = Path(output_dir) / f"{stem}.txt"
                if sidecar.exists():
                    try:
                        sidecar.unlink()
                    except OSError as exc:
                        warn(f"Could not remove {sidecar.name}: {exc}")
        except Exception as exc:
            warn(f"BBCode render failed for app {app_meta.get('appid')}: {exc}")

    # ---- post-upload notify -------------------------------------------
    if notify_mode not in ("delay", "both"):
        return
    send_notifications(
        build_notify_data(app_meta, previous_buildid),
        upload_links=platform_links or None,
        creds=creds, notify_mod=notify_mod,
        force_download=force_download,
        warn=warn,
    )


def _platform_from_archive_stem(stem: str) -> str | None:
    """Mirror cli/main._platform_from_archive_stem for run_post_pipeline."""
    for plat in ("windows", "linux", "macos"):
        if f".{plat}" in stem:
            return plat
    return None


def run_one_app(app_id: int, previous_buildid: str, *,
                client, cdn, output_dir: Path,
                platform: str, opts: dict,
                creds, branch: str, crack: bool,
                crack_identity, unstub_options,
                volume_size, depot_names: dict,
                subscriber: SubscriberFn | None,
                notify_mode: str,
                project_obj,
                upload_mod, notify_mod,
                app_info_hint: dict | None = None,
                log: LogFn = print,
                warn: LogFn = print,
                result: RunResult | None = None,
                apps_by_id: dict | None = None) -> None:
    """Pre-notify + download + post-pipeline for a single app.

    Errors from download_app are logged via `warn` and skipped — caller
    loop continues with the next entry.  Mutates `result` in place
    (archives + unknown_depot_ids) and updates AppEntry buildids.
    """
    from .download import download_app

    log(f"=== app {app_id} ===")
    hint = app_info_hint or {}

    if notify_mode in ("pre", "both"):
        run_pre_notify(
            app_meta={
                "appid":       app_id,
                "name":        hint.get("name", str(app_id)),
                "buildid":     hint.get("buildid", ""),
                "timeupdated": hint.get("timeupdated", 0),
            },
            previous_buildid=previous_buildid, creds=creds,
            notify_mode=notify_mode, notify_mod=notify_mod,
            force_download=opts["force_download"],
            warn=warn,
        )
    try:
        archives, platform_manifests, app_meta = download_app(
            client, cdn, app_id, output_dir,
            platform=platform, workers=opts["workers"],
            password=opts["archive_password"],
            compression_level=opts["compression"],
            volume_size=volume_size,
            branch=branch,
            crack=crack,
            experimental=opts["experimental"],
            unstub_options=unstub_options,
            depot_names=depot_names,
            max_retries=opts["max_retries"],
            language=opts["language"],
            crack_identity=crack_identity,
            on_event=subscriber,
        )
    except NotImplementedError:
        raise
    except Exception as exc:
        warn(f"app {app_id} failed: {exc}")
        return

    if result is not None:
        result.archives.extend(archives)
        for plat_records in platform_manifests.values():
            for depot_id, depot_name, _gid in plat_records:
                if not depot_name:
                    result.unknown_depot_ids.add(str(depot_id))

    run_post_pipeline(
        archives, app_meta, previous_buildid, creds,
        upload_mod=upload_mod, notify_mod=notify_mod,
        output_dir=output_dir, subscriber=subscriber,
        notify_mode=notify_mode,
        description=opts["description"],
        max_concurrent=opts["max_concurrent_uploads"],
        delete_archives=opts["delete_archives"],
        force_download=opts["force_download"],
        manifests=platform_manifests,
        bbcode_template=(project_obj.bbcode_template
                         if project_obj is not None else ""),
        log=log, warn=warn,
    )

    if apps_by_id is not None:
        entry_local = apps_by_id.get(app_id)
        if entry_local is not None and app_meta.get("buildid"):
            new_bid = str(app_meta["buildid"])
            old_bid = entry_local.current_buildid.buildid
            new_ts  = int(app_meta.get("timeupdated", 0) or 0)
            old_ts  = int(entry_local.current_buildid.timeupdated or 0)
            # Shift the persisted history one slot when the buildid
            # actually moves; a re-download of the same buildid (e.g.
            # --force-download against an unchanged Steam state) leaves
            # previous_buildid alone so we don't lose the real history.
            if old_bid and old_bid != new_bid:
                entry_local.previous_buildid = project_mod.BuildIdRecord(
                    buildid=old_bid, timeupdated=old_ts,
                )
            entry_local.current_buildid.buildid = new_bid
            if new_ts:
                entry_local.current_buildid.timeupdated = new_ts
        # Refresh the cached display name whenever we have one — keeps
        # the .xarchive showing the latest Steam name even after a
        # rename, with negligible cost.
        if entry_local is not None and app_meta.get("name"):
            entry_local.name = str(app_meta["name"])

        # Append-only manifest history.  Dedup on the full
        # (buildid, branch, platform, depot_id, manifest_gid) tuple so
        # repeated --force-download runs against the same buildid don't
        # bloat the list, while a shared content depot under two
        # platforms intentionally produces two distinct rows.
        if (entry_local is not None
                and platform_manifests
                and app_meta.get("buildid")):
            bid = str(app_meta["buildid"])
            timeupdated = int(app_meta.get("timeupdated", 0) or 0)
            existing = {
                (r.buildid, r.branch, r.platform,
                 int(r.depot_id), str(r.manifest_gid))
                for r in entry_local.manifest_history
            }
            for plat_name, plat_records in platform_manifests.items():
                # Defensive: download_app keys this dict by actual
                # platform name even under --platform all, but skip the
                # literal "all" if a future caller ever lands it here.
                if plat_name == "all":
                    continue
                for depot_id, depot_name, gid in plat_records:
                    key = (bid, branch, plat_name,
                           int(depot_id), str(gid))
                    if key in existing:
                        continue
                    entry_local.manifest_history.append(
                        project_mod.ManifestRecord(
                            buildid=bid, branch=branch,
                            platform=plat_name,
                            depot_id=int(depot_id),
                            depot_name=depot_name or "",
                            manifest_gid=str(gid),
                            timeupdated=timeupdated,
                        )
                    )
                    existing.add(key)


# ---------------------------------------------------------------------------
# Top-level driver
# ---------------------------------------------------------------------------

def run_session(*,
                client, cdn,
                project_obj, project_path: Path | None,
                creds, output_dir: Path,
                app_ids: list[int],
                opts: dict,
                platform: str,
                notify_mode: str,
                branch: str,
                crack: bool,
                crack_identity,
                unstub_options,
                volume_size,
                depot_names: dict,
                subscriber: SubscriberFn | None,
                upload_mod, notify_mod,
                countdown_sleep: CountdownFn | None = None,
                log: LogFn = print,
                warn: LogFn = print,
                abort=None) -> RunResult:
    """Run one full archive session — single-pass over `app_ids`, or
    polling driver when `opts["restart_delay"] > 0`.

    Returns a RunResult; caller handles project persistence.
    """
    result = RunResult()

    apps_by_id: dict[int, project_mod.AppEntry] = {}
    if project_obj is not None:
        for entry in project_obj.apps:
            apps_by_id[entry.app_id] = entry

    restart_delay = int(opts.get("restart_delay") or 0)
    poll_mode = restart_delay > 0
    poll_batch_size = int(opts.get("batch_size") or 0) or None

    def _save_project_now() -> None:
        if project_obj is None or project_path is None:
            return
        try:
            project_mod.save(project_obj, project_path)
        except Exception as exc:
            warn(f"Could not persist project to {project_path}: {exc}")

    def _aborted() -> bool:
        return abort is not None and abort()

    if poll_mode:
        from . import poll as poll_mod
        force = bool(opts["force_download"])
        iteration = 0
        while True:
            if _aborted():
                log("aborted before poll cycle")
                break
            iteration += 1
            log(f"\n=== poll cycle {iteration} ===")
            try:
                changes = poll_mod.detect_changes(
                    client, cdn, apps_by_id,
                    force_download=force,
                    batch_size=poll_batch_size,
                    max_retries=opts["max_retries"],
                    on_event=subscriber,
                    abort=abort,
                )
            except Exception as exc:
                warn(f"poll cycle failed: {exc}")
                changes = []
            if not changes:
                log("no buildid changes detected this cycle")
            for app_id, prev, _curr, info in changes:
                if _aborted():
                    log("aborted between apps")
                    break
                run_one_app(
                    app_id, prev, app_info_hint=info,
                    client=client, cdn=cdn, output_dir=output_dir,
                    platform=platform, opts=opts,
                    creds=creds, branch=branch, crack=crack,
                    crack_identity=crack_identity,
                    unstub_options=unstub_options,
                    volume_size=volume_size, depot_names=depot_names,
                    subscriber=subscriber, notify_mode=notify_mode,
                    project_obj=project_obj,
                    upload_mod=upload_mod, notify_mod=notify_mod,
                    log=log, warn=warn,
                    result=result, apps_by_id=apps_by_id,
                )
            _save_project_now()
            force = False
            if _aborted():
                break
            if countdown_sleep is None:
                break
            if not countdown_sleep(restart_delay):
                break
    else:
        # Single-pass: emit a synthetic app_info_progress so the GUI /
        # cli display can show "X / N apps processed" the same way poll
        # mode does, even though we don't pre-fetch product-info here.
        total = len(app_ids)
        for i, app_id in enumerate(app_ids, 1):
            if _aborted():
                log("aborted between apps")
                break
            if subscriber is not None:
                from .download import DownloadEvent
                subscriber(DownloadEvent(
                    kind="app_info_progress",
                    name=str(app_id),
                    done=i,
                    total=total,
                ))
            entry = apps_by_id.get(app_id)
            previous_buildid = entry.current_buildid.buildid if entry else ""
            run_one_app(
                app_id, previous_buildid,
                client=client, cdn=cdn, output_dir=output_dir,
                platform=platform, opts=opts,
                creds=creds, branch=branch, crack=crack,
                crack_identity=crack_identity,
                unstub_options=unstub_options,
                volume_size=volume_size, depot_names=depot_names,
                subscriber=subscriber, notify_mode=notify_mode,
                project_obj=project_obj,
                upload_mod=upload_mod, notify_mod=notify_mod,
                log=log, warn=warn,
                result=result, apps_by_id=apps_by_id,
            )

    return result
