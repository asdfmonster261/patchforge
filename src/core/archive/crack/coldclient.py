"""ColdClient loader integration.

Rewritten in PatchForge style from SteamArchiver/crack/coldclient.py.

Deploys steamclient_loader_x*.exe + steamclient*.dll instead of replacing
steam_api.dll.  Linux binaries fall back to the standard Goldberg path.
"""

from __future__ import annotations

import shutil
from pathlib import Path

from ..utils import run_in_thread
from .gse import (
    build_shared_settings,
    copy_settings_payload,
    detect_binary_arch,
    download_gbe_archive,
    find_steam_apis,
    get_latest_gbe,
    is_linux_so,
    write_configs_overlay,
    unstub_exes,
)


_SKIP_EXE_TOKENS = (
    "unins", "setup", "install", "redist", "crash", "report",
    "update", "patch", "vc_", "dotnet", "directx", "dxsetup", "unitycrash",
)

# Paths inside emu-win-release.7z
_ARCHIVE_BASE = "release/steamclient_experimental"


def _get_launch_exe(app_data: dict) -> str | None:
    """Return the Windows default launch executable from Steam app data."""
    launch = app_data.get("config", {}).get("launch", {})
    candidates = []
    for entry in launch.values():
        oslist = entry.get("config", {}).get("oslist", "")
        if oslist and "windows" not in oslist:
            continue
        candidates.append(entry)
    for entry in candidates:
        if entry.get("type") == "default":
            return entry.get("executable")
    if candidates:
        return candidates[0].get("executable")
    return None


def _find_main_exe(game_root: Path) -> Path | None:
    best: Path | None = None
    best_size = 0
    for exe in game_root.rglob("*.exe"):
        if any(tok in exe.name.lower() for tok in _SKIP_EXE_TOKENS):
            continue
        try:
            size = exe.stat().st_size
            if size > best_size:
                best_size = size
                best = exe
        except OSError:
            continue
    return best


def _extract_from_archive(archive: Path, inner: str, dest: Path) -> bool:
    def _do_extract():
        import libarchive
        found = False
        with libarchive.file_reader(str(archive)) as arc:
            for entry in arc:
                if entry.pathname == inner:
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    with open(dest, "wb") as fh:
                        for block in entry.get_blocks():
                            fh.write(block)
                    found = True
                    break
        if not found:
            raise FileNotFoundError(f"{inner!r} not found in archive.")
    try:
        run_in_thread(_do_extract)
        return True
    except Exception as exc:
        print(f"  [!] Extraction error for {inner!r}: {exc}")
        return False


def _populate_steam_settings(appid: str, app_data: dict, settings_dir: Path,
                             identity, *,
                             shared_settings: Path | None) -> None:
    """Populate ColdClient's emu/steam_settings/.  When shared_settings is
    provided, copy the canonical payload from disk and add ColdClient's
    extra configs.overlay.ini on top.  Otherwise build from scratch."""
    if shared_settings is not None and shared_settings.exists():
        copy_settings_payload(shared_settings, settings_dir)
        write_configs_overlay(settings_dir)
        return
    build_shared_settings(appid, app_data, identity, settings_dir,
                          want_overlay=True)


def _deploy_loader(bits: str, appid: str, app_data: dict, game_dest: Path,
                   loader_dir: Path, emu_dir: Path, settings_dir: Path,
                   archive: Path) -> None:
    archive_loader_exe = f"steamclient_loader_{bits}.exe"

    launch_exe = _get_launch_exe(app_data)
    if launch_exe:
        exe_rel = Path(launch_exe)
        loader_exe = f"start_{Path(launch_exe).name}".replace(" ", "_")
        print(f"  [i] Main exe (from Steam): {exe_rel}")
    else:
        main_exe = _find_main_exe(game_dest)
        if main_exe:
            try:
                exe_rel = main_exe.relative_to(game_dest)
            except ValueError:
                exe_rel = Path(main_exe.name)
            loader_exe = f"start_{main_exe.name}".replace(" ", "_")
            print(f"  [i] Main exe (by size): {exe_rel}")
        else:
            exe_rel = Path("")
            loader_exe = archive_loader_exe
            print("  [!] Could not detect main exe — ColdClientLoader.ini Exe= will be blank")

    print("\n  [ ] Deploying ColdClient loader files...")

    emu_dir.mkdir(parents=True, exist_ok=True)

    if _extract_from_archive(archive,
                             f"{_ARCHIVE_BASE}/{archive_loader_exe}",
                             loader_dir / loader_exe):
        print(f"  [x] {loader_exe}")

    for client_dll in ("steamclient.dll", "steamclient64.dll"):
        if _extract_from_archive(archive,
                                 f"{_ARCHIVE_BASE}/{client_dll}",
                                 emu_dir / client_dll):
            print(f"  [x] emu/{client_dll}")

    load_dlls_dir = settings_dir / "load_dlls"
    load_dlls_dir.mkdir(parents=True, exist_ok=True)
    for overlay_dll in ("GameOverlayRenderer.dll", "GameOverlayRenderer64.dll"):
        if _extract_from_archive(archive,
                                 f"{_ARCHIVE_BASE}/{overlay_dll}",
                                 load_dlls_dir / overlay_dll):
            print(f"  [x] emu/steam_settings/load_dlls/{overlay_dll}")

    with open(settings_dir / "configs.main.ini", "w", encoding="utf-8") as fh:
        fh.write("[main::connectivity]\n")
        fh.write("disable_lan_only=1\n")
        fh.write("offline=0\n")
    print("  [x] configs.main.ini written")

    ini_path = loader_dir / "ColdClientLoader.ini"
    with open(ini_path, "w", encoding="utf-8") as fh:
        fh.write("[SteamClient]\n")
        fh.write(f"Exe={exe_rel}\n")
        fh.write("ExeRunDir=\n")
        fh.write("ExeCommandLine=\n")
        fh.write(f"AppId={appid}\n")
        fh.write("SteamClientDll=emu/steamclient.dll\n")
        fh.write("SteamClient64Dll=emu/steamclient64.dll\n")
        fh.write("\n[Injection]\n")
        fh.write("ForceInjectSteamClient=0\n")
        fh.write("ForceInjectGameOverlayRenderer=0\n")
        fh.write("DllsToInjectFolder=\n")
        fh.write("IgnoreInjectionError=1\n")
        fh.write("IgnoreLoaderArchDifference=0\n")
        fh.write("\n[Persistence]\n")
        fh.write("Mode=0\n")
        fh.write("\n[Debug]\n")
        fh.write("ResumeByDebugger=0\n")
    print("  [x] ColdClientLoader.ini written")


def crack_coldclient(app_id: int, app_data: dict, dest: Path, game_dest: Path,
                     *, identity,
                     unstub_options: dict | None = None,
                     output_base: Path | None = None,
                     shared_settings: Path | None = None) -> Path | None:
    """Deploy the ColdClient loader for app_id into a gse_config_{app_id}/coldclient/
    folder.  All ColdClient files land at game root level regardless of
    where steam_api.dll lives.

    identity — CrackIdentity dataclass loaded from .xarchive.  Mutated in
    place when prompts fill in missing fields.

    Linux/macOS binaries are NOT handled here — the orchestrator skips
    coldclient on non-Windows platforms.  Use --crack all to also generate
    the Goldberg emulator subdir on the same archive.

    Returns the combined gse_config_{app_id}/ directory, or None if no
    Windows Steam API binary was found.
    """
    print("\n[ ] Setting up ColdClient loader...")
    appid = str(app_id)

    combined_dir   = output_base if output_base is not None else dest / f"gse_config_{appid}"
    coldclient_dir = combined_dir / "coldclient"

    found = find_steam_apis(game_dest)

    if not found:
        print(f"  [!] No Steam API binary found under {game_dest} — skipping.")
        unstub_exes(game_dest, coldclient_dir, unstub_options)
        return combined_dir if combined_dir.exists() else None

    dlls = [p for p in found if not is_linux_so(p)]

    if not dlls:
        print("  [!] No Windows Steam API DLL found — ColdClient is Windows-only. "
              "Use --crack all (or --crack gse) for Linux/macOS binaries.")
        return combined_dir if combined_dir.exists() else None

    dll_path = dlls[0]
    bits = detect_binary_arch(dll_path)
    print(f"  [i] Detected architecture: {bits}  ({dll_path.name})")

    loader_dir   = coldclient_dir / game_dest.name
    emu_dir      = loader_dir / "emu"
    settings_dir = emu_dir / "steam_settings"

    _populate_steam_settings(appid, app_data, settings_dir, identity,
                             shared_settings=shared_settings)

    print("\n  [ ] Fetching latest Goldberg release (Windows)...")
    release_name, asset_filename = get_latest_gbe(linux=False)
    if not asset_filename:
        print("  [!] Could not find a Windows release asset on GitHub.")
        return None

    print(f"  [i] Latest release: {release_name}  ({asset_filename})")
    archive = download_gbe_archive(asset_filename, release_name)
    if not archive:
        return None

    _deploy_loader(bits, appid, app_data, game_dest,
                   loader_dir, emu_dir, settings_dir, archive)

    unstub_exes(game_dest, loader_dir, unstub_options)

    print(f"\n[+] ColdClient setup complete — {combined_dir.name}/coldclient/ at {coldclient_dir}")
    return combined_dir
