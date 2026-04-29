"""Goldberg Steam Emulator integration.

Rewritten in PatchForge style from SteamArchiver/crack/gse.py.  The actual
DRM unpacking is delegated to the vendored unstub/ tree (which is NOT
rewritten — see crack/unstub/ for why).

Source of values:
  - Steam Web API key  → archive_credentials.json (Credentials.web_api_key);
                         prompted + saved if missing
  - Crack identity     → CrackIdentity dataclass passed in by the CLI
                         (loaded from .xarchive); prompted in-place when
                         a field is empty
  - Goldberg releases  → cached under ~/.cache/patchforge/archive/gbe/
  - DLC name database  → ~/.cache/patchforge/archive/dlc_db/<appid>.ini
"""

from __future__ import annotations

import json
import re
import shutil
import struct
from pathlib import Path

from ..utils import dlc_db_dir, gbe_dir, run_in_thread


_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    )
}

GBE_VERSION_FILE = lambda: gbe_dir() / "release.txt"   # noqa: E731

# GBE language code → Steam API language name (used by GetSchemaForGame)
_GBE_TO_API_LANG: dict[str, str] = {
    "arabic":     "arabic",      "bulgarian":  "bulgarian",
    "schinese":   "schinese",    "tchinese":   "tchinese",
    "czech":      "czech",       "danish":     "danish",
    "dutch":      "dutch",       "english":    "english",
    "finnish":    "finnish",     "french":     "french",
    "german":     "german",      "greek":      "greek",
    "hungarian":  "hungarian",   "indonesian": "indonesian",
    "italian":    "italian",     "japanese":   "japanese",
    "koreana":    "koreana",     "norwegian":  "norwegian",
    "polish":     "polish",      "portuguese": "portuguese",
    "brazilian":  "brazilian",   "romanian":   "romanian",
    "russian":    "russian",     "spanish":    "spanish",
    "latam":      "latam",       "swedish":    "swedish",
    "thai":       "thai",        "turkish":    "turkish",
    "ukrainian":  "ukrainian",   "vietnamese": "vietnamese",
}

# Byte patterns used to extract steam_interfaces.txt from a Steam API DLL.
_INTERFACE_PATTERNS: list[bytes] = [
    rb'STEAMAPPLIST_INTERFACE_VERSION\d{3}',
    rb'STEAMAPPS_INTERFACE_VERSION\d{3}',
    rb'STEAMAPPTICKET_INTERFACE_VERSION\d{3}',
    rb'SteamClient\d{3}',
    rb'STEAMCONTROLLER_INTERFACE_VERSION\d*',
    rb'SteamController\d{3}',
    rb'SteamInput\d{3}',
    rb'SteamFriends\d{3}',
    rb'SteamGameCoordinator\d{3}',
    rb'SteamGameServer\d{3}',
    rb'SteamGameServerStats\d{3}',
    rb'SteamGameStats\d{3}',
    rb'STEAMHTMLSURFACE_INTERFACE_VERSION_\d{3}',
    rb'STEAMHTTP_INTERFACE_VERSION\d{3}',
    rb'STEAMINVENTORY_INTERFACE_V\d{3}',
    rb'SteamMasterServerUpdater\d{3}',
    rb'SteamMatchMaking\d{3}',
    rb'SteamMatchMakingServers\d{3}',
    rb'SteamMatchGameSearch\d{3}',
    rb'STEAMMUSIC_INTERFACE_VERSION\d{3}',
    rb'STEAMMUSICREMOTE_INTERFACE_VERSION\d{3}',
    rb'SteamNetworking\d{3}',
    rb'SteamNetworkingMessages\d{3}',
    rb'SteamNetworkingSockets\d{3}',
    rb'SteamNetworkingUtils\d{3}',
    rb'STEAMPARENTALSETTINGS_INTERFACE_VERSION\d{3}',
    rb'SteamParties\d{3}',
    rb'STEAMREMOTEPLAY_INTERFACE_VERSION\d{3}',
    rb'STEAMREMOTESTORAGE_INTERFACE_VERSION\d{3}',
    rb'STEAMSCREENSHOTS_INTERFACE_VERSION\d{3}',
    rb'STEAMTIMELINE_INTERFACE_V\d{3}',
    rb'STEAMUGC_INTERFACE_VERSION\d{3}',
    rb'STEAMUNIFIEDMESSAGES_INTERFACE_VERSION\d{3}',
    rb'SteamUser\d{3}',
    rb'STEAMUSERSTATS_INTERFACE_VERSION\d{3}',
    rb'SteamUtils\d{3}',
    rb'STEAMVIDEO_INTERFACE_V\d{3}',
]


# ---------------------------------------------------------------------------
# Networking helpers
# ---------------------------------------------------------------------------

def _http_get(url: str, stream: bool = False, extra_headers: dict | None = None,
              **kwargs):
    import requests
    headers = {**_HEADERS, **(extra_headers or {})}
    try:
        resp = requests.get(url, headers=headers, timeout=20, stream=stream, **kwargs)
        resp.raise_for_status()
        return resp
    except requests.RequestException as exc:
        print(f"    [!] HTTP error for {url!r}: {exc}")
        return None


def _prompt(message: str, default: str = "") -> str:
    suffix = f" [{default}]" if default else ""
    try:
        value = input(f"  {message}{suffix}: ").strip()
    except (EOFError, KeyboardInterrupt):
        print()
        import sys
        sys.exit(0)
    return value if value else default


def _steam_image_url(appid: str) -> str:
    """Return the best-available header image URL for a Steam app.

    Tries the legacy flat CDN path first; falls back to the appdetails API
    for newer titles whose assets live on a content-addressed CDN.
    """
    import requests
    flat = f"https://cdn.cloudflare.steamstatic.com/steam/apps/{appid}/header.jpg"
    try:
        if requests.head(flat, timeout=5).ok:
            return flat
    except Exception:
        return flat
    try:
        r = requests.get(
            f"https://store.steampowered.com/api/appdetails?appids={appid}&filters=basic",
            timeout=8,
        )
        if r.ok:
            payload = r.json().get(str(appid), {})
            if payload.get("success"):
                url = payload["data"].get("header_image", "")
                if url:
                    return url
    except Exception:
        pass
    return flat


def _download_image(url: str, dest: Path) -> bool:
    if not url or dest.exists():
        return dest.exists()
    resp = _http_get(url)
    if resp:
        dest.write_bytes(resp.content)
        return True
    return False


# ---------------------------------------------------------------------------
# Steam Web API key (lives in archive_credentials.json)
# ---------------------------------------------------------------------------

def _resolve_api_key() -> str:
    """Return the Web API key, loading from archive_credentials.json or
    prompting and saving when absent."""
    from .. import credentials as creds_mod
    creds = creds_mod.load()
    if creds.web_api_key:
        return creds.web_api_key

    print()
    print("  A Steam Web API key is required to fetch achievement data.")
    print("  Get yours at: https://steamcommunity.com/dev/apikey")
    print()
    try:
        key = input("  Enter your Steam API key: ").strip()
    except (EOFError, KeyboardInterrupt):
        print()
        import sys
        sys.exit(0)

    if not key:
        print("  [!] No API key provided — achievement fetching will be skipped.")
        return ""

    creds.web_api_key = key
    creds_mod.save(creds)
    print(f"  [i] Key saved to {creds_mod.credentials_path()}")
    return key


# ---------------------------------------------------------------------------
# DLC name resolution
# ---------------------------------------------------------------------------

def _appdetails(appid: str) -> dict | None:
    url = f"https://store.steampowered.com/api/appdetails/?filters=basic&appids={appid}"
    resp = _http_get(url)
    if not resp:
        return None
    try:
        data = resp.json()
        entry = data.get(str(appid), {})
        if entry.get("success") and entry.get("data"):
            return entry["data"]
    except Exception:
        pass
    return None


def _db_path(appid: str) -> Path:
    return dlc_db_dir() / f"{appid}.ini"


def db_load(appid: str) -> dict[str, str]:
    """Load cached DLC names for app, or {} when no cache exists."""
    path = _db_path(appid)
    result: dict[str, str] = {}
    if not path.exists():
        return result
    try:
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line.startswith(";") or line.startswith("[") or "=" not in line:
                    continue
                k, _, v = line.partition("=")
                result[k.strip()] = v.strip()
    except Exception:
        pass
    return result


def db_save(appid: str, dlcs: dict[str, str]) -> None:
    dlc_db_dir().mkdir(parents=True, exist_ok=True)
    existing = db_load(appid)
    existing.update(dlcs)
    path = _db_path(appid)

    def sort_key(item: tuple) -> int:
        return int(item[0]) if item[0].isdigit() else 0

    with open(path, "w", encoding="utf-8") as fh:
        for dlc_id, name in sorted(existing.items(), key=sort_key):
            fh.write(f"{dlc_id} = {name}\n")


def _dlc_from_steam(dlc_id: str) -> str | None:
    data = _appdetails(dlc_id)
    return data.get("name") if data else None


def _dlc_from_steamdb(dlc_id: str) -> str | None:
    resp = _http_get(f"https://steamdb.info/app/{dlc_id}/")
    if not resp:
        return None
    m = re.search(r'<h1[^>]*itemprop="name"[^>]*>\s*([^<\n]+)', resp.text)
    return m.group(1).strip() if m else None


def _dlc_from_steam_applist(dlc_id: str, applist: dict[str, str]) -> str | None:
    return applist.get(dlc_id)


def _build_applist() -> dict[str, str]:
    resp = _http_get("https://api.steampowered.com/ISteamApps/GetAppList/v2/")
    if not resp:
        return {}
    try:
        apps = resp.json()["applist"]["apps"]
        return {str(a["appid"]): a["name"] for a in apps}
    except Exception:
        return {}


def fetch_dlcs(appid: str, use_db: bool = True) -> dict[str, str]:
    db = db_load(appid) if use_db else {}

    print("  [ ] Fetching DLC list from Steam API...")
    dlc_ids: list[str] = []
    data = _appdetails(appid)
    if data:
        dlc_ids = [str(d) for d in data.get("dlc", [])]

    if not dlc_ids and not db:
        print("  [i] No DLCs found.")
        return {}

    all_ids = list(dict.fromkeys(dlc_ids + list(db.keys())))
    total = len(all_ids)
    if total == 0:
        return {}

    applist: dict[str, str] = {}
    result: dict[str, str] = {}

    for idx, dlc_id in enumerate(all_ids, 1):
        name: str | None = None
        source = ""

        if dlc_id in db:
            name, source = db[dlc_id], "[Database]"
        if not name:
            name = _dlc_from_steam(dlc_id)
            if name:
                source = "[Steam]"
        if not name:
            name = _dlc_from_steamdb(dlc_id)
            if name:
                source = "[SteamDB]"
        if not name:
            if not applist:
                print("  [ ] Loading full Steam app list...")
                applist = _build_applist()
            name = _dlc_from_steam_applist(dlc_id, applist)
            if name:
                source = "[Steam API]"
        if not name:
            name, source = "Unknown", ""

        result[dlc_id] = name
        print(f"  [{idx}/{total}] {dlc_id} = {name}  {source}")

    return result


# ---------------------------------------------------------------------------
# Crack identity (Steam64, username, language, listen port)
# ---------------------------------------------------------------------------

def _has_lan_multiplayer(app_data: dict) -> bool:
    """True if the app's category list includes LAN Multi-player or LAN Co-op."""
    cats = app_data.get("common", {}).get("categories", {})
    cat_ids = {v.get("categoryid") for v in cats.values() if isinstance(v, dict)}
    return bool(cat_ids & {7, 15})  # 7 = LAN Multi-player, 15 = LAN Co-op


def _resolve_user_config(identity, langs: list[str], has_lan: bool) -> dict:
    """Fill any missing CrackIdentity fields by prompting; return the dict
    Goldberg expects.  Mutates `identity` in place when prompts produce values
    so callers can persist them back to .xarchive afterwards."""
    import random

    print()
    print("  -- User Configuration ----------------------------------")

    if identity.steam_id:
        print(f"  Steam64 ID:  {identity.steam_id}  (from project)")
    else:
        raw = _prompt("Steam64 ID (blank = random)")
        if not raw:
            identity.steam_id = random.randint(76561197960265729, 76561198999999998)
            print(f"    Generated: {identity.steam_id}")
        else:
            try:
                identity.steam_id = int(raw)
            except ValueError:
                print("  [!] Not a valid SteamID, generated random instead.")
                identity.steam_id = random.randint(76561197960265729, 76561198999999998)

    if identity.username:
        print(f"  Username:    {identity.username}  (from project)")
    else:
        identity.username = _prompt("Username", "Player")

    print(f"  Supported languages: {', '.join(langs)}")
    if identity.language and identity.language in langs:
        print(f"  Language:    {identity.language}  (from project)")
    else:
        default_lang = identity.language if identity.language else (langs[0] if langs else "english")
        identity.language = _prompt("Language", default_lang)
        if identity.language not in langs:
            identity.language = langs[0] if langs else "english"

    listen_port: int | None = None
    if has_lan:
        if identity.listen_port:
            print(f"  Listen port: {identity.listen_port}  (from project)")
            listen_port = identity.listen_port
        else:
            raw = _prompt("Listen port", "47584")
            try:
                identity.listen_port = int(raw)
            except ValueError:
                identity.listen_port = 47584
            listen_port = identity.listen_port
    else:
        print("  Listen port: skipped (no LAN multiplayer)")

    return {
        "steam_id":    str(identity.steam_id),
        "username":    identity.username,
        "language":    identity.language,
        "listen_port": str(listen_port) if listen_port else None,
    }


def _write_configs_user(settings_dir: Path, cfg: dict) -> None:
    path = settings_dir / "configs.user.ini"
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("[user::general]\n")
        fh.write(f"account_name={cfg['username']}\n")
        fh.write(f"account_steamid={cfg['steam_id']}\n")
        fh.write(f"language={cfg['language']}\n")
        fh.write("\n[user::saves]\n")
        fh.write("# local_save_path=\n")
        if cfg["listen_port"] is not None:
            fh.write("\n[connectivity::general]\n")
            fh.write(f"listen_port={cfg['listen_port']}\n")


# ---------------------------------------------------------------------------
# Achievements
# ---------------------------------------------------------------------------

def fetch_achievements(appid: str, gbe_lang: str = "english",
                       api_key: str = "") -> list[dict]:
    api_lang = _GBE_TO_API_LANG.get(gbe_lang, "english")
    resp = None
    if api_key:
        url = (
            "https://api.steampowered.com/ISteamUserStats/GetSchemaForGame/v2/"
            f"?key={api_key}&appid={appid}&l={api_lang}&format=json"
        )
        resp = _http_get(url)
    if resp:
        try:
            schema = resp.json()
            ach_list = (
                schema.get("game", {})
                       .get("availableGameStats", {})
                       .get("achievements", [])
            )
            if ach_list:
                return [
                    {
                        "name":        a.get("name", ""),
                        "displayName": a.get("displayName", a.get("name", "")),
                        "description": a.get("description", ""),
                        "hidden":      int(a.get("hidden", 0)),
                        "icon":        a.get("icon", ""),
                        "icongray":    a.get("icongray", ""),
                    }
                    for a in ach_list
                ]
        except Exception:
            pass

    print("  [i] Schema API returned nothing, trying community stats page...")
    resp2 = _http_get(
        f"https://steamcommunity.com/stats/{appid}/achievements/",
        extra_headers={"Accept-Language": api_lang},
    )
    if not resp2:
        return []

    results: list[dict] = []
    for m in re.finditer(
        r'images/apps/\d+/([a-f0-9]+\.jpg)[^>]*>'
        r'.*?achievePercent[^>]*>[^<]*</div>'
        r'\s*<h3>([^<]*)</h3><h5>([^<]*)</h5>',
        resp2.text,
        re.DOTALL,
    ):
        icon, dname, desc = m.group(1), m.group(2).strip(), m.group(3).strip()
        results.append({
            "name":        dname,
            "displayName": dname,
            "description": desc,
            "hidden":      0,
            "icon":        f"https://cdn.akamai.steamstatic.com/steamcommunity/public/images/apps/{appid}/{icon}",
            "icongray":    "",
        })
    return results


def download_achievements(appid: str, achievements: list[dict],
                          settings_dir: Path) -> None:
    images_dir = settings_dir / "images"
    images_dir.mkdir(exist_ok=True)
    total = len(achievements)
    entries: list[dict] = []

    for idx, ach in enumerate(achievements, 1):
        print(f"  [{idx}/{total}] {ach['name']}")
        icon_rel = icongray_rel = ""

        icon_url = ach.get("icon", "")
        if icon_url:
            fn = Path(icon_url).name
            if _download_image(icon_url, images_dir / fn):
                icon_rel = f"images/{fn}"

        icongray_url = ach.get("icongray", "")
        if icongray_url:
            fn = Path(icongray_url).name
            if _download_image(icongray_url, images_dir / fn):
                icongray_rel = f"images/{fn}"

        entries.append({
            "description": ach.get("description", ""),
            "displayName": ach.get("displayName", ach["name"]),
            "hidden":      ach.get("hidden", 0),
            "icon":        icon_rel,
            "icongray":    icongray_rel,
            "name":        ach["name"],
        })

    with open(settings_dir / "achievements.json", "w", encoding="utf-8") as fh:
        json.dump(entries, fh, indent=2, ensure_ascii=False)

    if not any(images_dir.iterdir()):
        images_dir.rmdir()


def write_configs_overlay(settings_dir: Path) -> None:
    with open(settings_dir / "configs.overlay.ini", "w") as fh:
        fh.write("[overlay::general]\n")
        fh.write("enable_experimental_overlay=1\n\n")
        fh.write("[overlay::appearance]\n")
        fh.write("Notification_Rounding=10.0\n")
        fh.write("Notification_Margin_x=5.0\n")
        fh.write("Notification_Margin_y=5.0\n")
        fh.write("Notification_Animation=0.35\n")
        fh.write("PosAchievement=bot_right\n")


# ---------------------------------------------------------------------------
# Steam API binary discovery + arch detection
# ---------------------------------------------------------------------------

def is_linux_so(path: Path) -> bool:
    return path.name == "libsteam_api.so"


def detect_binary_arch(path: Path) -> str:
    try:
        with open(path, "rb") as fh:
            magic = fh.read(4)
            if magic == b"\x7fELF":
                ei_class = struct.unpack("B", fh.read(1))[0]
                return "x32" if ei_class == 1 else "x64"
            if magic[:2] == b"MZ":
                fh.seek(0x3C)
                pe_offset = struct.unpack("<I", fh.read(4))[0]
                fh.seek(pe_offset)
                if fh.read(4) == b"PE\x00\x00":
                    machine = struct.unpack("<H", fh.read(2))[0]
                    return "x32" if machine == 0x014C else "x64"
    except Exception:
        pass
    return "x64"


def extract_steam_interfaces(dll_path: Path) -> list[str]:
    with open(dll_path, "rb") as fh:
        data = fh.read()
    found: set[str] = set()
    for pattern in _INTERFACE_PATTERNS:
        for m in re.finditer(pattern, data):
            try:
                found.add(m.group().decode("ascii"))
            except UnicodeDecodeError:
                pass
    return sorted(found)


_API_NAMES = ("steam_api64.dll", "steam_api.dll", "libsteam_api.so")


def find_steam_apis(game_root: Path) -> list[Path]:
    buckets: dict[str, list[Path]] = {name: [] for name in _API_NAMES}
    for path in game_root.rglob("*"):
        if path.name in buckets:
            buckets[path.name].append(path)
    for name in _API_NAMES:
        buckets[name].sort(key=lambda p: len(p.parts))
    return [p for name in _API_NAMES for p in buckets[name]]


# ---------------------------------------------------------------------------
# Goldberg release download / cache
# ---------------------------------------------------------------------------

def get_latest_gbe(linux: bool = False) -> tuple[str, str]:
    resp = _http_get("https://api.github.com/repos/Detanup01/gbe_fork/releases/latest")
    if not resp:
        return "", ""
    try:
        data = resp.json()
        release_name = data.get("name", "")
        keyword = "emu-linux-release" if linux else "emu-win-release"
        for asset in data.get("assets", []):
            name = asset.get("name", "")
            if keyword in name:
                return release_name, name
    except Exception:
        pass
    return "", ""


def _read_cached_release() -> str:
    try:
        return GBE_VERSION_FILE().read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        return ""


def _write_cached_release(release_name: str) -> None:
    GBE_VERSION_FILE().parent.mkdir(parents=True, exist_ok=True)
    GBE_VERSION_FILE().write_text(release_name + "\n", encoding="utf-8")


def download_gbe_archive(asset_filename: str, release_name: str) -> Path | None:
    import requests
    gbe_dir().mkdir(parents=True, exist_ok=True)
    dest = gbe_dir() / asset_filename

    cached_release = _read_cached_release()
    is_new_release = cached_release != release_name

    if not is_new_release and dest.exists():
        print(f"  [i] Already up to date: {release_name}")
        return dest

    if is_new_release and cached_release:
        print(f"  [i] New release detected: {release_name!r} (was {cached_release!r})")
        for old in gbe_dir().iterdir():
            old.unlink(missing_ok=True)

    url = ("https://github.com/Detanup01/gbe_fork"
           f"/releases/latest/download/{asset_filename}")
    print(f"  [ ] Downloading {asset_filename}...")
    try:
        resp = requests.get(url, headers=_HEADERS, stream=True, timeout=120)
        resp.raise_for_status()
        total_bytes = int(resp.headers.get("Content-Length", 0))
        received = 0
        with open(dest, "wb") as fh:
            for chunk in resp.iter_content(chunk_size=65536):
                fh.write(chunk)
                received += len(chunk)
                if total_bytes:
                    pct = received * 100 // total_bytes
                    print(f"\r      {pct}%", end="", flush=True)
        print()
        _write_cached_release(release_name)
        return dest
    except Exception as exc:
        print(f"\n  [!] Download failed: {exc}")
        dest.unlink(missing_ok=True)
        return None


def apply_goldberg(original: Path, archive: Path, bits: str, api_name: str,
                   output_dir: Path, experimental: bool = False,
                   linux: bool = False) -> bool:
    variant = "experimental" if experimental else "regular"

    if linux:
        file_name = "libsteam_api.so"
        inner     = f"release/{variant}/{bits}/{file_name}"
        backup    = output_dir / "libsteam_api_o.so"
        target    = output_dir / "libsteam_api.so"
    else:
        file_name = f"{api_name}.dll"
        inner     = f"release/{variant}/{bits}/{file_name}"
        backup    = output_dir / f"{api_name}_o.dll"
        target    = output_dir / f"{api_name}.dll"

    output_dir.mkdir(parents=True, exist_ok=True)

    print(f"  [ ] Backing up original -> {backup.name}")
    shutil.copy2(original, backup)

    print(f"  [ ] Extracting {inner}...")
    try:
        if linux:
            def _extract_tar():
                import tarfile
                with tarfile.open(archive, "r:bz2") as tf:
                    try:
                        member = tf.getmember(inner)
                    except KeyError:
                        raise FileNotFoundError(f"{inner!r} not found inside archive.")
                    member.name = file_name
                    tf.extract(member, path=output_dir, filter="data")
            run_in_thread(_extract_tar)
        else:
            # py7zr does not support BCJ2 compression used in the Goldberg
            # Windows archive, so we fall back to libarchive-c here.
            def _extract_libarchive():
                import libarchive
                found = False
                with libarchive.file_reader(str(archive)) as arc:
                    for entry in arc:
                        if entry.pathname == inner:
                            with open(target, "wb") as fh:
                                for block in entry.get_blocks():
                                    fh.write(block)
                            found = True
                            break
                if not found:
                    raise FileNotFoundError(f"{inner!r} not found inside archive.")
            run_in_thread(_extract_libarchive)
    except Exception as exc:
        print(f"  [!] Extraction error: {exc}")
        return False

    if target.exists():
        print(f"  [x] {target.name} placed in {output_dir}")
    return True


def _process_dll(dll_path: Path, settings_dir: Path, output_dir: Path,
                 experimental: bool = False) -> None:
    linux    = is_linux_so(dll_path)
    platform = "Linux" if linux else "Windows"
    variant  = "experimental" if experimental else "regular"
    print(f"\n  [ ] Processing {platform} binary: {dll_path}  [{variant} build]")

    bits = detect_binary_arch(dll_path)
    if linux:
        api_name = "libsteam_api"
        print(f"  [i] Detected architecture: {bits}  (libsteam_api.so)")
    else:
        api_name = "steam_api" if bits == "x32" else "steam_api64"
        print(f"  [i] Detected architecture: {bits}  ({api_name}.dll)")

    print("  [ ] Extracting steam interfaces...")
    interfaces = extract_steam_interfaces(dll_path)
    if interfaces:
        (settings_dir / "steam_interfaces.txt").write_text(
            "\n".join(interfaces) + "\n", encoding="utf-8"
        )
        print(f"  [x] {len(interfaces)} interface(s) written to steam_interfaces.txt")
    else:
        print("  [!] No interface strings found — is this the original Steam API binary?")

    print(f"\n  [ ] Fetching latest Goldberg emulator ({platform} build)...")
    release_name, asset_filename = get_latest_gbe(linux=linux)
    if not asset_filename:
        print(f"  [!] Could not find a {platform} release asset on GitHub.")
        return

    print(f"  [i] Latest release: {release_name}  ({asset_filename})")
    archive = download_gbe_archive(asset_filename, release_name)
    if not archive:
        return

    apply_goldberg(dll_path, archive, bits, api_name, output_dir,
                   experimental=experimental, linux=linux)


# ---------------------------------------------------------------------------
# SteamStub unpacking — delegates to vendored unstub/ tree
# ---------------------------------------------------------------------------

def unstub_exes(game_dest: Path, gse_install_dir: Path,
                options: dict | None = None) -> None:
    """Scan game_dest for .exe files and attempt to unstub each one.

    For every successfully unpacked executable, two files are written to the
    mirrored location under gse_install_dir:
        <relpath>.bak  — copy of the original protected binary
        <relpath>      — the unpacked binary (original name)
    """
    import logging
    from .unstub.unpackers import get_unpackers

    unstub_log = logging.getLogger("unstub")
    if not unstub_log.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter("    %(levelname)-7s | %(message)s"))
        unstub_log.addHandler(handler)
        unstub_log.setLevel(logging.INFO)

    exes = sorted(game_dest.rglob("*.exe"))
    if not exes:
        print("  [i] No .exe files found to unstub.")
        return

    print(f"\n  [ ] Scanning {len(exes)} .exe file(s) for SteamStub protection...")

    defaults = {
        "keepbind":       False,
        "zerodostub":     True,
        "dumppayload":    False,
        "dumpdrmp":       False,
        "realign":        False,
        "recalcchecksum": False,
    }
    options = {**defaults, **(options or {})}

    unpacked_count = 0
    for exe_path in exes:
        rel = exe_path.relative_to(game_dest)
        for unpacker_cls in get_unpackers():
            unpacker = unpacker_cls(str(exe_path), options)
            if not unpacker.can_process():
                continue
            print(f"\n  [ ] Unpacking: {rel}")
            if unpacker.process():
                unpacked_out = Path(str(exe_path) + ".unpacked.exe")
                dest_exe = gse_install_dir / rel
                dest_bak = dest_exe.parent / (dest_exe.name + ".bak")
                dest_exe.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(exe_path, dest_bak)
                shutil.move(str(unpacked_out), dest_exe)
                print(f"  [+] {rel}  ->  {rel}.bak (original) + {rel} (unpacked)")
                unpacked_count += 1
            else:
                print(f"  [!] Unpacking failed: {rel}")
                Path(str(exe_path) + ".unpacked.exe").unlink(missing_ok=True)
            break  # Only one unpacker should claim a given file

    if unpacked_count:
        print(f"\n  [+] {unpacked_count} executable(s) unstubbed.")
    else:
        print("  [i] No SteamStub-protected executables found.")


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def crack_game(app_id: int, app_data: dict, dest: Path, game_dest: Path,
               *, identity, experimental: bool = False,
               unstub_options: dict | None = None) -> Path | None:
    """Generate Goldberg Steam Emulator config + emulator binaries into a
    gse_config_{app_id}/ folder alongside depotcache/ and steamapps/.

    identity — CrackIdentity dataclass loaded from .xarchive.  Mutated in
    place when prompts fill in missing fields, so callers can persist
    updates back to the project file.

    Returns the gse_config directory, or None if no Steam API binary was found.
    """
    print("\n[ ] Setting up Goldberg Steam Emulator...")
    appid = str(app_id)

    found = find_steam_apis(game_dest)
    gse_output_dir = dest / f"gse_config_{appid}"

    if not found:
        print(f"  [!] No Steam API binary found under {game_dest} — skipping GSE setup.")
        unstub_exes(game_dest, gse_output_dir, unstub_options)
        return gse_output_dir if gse_output_dir.exists() else None

    dlls = [p for p in found if p.suffix == ".dll"]
    sos  = [p for p in found if p.name == "libsteam_api.so"]
    binaries_to_process = ([dlls[0]] if dlls else []) + ([sos[0]] if sos else [])

    skipped = [p for p in found if p not in binaries_to_process]
    for p in skipped:
        print(f"  [i] Skipping duplicate binary: {p}")

    if len(binaries_to_process) > 1:
        print("  [i] Found both Windows and Linux Steam API binaries — processing both.")

    dll_path = binaries_to_process[0]

    try:
        rel = dll_path.parent.relative_to(game_dest.parent)
        dll_output_dir = gse_output_dir / rel
    except ValueError:
        dll_output_dir = gse_output_dir

    settings_dir = dll_output_dir / "steam_settings"
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
        ach_lang = _prompt("Achievement language", user_cfg["language"])
        if ach_lang not in langs:
            ach_lang = user_cfg["language"]
        identity.achievement_language = ach_lang

    api_key = _resolve_api_key()
    achievements = fetch_achievements(appid, ach_lang, api_key=api_key)
    if achievements:
        print(f"  [ ] Downloading {len(achievements)} achievement(s)...")
        download_achievements(appid, achievements, settings_dir)
        if experimental:
            write_configs_overlay(settings_dir)
        else:
            print("  [i] Skipping configs.overlay.ini (regular build).")
        print("  [x] Achievements done.")
    else:
        print("  [x] No achievements found.")

    for binary in binaries_to_process:
        try:
            rel = binary.parent.relative_to(game_dest.parent)
            binary_output_dir = gse_output_dir / rel
        except ValueError:
            binary_output_dir = gse_output_dir
        _process_dll(binary, settings_dir, binary_output_dir, experimental=experimental)
        if binary_output_dir != dll_output_dir:
            alt_settings = binary_output_dir / "steam_settings"
            if alt_settings.exists():
                shutil.rmtree(alt_settings)
            shutil.copytree(settings_dir, alt_settings)
            print(f"  [x] steam_settings copied to {binary_output_dir}")

    unstub_exes(game_dest, dll_output_dir, unstub_options)

    print(f"\n[+] GSE setup complete — {gse_output_dir.name}/ at {gse_output_dir}")
    return gse_output_dir
