"""Steam depot downloader — fetches manifests, pulls per-file content from
the CDN, writes ACF, and hands off to compress_platform() for archiving.

Progress is reported via DownloadEvent objects passed to an optional
on_event callback.  This decouples the download logic from any specific
display layer (CLI tqdm bars, GUI Qt signals, structured log lines), and
lets Phase 6's GUI subscribe to the same event stream for free.

Imports steam[client] / gevent lazily so that simply importing this
module without the archive extras installed does not raise.
"""

from __future__ import annotations

import shutil
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Literal
from zlib import adler32

from .acf       import build_app_acf, build_shared_acf, write_acf
from .compress  import compress_platform, sanitize_name
from .errors    import SessionDead


KNOWN_PLATFORMS = ("windows", "linux", "macos")


# ---------------------------------------------------------------------------
# Event protocol — what the download emits to its subscribers
# ---------------------------------------------------------------------------

EventKind = Literal[
    "file_started",
    "file_progress",
    "file_finished",
    "file_skipped",
    "stage",
    "error",
    "compress_started",
    "compress_progress",
    "compress_finished",
    "crack_started",
    "crack_finished",
]


@dataclass
class DownloadEvent:
    """Structured progress event.

    file_started / file_finished / file_skipped fire once per file.
    file_progress fires repeatedly with cumulative `done` byte count.
    stage fires for high-level transitions ("Fetching manifests", etc.).
    error reports a non-fatal per-file failure; callers should still get
    a final stage='done' or similar.
    """
    kind:      EventKind
    name:      str   = ""
    total:     int   = 0
    done:      int   = 0
    elapsed:   float = 0.0
    stage_msg: str   = ""
    error_msg: str   = ""


EventCallback = Callable[[DownloadEvent], None]


# ---------------------------------------------------------------------------
# Lazy steam[client] imports
# ---------------------------------------------------------------------------

def _import_steam():
    from ._extras import patch_steam_monkey, require_extras
    require_extras()
    patch_steam_monkey()
    from gevent.pool       import Pool as GreenPool
    from gevent.timeout    import Timeout as GeventTimeout
    from steam.client      import SteamClient
    from steam.client.cdn  import CDNClient
    from steam.enums       import EResult
    from steam.exceptions  import ManifestError
    return GreenPool, GeventTimeout, SteamClient, CDNClient, EResult, ManifestError


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _emit(on_event: EventCallback | None, **kw) -> None:
    if on_event is not None:
        on_event(DownloadEvent(**kw))


def _get_dlc_app_ids(app_data: dict) -> list[int]:
    """Return DLC app IDs listed in extended.listofdlc."""
    listofdlc = app_data.get("extended", {}).get("listofdlc", "")
    if not listofdlc:
        return []
    return [int(x.strip()) for x in str(listofdlc).split(",") if x.strip().isdigit()]


def _get_available_platforms(app_data: dict) -> list[str]:
    """Return the sorted list of platforms this app supports, from common.oslist."""
    app_oslist_raw = app_data.get("common", {}).get("oslist", "")
    if not app_oslist_raw:
        return []
    return sorted(
        p.strip() for p in app_oslist_raw.split(",")
        if p.strip() in KNOWN_PLATFORMS
    )


# ---------------------------------------------------------------------------
# Core: download one platform's depots into dest
# ---------------------------------------------------------------------------

def _download_platform(cdn, client, app_id: int, app_data: dict, dest: Path,
                       platform: str, branch: str = "public",
                       workers: int = 8, password: str | None = None,
                       compression_level: int = 0,
                       volume_size: int | None = None,
                       crack: str | None = None,
                       experimental: bool = False,
                       unstub_options: dict | None = None,
                       depot_names: dict | None = None,
                       max_retries: int = 1,
                       language: str = "english",
                       crack_identity=None,
                       on_event: EventCallback | None = None
                       ) -> tuple[list[Path], list]:
    """Download required depots for one platform into dest in Steam-like layout."""
    if crack and crack_identity is None:
        raise ValueError("crack=... requires crack_identity to be passed in")

    GreenPool, GeventTimeout, _, _, EResult, ManifestError = _import_steam()

    installdir  = app_data.get("config", {}).get("installdir", str(app_id))
    common_dir  = dest / "steamapps" / "common"
    game_dest   = common_dir / installdir
    shared_dest = common_dir / "Steamworks Shared"
    depotcache  = dest / "depotcache"
    depotcache.mkdir(parents=True, exist_ok=True)

    licensed_apps = cdn.licensed_app_ids
    depots_info   = app_data.get("depots", {})

    def filter_func(depot_id, depot_info):
        config = depot_info.get("config", {})
        depot_lang = config.get("language", "")
        if depot_lang and depot_lang != language:
            return False
        if "dlcappid" in depot_info:
            if int(depot_info["dlcappid"]) not in licensed_apps:
                return False
        oslist = config.get("oslist", "")
        if oslist and platform not in [p.strip() for p in oslist.split(",")]:
            return False
        return True

    _emit(on_event, kind="stage",
          stage_msg=f"Fetching manifests for platform '{platform}'")

    manifests = None
    for attempt in range(max_retries + 1):
        try:
            manifests = cdn.get_manifests(app_id, filter_func=filter_func)
            break
        except (GeventTimeout, ManifestError) as e:
            is_timeout = isinstance(e, GeventTimeout) or (
                isinstance(e, ManifestError)
                and getattr(e, "eresult", None) == EResult.Timeout
            )
            if attempt < max_retries:
                _emit(on_event, kind="error",
                      error_msg=f"Manifest fetch failed: {e}, retrying "
                                f"({attempt + 1}/{max_retries})")
            elif is_timeout:
                raise SessionDead(
                    f"Timed out fetching manifests for platform '{platform}'."
                )
            else:
                _emit(on_event, kind="error",
                      error_msg=f"Manifest fetch failed: {e}")
                return [], []

    if not manifests:
        _emit(on_event, kind="error",
              error_msg=f"No depots matched for platform '{platform}'")
        return [], []

    # DLC handling — fetch manifests for owned DLC apps.
    def _dlc_filter(depot_id, depot_info):
        config = depot_info.get("config", {})
        depot_lang = config.get("language", "")
        if depot_lang and depot_lang != language:
            return False
        oslist = config.get("oslist", "")
        if oslist and platform not in [p.strip() for p in oslist.split(",")]:
            return False
        return True

    dlc_data: list[tuple[int, dict, list, dict]] = []
    for dlc_app_id in _get_dlc_app_ids(app_data):
        if dlc_app_id not in licensed_apps:
            continue
        dlc_info = client.get_product_info(apps=[dlc_app_id])
        dlc_app_data = dlc_info.get("apps", {}).get(dlc_app_id, {})
        if not dlc_app_data or not dlc_app_data.get("depots"):
            continue
        dlc_name = dlc_app_data.get("common", {}).get("name", f"DLC {dlc_app_id}")
        dlc_manifests = None
        skip_dlc = False
        for attempt in range(max_retries + 1):
            try:
                dlc_manifests = cdn.get_manifests(dlc_app_id, filter_func=_dlc_filter)
                break
            except KeyError:
                skip_dlc = True
                break
            except ManifestError as e:
                is_timeout = getattr(e, "eresult", None) == EResult.Timeout
                if attempt < max_retries:
                    _emit(on_event, kind="error",
                          error_msg=f"DLC '{dlc_name}' manifest fetch failed: {e}, retrying")
                elif is_timeout:
                    raise SessionDead(
                        f"Timed out fetching manifests for DLC '{dlc_name}'."
                    )
                else:
                    _emit(on_event, kind="error",
                          error_msg=f"DLC '{dlc_name}' manifest fetch failed: {e}, skipping")
                    skip_dlc = True
                    break
            except GeventTimeout:
                if attempt < max_retries:
                    _emit(on_event, kind="error",
                          error_msg=f"DLC '{dlc_name}' manifest fetch timed out, retrying")
                else:
                    raise SessionDead(
                        f"Timed out fetching manifests for DLC '{dlc_name}'."
                    )
        if skip_dlc or not dlc_manifests:
            continue
        dlc_data.append((dlc_app_id, dlc_app_data, dlc_manifests,
                         dlc_app_data.get("depots", {})))

    all_manifests = manifests + [m for _, _, ms, _ in dlc_data for m in ms]
    all_depots_info = dict(depots_info)
    for _, _, _, dlc_depots_info in dlc_data:
        all_depots_info.update(dlc_depots_info)

    manifest_records = [
        (m.depot_id,
         (depot_names or {}).get(str(m.depot_id))
         or all_depots_info.get(str(m.depot_id), {}).get("name")
         or (m.name if isinstance(m.name, str) else None)
         or None,
         str(m.gid))
        for m in all_manifests
    ]

    # Persist manifests to depotcache/ for Steam compatibility.
    for manifest in all_manifests:
        manifest_path = depotcache / f"{manifest.depot_id}_{manifest.gid}.manifest"
        if not manifest_path.exists():
            manifest.metadata.crc_clear = (
                adler32(manifest.payload.SerializeToString()) & 0xFFFFFFFF
            )
            manifest.signature.Clear()
            manifest_path.write_bytes(manifest.serialize(compress=False))

    # ---- Per-file download via gevent green pool --------------------------

    def _do_download(depot_file, dest_dir: Path) -> tuple[int, int]:
        """Download one file.  Returns (downloaded_bytes, skipped_bytes)."""
        fpath = dest_dir / depot_file.filename
        fpath.parent.mkdir(parents=True, exist_ok=True)

        if fpath.exists() and fpath.stat().st_size == depot_file.size:
            _emit(on_event, kind="file_skipped",
                  name=depot_file.filename, total=depot_file.size,
                  done=depot_file.size)
            return 0, depot_file.size

        started = time.monotonic()
        _emit(on_event, kind="file_started",
              name=depot_file.filename, total=depot_file.size)

        chunk_size = 1024 * 1024
        done = 0
        with open(fpath, "wb") as fh:
            while True:
                data = depot_file.read(chunk_size)
                if not data:
                    break
                fh.write(data)
                done += len(data)
                _emit(on_event, kind="file_progress",
                      name=depot_file.filename,
                      total=depot_file.size, done=done)

        if depot_file.is_executable:
            fpath.chmod(fpath.stat().st_mode | 0o111)

        _emit(on_event, kind="file_finished",
              name=depot_file.filename,
              total=depot_file.size, done=done,
              elapsed=time.monotonic() - started)
        return done, 0

    def _run_depot(files: list, dest_dir: Path) -> tuple[int, int, list]:
        """Download a list of depot files via the green pool.
        Returns (downloaded_bytes, skipped_bytes, errors)."""

        def _try(df):
            try:
                dl, sk = _do_download(df, dest_dir)
                return ("ok", dl, sk)
            except KeyboardInterrupt:
                raise
            except Exception as exc:
                _emit(on_event, kind="error",
                      name=df.filename, error_msg=str(exc))
                return ("err", df.filename, exc)

        downloaded = 0
        skipped    = 0
        errors: list = []
        pool = GreenPool(size=workers)
        try:
            for result in pool.imap_unordered(_try, files):
                if result[0] == "ok":
                    downloaded += result[1]
                    skipped    += result[2]
                else:
                    errors.append((result[1], result[2]))
        except KeyboardInterrupt:
            pool.kill()
            raise
        return downloaded, skipped, errors

    # ---- Iterate base + DLC depots ---------------------------------------

    total_downloaded = 0
    total_skipped    = 0

    def _process_manifests(ms, dest_dir_resolver):
        nonlocal total_downloaded, total_skipped
        for manifest in ms:
            _emit(on_event, kind="stage",
                  stage_msg=f"Depot {manifest.depot_id}  {manifest.name or ''}")
            depot_dest = dest_dir_resolver(manifest)
            files_to_download = []
            for depot_file in manifest:
                fpath = depot_dest / depot_file.filename
                if depot_file.is_directory:
                    fpath.mkdir(parents=True, exist_ok=True)
                elif depot_file.is_symlink:
                    target = Path(depot_file.linktarget)
                    if fpath.exists() or fpath.is_symlink():
                        fpath.unlink()
                    try:
                        fpath.symlink_to(target)
                    except OSError as e:
                        _emit(on_event, kind="error",
                              name=depot_file.filename,
                              error_msg=f"Symlink failed: {e}")
                else:
                    files_to_download.append(depot_file)

            dl, sk, errors = _run_depot(files_to_download, depot_dest)
            total_downloaded += dl
            total_skipped    += sk

    def _base_dest_resolver(manifest):
        depot_info = depots_info.get(str(manifest.depot_id), {})
        return shared_dest if "depotfromapp" in depot_info else game_dest

    _process_manifests(manifests, _base_dest_resolver)

    for dlc_app_id, dlc_app_data, dlc_manifests, _dlc_depots_info in dlc_data:
        dlc_name = dlc_app_data.get("common", {}).get("name", f"DLC {dlc_app_id}")
        _emit(on_event, kind="stage", stage_msg=f"DLC: {dlc_name}")
        _process_manifests(dlc_manifests, lambda m: game_dest)

    _emit(on_event, kind="stage",
          stage_msg=f"Done downloading "
                    f"({total_downloaded} bytes downloaded, {total_skipped} skipped)")

    # ---- ACF generation --------------------------------------------------

    steamapps_dir = dest / "steamapps"
    steamapps_dir.mkdir(parents=True, exist_ok=True)

    game_manifests   = [m for m in manifests
                        if "depotfromapp" not in depots_info.get(str(m.depot_id), {})]
    shared_manifests = [m for m in manifests
                        if "depotfromapp" in     depots_info.get(str(m.depot_id), {})]

    write_acf(steamapps_dir / f"appmanifest_{app_id}.acf",
              build_app_acf(app_id, app_data, game_manifests, depots_info,
                            dlc_data=[(did, ms) for did, _, ms, _ in dlc_data]))

    source_app_ids = {
        int(depots_info[str(m.depot_id)]["depotfromapp"])
        for m in shared_manifests
    }
    for source_app_id in source_app_ids:
        source_manifests = [m for m in shared_manifests
                            if int(depots_info[str(m.depot_id)]["depotfromapp"]) == source_app_id]
        source_info = client.get_product_info(apps=[source_app_id])
        source_app_data = source_info["apps"].get(source_app_id, {})
        source_depots_info = source_app_data.get("depots", {})
        write_acf(steamapps_dir / f"appmanifest_{source_app_id}.acf",
                  build_shared_acf(source_app_id, source_app_data,
                                   source_manifests, source_depots_info))

    # ---- Compression -----------------------------------------------------

    _PLATFORM_DISPLAY = {"windows": "Windows", "linux": "Linux", "macos": "macOS"}
    game_name    = sanitize_name(app_data.get("common", {}).get("name", str(app_id)))
    build_id     = app_data.get("depots", {}).get("branches", {}).get(branch, {}).get("buildid", "unknown")
    plat_display = _PLATFORM_DISPLAY.get(platform, platform)
    archive_stem = f"{game_name}.{build_id}.{plat_display}.{branch}"

    # ---- Crack step (Phase 3) -------------------------------------------
    gse_dir: Path | None = None
    if crack:
        _emit(on_event, kind="stage",
              stage_msg=f"Running crack: {crack}")
        # Suspend the live download display while the crack step runs — it
        # uses print() heavily (achievement fetch, DLL processing, prompts)
        # and would otherwise interleave with the redraw greenlet.
        _emit(on_event, kind="crack_started")
        try:
            if crack == "coldclient":
                from .crack.coldclient import crack_coldclient
                gse_dir = crack_coldclient(
                    app_id, app_data, dest, game_dest,
                    identity=crack_identity,
                    unstub_options=unstub_options,
                )
            elif crack == "gse":
                from .crack.gse import crack_game
                gse_dir = crack_game(
                    app_id, app_data, dest, game_dest,
                    identity=crack_identity,
                    experimental=experimental,
                    unstub_options=unstub_options,
                )
            else:
                raise ValueError(f"Unknown crack mode: {crack!r}")
        finally:
            _emit(on_event, kind="crack_finished")

    archives = compress_platform(
        dest, archive_stem, password, compression_level, volume_size,
        gse_dir=gse_dir,
        on_event=on_event,
    )
    return archives, manifest_records


# ---------------------------------------------------------------------------
# Top-level: download_app — orchestrates one or all platforms
# ---------------------------------------------------------------------------

def _move_archives(archives: list[Path], dest_dir: Path,
                   on_event: EventCallback | None) -> None:
    for archive in archives:
        target = dest_dir / archive.name
        shutil.move(str(archive), str(target))
        _emit(on_event, kind="stage",
              stage_msg=f"Moved {archive.name} → {dest_dir}")


def download_app(client, cdn, app_id: int, output_dir: Path,
                 platform: str, workers: int = 8,
                 password: str | None = None, compression_level: int = 0,
                 volume_size: int | None = None,
                 branch: str = "public",
                 crack: str | None = None,
                 experimental: bool = False,
                 unstub_options: dict | None = None,
                 depot_names: dict | None = None,
                 max_retries: int = 1,
                 language: str = "english",
                 crack_identity=None,
                 on_event: EventCallback | None = None,
                 ) -> tuple[list[Path], dict[str, list]]:
    """Download depots for app_id using an already-connected client and cdn.

    When platform is 'all', iterates each platform listed in app metadata into
    output_dir/{app_id}/{build_id}/{platform}/.  Otherwise downloads into
    output_dir/{app_id}/{build_id}/{platform}/.

    Returns (archives, platform_manifests) where platform_manifests maps
    platform key → list of (depot_id, depot_name, gid) tuples.

    Raises SessionDead if the CM connection times out after exhausting retries.
    """
    _, GeventTimeout, _, _, _, _ = _import_steam()

    app_info = None
    for attempt in range(max_retries + 1):
        try:
            app_info = client.get_product_info(apps=[app_id])
            break
        except GeventTimeout:
            if attempt < max_retries:
                _emit(on_event, kind="error",
                      error_msg="Timed out fetching product info, retrying")
            else:
                raise SessionDead("Timed out fetching product info.")

    app_data = app_info["apps"].get(app_id) if app_info else None
    if not app_data:
        _emit(on_event, kind="error",
              error_msg=f"No product info for app {app_id}")
        return [], {}

    build_id = app_data.get("depots", {}).get("branches", {}).get(branch, {}).get("buildid", "unknown")
    base = output_dir / str(app_id) / str(build_id)

    all_archives: list[Path] = []
    platform_manifests: dict[str, list] = {}

    if platform == "all":
        platforms = _get_available_platforms(app_data)
        if not platforms:
            _emit(on_event, kind="error",
                  error_msg="Could not detect any platforms from depot info")
            return [], {}
        for plat in platforms:
            _emit(on_event, kind="stage", stage_msg=f"Platform: {plat}")
            archives, records = _download_platform(
                cdn, client, app_id, app_data, base / plat, plat,
                branch, workers, password, compression_level, volume_size,
                crack, experimental, unstub_options,
                depot_names=depot_names, max_retries=max_retries,
                language=language, crack_identity=crack_identity,
                on_event=on_event,
            )
            _move_archives(archives, output_dir, on_event)
            all_archives.extend(output_dir / a.name for a in archives)
            platform_manifests[plat] = records
            plat_dir = base / plat
            if plat_dir.exists():
                shutil.rmtree(plat_dir)
    else:
        archives, records = _download_platform(
            cdn, client, app_id, app_data, base / platform, platform,
            branch, workers, password, compression_level, volume_size,
            crack, experimental, unstub_options,
            depot_names=depot_names, max_retries=max_retries,
            language=language, crack_identity=crack_identity,
            on_event=on_event,
        )
        _move_archives(archives, output_dir, on_event)
        all_archives.extend(output_dir / a.name for a in archives)
        platform_manifests[platform] = records

    app_dir = output_dir / str(app_id)
    if app_dir.exists():
        shutil.rmtree(app_dir)

    return all_archives, platform_manifests
