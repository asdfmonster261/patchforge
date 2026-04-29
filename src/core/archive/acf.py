"""Build and write Steam appmanifest .acf (VDF) files.

Pure data-shape code — no Steam network calls, no globals.  Thin port of
SteamArchiver/download/acf.py with cosmetic adjustments to match PatchForge
style.

The output format mirrors what Steam itself writes for appmanifest_<id>.acf
under steamapps/, so that resulting archives can be dropped into a real
Steam install directory and recognised by the client.
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Iterable


# ---------------------------------------------------------------------------
# VDF emit
# ---------------------------------------------------------------------------

def _vdf_dumps(data: dict, indent: int = 0) -> str:
    lines: list[str] = []
    prefix = "\t" * indent
    for key, value in data.items():
        if isinstance(value, dict):
            lines.append(f'{prefix}"{key}"')
            lines.append(f"{prefix}{{")
            inner = _vdf_dumps(value, indent + 1)
            if inner:
                lines.append(inner)
            lines.append(f"{prefix}}}")
        else:
            lines.append(f'{prefix}"{key}"\t\t"{value}"')
    return "\n".join(lines)


def write_acf(path: Path, data: dict) -> None:
    """Serialize data into Steam's VDF text format and write to path."""
    inner = _vdf_dumps(data, 1)
    Path(path).write_text(f'"AppState"\n{{\n{inner}\n}}\n')


# ---------------------------------------------------------------------------
# Builders
# ---------------------------------------------------------------------------

def build_app_acf(app_id: int, app_data: dict, game_manifests: list,
                  depots_info: dict,
                  dlc_data: Iterable[tuple[int, list]] | None = None) -> dict:
    """Build the ACF data dict for the main app.

    dlc_data is an iterable of (dlc_app_id, dlc_manifests) tuples whose depots
    are folded into InstalledDepots with a dlcappid field, matching the format
    Steam itself writes.
    """
    name       = app_data.get("common", {}).get("name", str(app_id))
    installdir = app_data.get("config", {}).get("installdir", str(app_id))
    buildid    = app_data.get("depots", {}).get("branches", {}).get("public", {}).get("buildid", "0")

    dlc_data = list(dlc_data or [])

    all_manifests  = game_manifests + [m for _, ms in dlc_data for m in ms]
    size_on_disk   = sum(m.metadata.cb_disk_original   for m in all_manifests)
    bytes_download = sum(m.metadata.cb_disk_compressed for m in all_manifests)
    bytes_stage    = size_on_disk

    has_dlc_depots = bool(dlc_data) or any(
        "dlcappid" in depots_info.get(str(m.depot_id), {}) for m in game_manifests
    )
    download_type = "3" if has_dlc_depots else "0"

    installed_depots: dict[str, dict] = {}
    for m in game_manifests:
        entry = {
            "manifest": str(m.gid),
            "size":     str(m.metadata.cb_disk_original),
        }
        dlcappid = depots_info.get(str(m.depot_id), {}).get("dlcappid")
        if dlcappid:
            entry["dlcappid"] = str(dlcappid)
        installed_depots[str(m.depot_id)] = entry

    for dlc_app_id, dlc_manifests in dlc_data:
        for m in dlc_manifests:
            installed_depots[str(m.depot_id)] = {
                "manifest": str(m.gid),
                "size":     str(m.metadata.cb_disk_original),
                "dlcappid": str(dlc_app_id),
            }

    shared_depots = {
        key: str(info["depotfromapp"])
        for key, info in depots_info.items()
        if key.isdigit() and "depotfromapp" in info
    }

    acf: dict = {
        "appid":                          str(app_id),
        "Universe":                       "1",
        "LauncherPath":                   "0",
        "name":                           name,
        "StateFlags":                     "4",
        "installdir":                     installdir,
        "LastUpdated":                    str(int(time.time())),
        "LastPlayed":                     "0",
        "SizeOnDisk":                     str(size_on_disk),
        "StagingSize":                    "0",
        "buildid":                        str(buildid),
        "LastOwner":                      "0",
        "DownloadType":                   download_type,
        "UpdateResult":                   "0",
        "BytesToDownload":                str(bytes_download),
        "BytesDownloaded":                str(bytes_download),
        "BytesToStage":                   str(bytes_stage),
        "BytesStaged":                    str(bytes_stage),
        "TargetBuildID":                  str(buildid),
        "AutoUpdateBehavior":             "0",
        "AllowOtherDownloadsWhileRunning": "0",
        "ScheduledAutoUpdate":            "0",
        "InstalledDepots":                installed_depots,
    }

    if shared_depots:
        acf["SharedDepots"] = shared_depots

    acf["UserConfig"]    = {}
    acf["MountedConfig"] = {}
    return acf


def build_shared_acf(source_app_id: int, source_app_data: dict,
                     shared_manifests: list, source_depots_info: dict) -> dict:
    """Build the ACF data dict for a shared depot source app
    (e.g. Steamworks Common Redistributables)."""
    name       = source_app_data.get("common", {}).get("name", str(source_app_id))
    installdir = source_app_data.get("config", {}).get("installdir", str(source_app_id))
    buildid    = source_app_data.get("depots", {}).get("branches", {}).get("public", {}).get("buildid", "0")

    size_on_disk = sum(m.metadata.cb_disk_original for m in shared_manifests)

    installed_depots = {
        str(m.depot_id): {
            "manifest": str(m.gid),
            "size":     str(m.metadata.cb_disk_original),
        }
        for m in shared_manifests
    }

    install_scripts = {
        str(depot_id): info["installscript"]
        for depot_id, info in source_depots_info.items()
        if isinstance(info, dict) and "installscript" in info
    }

    acf: dict = {
        "appid":                          str(source_app_id),
        "Universe":                       "1",
        "LauncherPath":                   "0",
        "name":                           name,
        "StateFlags":                     "4",
        "installdir":                     installdir,
        "LastUpdated":                    str(int(time.time())),
        "LastPlayed":                     "0",
        "SizeOnDisk":                     str(size_on_disk),
        "StagingSize":                    "0",
        "buildid":                        str(buildid),
        "LastOwner":                      "0",
        "DownloadType":                   "0",
        "AutoUpdateBehavior":             "0",
        "AllowOtherDownloadsWhileRunning": "0",
        "ScheduledAutoUpdate":            "0",
        "InstalledDepots":                installed_depots,
    }

    if install_scripts:
        acf["InstallScripts"] = install_scripts

    acf["UserConfig"]    = {}
    acf["MountedConfig"] = {}
    return acf
