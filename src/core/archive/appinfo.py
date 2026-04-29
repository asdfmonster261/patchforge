"""Query Steam product info for one or more app IDs.

Thin wrapper around steam[client]'s SteamClient.get_product_info that retries
on gevent timeouts and yields a structured info dict per app.

Output is print-formatted with plain stdout (no ANSI color in this rewrite —
PatchForge CLI is plain throughout); GUI-side code can format the same data
itself off the structured info dict.
"""

from __future__ import annotations

from typing import Iterator

from .errors import SessionDead


def _import_steam():
    from ._extras import require_extras
    require_extras()
    from gevent.timeout import Timeout as GeventTimeout
    from steam.client     import SteamClient
    from steam.client.cdn import CDNClient
    from steam.enums      import EResult
    return GeventTimeout, SteamClient, CDNClient, EResult


def _retry(label: str, fn, max_retries: int):
    GeventTimeout, *_ = _import_steam()
    for attempt in range(max_retries + 1):
        try:
            return fn()
        except GeventTimeout:
            if attempt < max_retries:
                print(f"warning: timed out {label}, retrying ({attempt + 1}/{max_retries})...")
            else:
                print(f"error: timed out {label} after {max_retries} retries.")
                raise SessionDead(f"Timed out {label} after {max_retries} retries.")


def login(tokens: dict):
    """Connect SteamClient + CDNClient using saved refresh-token credentials.

    Returns (client, cdn).  Raises ArchiveError if login fails.
    """
    _, SteamClient, CDNClient, EResult = _import_steam()
    client = SteamClient()
    result = client.login(tokens["username"], access_token=tokens["client_refresh_token"])
    if result != EResult.OK:
        raise SessionDead(f"CM login failed: {result!r}")
    cdn = CDNClient(client)
    return client, cdn


def _format_app_info(app_id: int, app_data: dict, licensed_apps) -> dict | None:
    if not app_data:
        print(f"error: no product info returned for app {app_id}.")
        return None

    name        = app_data.get("common", {}).get("name", "Unknown")
    installdir  = app_data.get("config", {}).get("installdir", "Unknown")
    depots      = app_data.get("depots", {})
    branch      = depots.get("branches", {}).get("public", {})
    build_id    = branch.get("buildid", "Unknown")
    timeupdated = branch.get("timeupdated", "")
    oslist      = app_data.get("common", {}).get("oslist", "")

    print()
    print(f"  {name}  ·  App {app_id}")
    print(f"  InstallDir: {installdir}")
    print(f"  Build ID:   {build_id}")

    required: dict[str, list[tuple[int, str]]] = {}
    language: list[tuple[int, str, str]]       = []
    dlc:      list[tuple[int, str, int]]       = []
    shared:   list[tuple[int, str, str]]       = []

    for key, depot_info in depots.items():
        if not key.isdigit():
            continue
        depot_id   = int(key)
        depot_name = depot_info.get("name", f"Depot {depot_id}")
        config     = depot_info.get("config", {})

        if "depotfromapp" in depot_info:
            shared.append((depot_id, depot_name, depot_info["depotfromapp"]))
            continue
        if "dlcappid" in depot_info:
            dlc.append((depot_id, depot_name, int(depot_info["dlcappid"])))
            continue
        if "language" in config:
            language.append((depot_id, depot_name, config["language"]))
            continue

        depot_oslist = config.get("oslist", "")
        if depot_oslist:
            for platform in depot_oslist.split(","):
                required.setdefault(platform.strip(), []).append((depot_id, depot_name))
        else:
            required.setdefault("(all platforms)", []).append((depot_id, depot_name))

    print()
    print("  Required depots:")
    if required:
        for platform, entries in sorted(required.items()):
            print(f"    {platform}")
            for depot_id, depot_name in entries:
                print(f"      {depot_id:>10}  {depot_name}")
    else:
        print("    (none)")

    print()
    print(f"  Language depots ({len(language)})")
    for depot_id, depot_name, lang in sorted(language, key=lambda x: x[2]):
        print(f"    {depot_id:>10}  {depot_name}  [{lang}]")

    if dlc:
        print()
        print(f"  DLC depots ({len(dlc)})")
        for depot_id, depot_name, dlc_app in dlc:
            owned = "owned" if dlc_app in licensed_apps else "not owned"
            print(f"    {depot_id:>10}  {depot_name}  [DLC app {dlc_app}  {owned}]")

    if shared:
        print()
        print(f"  Shared depots ({len(shared)})")
        for depot_id, depot_name, source_app in shared:
            print(f"    {depot_id:>10}  {depot_name}  [from app {source_app}]")

    listofdlc = app_data.get("extended", {}).get("listofdlc", "")
    if listofdlc:
        dlc_app_ids = [int(x.strip()) for x in str(listofdlc).split(",") if x.strip().isdigit()]
        print()
        print(f"  DLC apps ({len(dlc_app_ids)})")
        for dlc_app_id in dlc_app_ids:
            owned = "owned" if dlc_app_id in licensed_apps else "not owned"
            print(f"    {dlc_app_id}  {owned}")

    return {
        "name":        name,
        "buildid":     build_id,
        "oslist":      oslist,
        "timeupdated": timeupdated,
    }


def query_app_info_batch(client, cdn, app_ids: list[int],
                         max_retries: int = 1,
                         batch_size: int | None = None) -> Iterator[tuple[int, dict | None]]:
    """Yield (app_id, info_dict | None) for each app, fetched in batches."""
    licensed_apps = cdn.licensed_app_ids
    batches = list(_chunks(app_ids, batch_size)) if batch_size else [app_ids]

    for batch in batches:
        info = _retry(
            "fetching product info",
            lambda b=batch: client.get_product_info(apps=b),
            max_retries,
        )
        for app_id in batch:
            app_data = info["apps"].get(app_id)
            yield app_id, _format_app_info(app_id, app_data, licensed_apps)


def _chunks(lst, n):
    for i in range(0, len(lst), n):
        yield lst[i:i + n]
