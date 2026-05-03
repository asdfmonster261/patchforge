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


def _extract_app_info(app_data: dict) -> dict | None:
    """Pull the structured fields PatchForge cares about out of a raw
    Steam product-info `app_data` dict.  Returns None when `app_data`
    is empty/missing.  Pure — does not print or touch any global state,
    safe for the polling-loop driver to call repeatedly.
    """
    if not app_data:
        return None
    branch = app_data.get("depots", {}).get("branches", {}).get("public", {})
    return {
        "name":        app_data.get("common", {}).get("name", "Unknown"),
        "buildid":     branch.get("buildid", "Unknown"),
        "oslist":      app_data.get("common", {}).get("oslist", ""),
        "timeupdated": branch.get("timeupdated", ""),
        "installdir":  app_data.get("config", {}).get("installdir", "Unknown"),
    }


def _format_app_info(app_id: int, app_data: dict, licensed_apps) -> dict | None:
    if not app_data:
        print(f"error: no product info returned for app {app_id}.")
        return None

    info        = _extract_app_info(app_data)
    name        = info["name"]
    installdir  = info["installdir"]
    build_id    = info["buildid"]
    depots      = app_data.get("depots", {})

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

    return info


def _streaming_product_info(client, app_ids: list[int],
                            max_retries: int,
                            timeout: int = 15) -> Iterator[tuple[int, dict | None]]:
    """Send a single ClientPICSProductInfoRequest for every app in
    `app_ids` and yield (app_id, app_data) pairs as each PICS response
    chunk arrives, instead of buffering the full result like
    SteamClient.get_product_info() does.

    Mirrors the chunk loop in steam/client/builtins/apps.py:get_product_info
    so the rest of the response shape (`_missing_token`, `_change_number`,
    `_sha`, `_size`) stays compatible with `_extract_app_info`.

    Apps Steam never returns in any chunk are emitted with `app_data=None`
    after the final chunk — caller treats those as failed lookups.
    """
    GeventTimeout, *_ = _import_steam()
    from steam.core.msg     import MsgProto
    from steam.enums.emsg   import EMsg
    import vdf
    from binascii import hexlify

    if not app_ids:
        return

    seen: set[int] = set()

    def _attempt():
        # Resolve access tokens up-front the same way get_product_info does.
        tokens = client.get_access_tokens(app_ids=list(app_ids))

        message = MsgProto(EMsg.ClientPICSProductInfoRequest)
        for app_id in app_ids:
            entry = message.body.apps.add()
            entry.appid = app_id
            if tokens:
                entry.access_token = tokens["apps"].get(app_id, 0)
        message.body.meta_data_only  = False
        message.body.num_prev_failed = 0

        job_id = client.send_job(message)
        while True:
            chunk = client.wait_event(job_id, timeout=timeout, raises=True)
            chunk = chunk[0].body
            for app in chunk.apps:
                if app.buffer:
                    info = vdf.loads(
                        app.buffer[:-1].decode("utf-8", "replace")
                    )["appinfo"]
                else:
                    info = {}
                info["_missing_token"] = app.missing_token
                info["_change_number"] = app.change_number
                info["_sha"]           = hexlify(app.sha).decode("ascii")
                info["_size"]          = app.size
                seen.add(app.appid)
                yield app.appid, info
            if not chunk.response_pending:
                break

    for attempt in range(max_retries + 1):
        try:
            for app_id, info in _attempt():
                yield app_id, info
            break
        except GeventTimeout:
            if attempt < max_retries:
                print(f"warning: timed out fetching product info, "
                      f"retrying ({attempt + 1}/{max_retries})...")
            else:
                print(f"error: timed out fetching product info after "
                      f"{max_retries} retries.")
                raise SessionDead(
                    f"Timed out fetching product info after {max_retries} retries."
                )

    # Apps Steam never returned — emit a None so the caller can record
    # a failed lookup and the progress counter still hits N/N.
    for app_id in app_ids:
        if app_id not in seen:
            yield app_id, None


def query_app_info_batch(client, cdn, app_ids: list[int],
                         max_retries: int = 1,
                         batch_size: int | None = None,
                         quiet: bool = False) -> Iterator[tuple[int, dict | None]]:
    """Yield (app_id, info_dict | None) for each app, streamed as the
    PICS responses arrive (one per app, not one per batch).

    `batch_size` is preserved for API compatibility but no longer
    affects progress granularity — Steam already chunks the response
    so a single request stream yields per-app updates as the data
    comes in.

    When `quiet` is True the per-app human-readable summary is skipped
    and only the structured info dict is returned — used by the Phase 5
    polling driver, which would otherwise spam the terminal each cycle.
    """
    licensed_apps = cdn.licensed_app_ids if not quiet else None
    batches = list(_chunks(app_ids, batch_size)) if batch_size else [app_ids]

    for batch in batches:
        for app_id, app_data in _streaming_product_info(
                client, batch, max_retries=max_retries):
            if quiet:
                yield app_id, _extract_app_info(app_data)
            else:
                yield app_id, _format_app_info(app_id, app_data, licensed_apps)


def _chunks(lst, n):
    for i in range(0, len(lst), n):
        yield lst[i:i + n]
