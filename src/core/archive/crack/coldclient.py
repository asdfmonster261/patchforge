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
    _download_image,
    _process_dll,
    _resolve_api_key,
    _resolve_user_config,
    _steam_image_url,
    _has_lan_multiplayer,
    _write_configs_user,
    db_save,
    detect_binary_arch,
    download_gbe_archive,
    fetch_achievements,
    fetch_dlcs,
    find_steam_apis,
    get_latest_gbe,
    is_linux_so,
    download_achievements,
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


def _build_steam_settings(appid: str, app_data: dict, settings_dir: Path,
                          identity) -> dict:
    """Populate steam_settings/ and return the user config dict."""
    settings_dir.mkdir(parents=True, exist_ok=True)

    (settings_dir / "steam_appid.txt").write_text(appid + "\n", encoding="utf-8")

    header_dest = settings_dir / "header.jpg"
    if not header_dest.exists():
        img_url = _steam_image_url(appid)
        if _download_image(img_url, header_dest):
            print("  [x] Header image saved to steam_settings/header.jpg")
        else:
            print("  [!] Could not download header image")

    print("\n  [ ] Fetching DLCs...")
    dlcs = fetch_dlcs(appid, use_db=True)
    if dlcs:
        with open(settings_dir / "configs.app.ini", "w", encoding="utf-8") as fh:
            fh.write("[app::dlcs]\n")
            fh.write("unlock_all=0\n")
            for dlc_id, name in dlcs.items():
                fh.write(f"{dlc_id}={name}\n")
        db_save(appid, dlcs)
        print(f"  [x] {len(dlcs)} DLC(s) written to configs.app.ini")
    else:
        print("  [x] No DLCs.")

    lang_data = app_data.get("common", {}).get("languages", {})
    langs = list(lang_data.keys()) if lang_data else ["english"]
    (settings_dir / "supported_languages.txt").write_text(
        "\n".join(langs) + "\n", encoding="utf-8"
    )
    print(f"  [x] Languages: {', '.join(langs)}")

    has_lan = _has_lan_multiplayer(app_data)
    if not has_lan:
        print("  [i] No LAN multiplayer detected — listen port will be skipped.")
    user_cfg = _resolve_user_config(identity, langs, has_lan=has_lan)
    _write_configs_user(settings_dir, user_cfg)
    print("  [x] configs.user.ini written.")

    print("\n  [ ] Fetching achievements...")
    print(f"  Available languages: {', '.join(langs)}")
    if identity.achievement_language and identity.achievement_language in langs:
        print(f"  Achievement language: {identity.achievement_language}  (from project)")
        ach_lang = identity.achievement_language
    else:
        from .gse import _prompt
        ach_lang = _prompt("Achievement language", user_cfg["language"])
        if ach_lang not in langs:
            ach_lang = user_cfg["language"]
        identity.achievement_language = ach_lang

    api_key = _resolve_api_key()
    achievements = fetch_achievements(appid, ach_lang, api_key=api_key)
    if achievements:
        print(f"  [ ] Downloading {len(achievements)} achievement(s)...")
        download_achievements(appid, achievements, settings_dir)
        write_configs_overlay(settings_dir)
        print("  [x] Achievements done.")
    else:
        print("  [x] No achievements found.")

    return user_cfg


def _deploy_loader(bits: str, appid: str, app_data: dict, game_dest: Path,
                   loader_dir: Path, emu_dir: Path, settings_dir: Path,
                   archive: Path) -> None:
    archive_loader_exe = f"steamclient_loader_{bits}.exe"

    launch_exe = _get_launch_exe(app_data)
    if launch_exe:
        exe_rel = Path(launch_exe)
        loader_exe = f"start_{Path(launch_exe).name}"
        print(f"  [i] Main exe (from Steam): {exe_rel}")
    else:
        main_exe = _find_main_exe(game_dest)
        if main_exe:
            try:
                exe_rel = main_exe.relative_to(game_dest)
            except ValueError:
                exe_rel = Path(main_exe.name)
            loader_exe = f"start_{main_exe.name}"
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
                     unstub_options: dict | None = None) -> Path | None:
    """Deploy the ColdClient loader for app_id into a gse_config_{app_id}/
    folder.  All ColdClient files land at game root level regardless of
    where steam_api.dll lives.  Linux .so files fall back to standard
    Goldberg.

    identity — CrackIdentity dataclass loaded from .xarchive.  Mutated in
    place when prompts fill in missing fields.

    Returns the gse_config directory, or None if no Steam API binary was found.
    """
    print("\n[ ] Setting up ColdClient loader...")
    appid = str(app_id)

    found = find_steam_apis(game_dest)
    gse_output_dir = dest / f"gse_config_{appid}"

    if not found:
        print(f"  [!] No Steam API binary found under {game_dest} — skipping.")
        unstub_exes(game_dest, gse_output_dir, unstub_options)
        return gse_output_dir if gse_output_dir.exists() else None

    dlls = [p for p in found if not is_linux_so(p)]
    sos  = [p for p in found if is_linux_so(p)]

    if sos and not dlls:
        print("  [i] Only Linux binary found — using standard Goldberg emulator.")
        from .gse import crack_game
        return crack_game(app_id, app_data, dest, game_dest,
                          identity=identity, unstub_options=unstub_options)

    dll_path = dlls[0]
    bits = detect_binary_arch(dll_path)
    print(f"  [i] Detected architecture: {bits}  ({dll_path.name})")

    loader_dir   = gse_output_dir / game_dest.name
    emu_dir      = loader_dir / "emu"
    settings_dir = emu_dir / "steam_settings"

    _build_steam_settings(appid, app_data, settings_dir, identity)

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

    if sos:
        print("\n  [i] Linux binary also found — applying standard Goldberg for Linux.")
        try:
            so_rel = sos[0].parent.relative_to(game_dest.parent)
            so_output_dir = gse_output_dir / so_rel
        except ValueError:
            so_output_dir = gse_output_dir
        _process_dll(sos[0], settings_dir, so_output_dir)
        if so_output_dir != loader_dir:
            alt_settings = so_output_dir / "steam_settings"
            if alt_settings.exists():
                shutil.rmtree(alt_settings)
            shutil.copytree(settings_dir, alt_settings)
            print(f"  [x] steam_settings copied to {so_output_dir}")

    unstub_exes(game_dest, loader_dir, unstub_options)

    print(f"\n[+] ColdClient setup complete — {gse_output_dir.name}/ at {gse_output_dir}")
    return gse_output_dir
