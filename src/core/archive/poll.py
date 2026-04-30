"""Phase 5 — poll-on-change buildid detection.

Wraps `query_app_info_batch(quiet=True)` to compare each tracked app's
current Steam buildid against the AppEntry.current_buildid the project
last persisted.  Returns only the apps whose buildid moved (or all of
them when `force_download` is set).

No sleeping, no printing, no IO beyond the steam[client] product-info
RPC — the CLI orchestrator owns the loop, the prints, and the sleeping
between iterations.  Keeps this module trivial to test against a stub
client/cdn pair and side-effect-free for non-CLI callers (the GUI's
poll loop in Phase 6 will call this directly).
"""

from __future__ import annotations

from .appinfo import query_app_info_batch


def detect_changes(client,
                   cdn,
                   apps_by_id: dict,        # int -> AppEntry
                   *,
                   force_download: bool = False,
                   batch_size: int | None = None,
                   max_retries: int = 1) -> list[tuple[int, str, str, dict]]:
    """Query product-info for every app in `apps_by_id` and return the
    subset whose Steam buildid differs from the AppEntry's persisted
    `current_buildid`.

    When `force_download` is True every app with a usable buildid is
    returned regardless of comparison — used to seed a polling run with
    an unconditional first pass.

    Apps with no info, no public buildid, or buildid == "Unknown" are
    silently skipped so a missing app doesn't poison the iteration; the
    next cycle will retry.

    Each tuple is (app_id, previous_buildid, current_buildid, info_dict)
    where info_dict is the same shape as `_extract_app_info` returns
    (name, buildid, oslist, timeupdated, installdir) so callers can
    feed it to notify / bbcode without re-fetching.
    """
    if not apps_by_id:
        return []

    out: list[tuple[int, str, str, dict]] = []
    for app_id, info in query_app_info_batch(
            client, cdn, list(apps_by_id),
            max_retries=max_retries,
            batch_size=batch_size,
            quiet=True):
        if not info:
            continue
        current = str(info.get("buildid") or "")
        if not current or current == "Unknown":
            continue
        entry = apps_by_id.get(app_id)
        previous = str(getattr(entry, "current_buildid", "") or "") if entry else ""
        if force_download or current != previous:
            out.append((app_id, previous, current, info))
    return out
