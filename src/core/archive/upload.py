"""MultiUp.io archive uploader + PrivateBin paste consolidation.

Routes upload progress through the same DownloadEvent stream the rest of
archive-mode uses, so the live CLI display (and Phase 6's Qt subscriber)
gets per-archive bytes without any tqdm-flavoured noise.

Multi-part archives that share a stem (e.g. game.7z.001 / .002 / ...) are
grouped into a single MultiUp project so the user sees one short URL per
build rather than one per part.

Lazy imports requests / requests-toolbelt / privatebinapi so importing
this module without the `archive` extra installed doesn't raise.
"""

from __future__ import annotations

import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Callable

from .download import DownloadEvent  # event protocol shared with download.py


MULTIUP_API = "https://multiup.io/api"

_ARCHIVE_EXTS = {
    ".7z", ".zip", ".rar", ".gz", ".bz2", ".tar", ".xz", ".zst",
}

# PrivateBin enforces a per-IP cooldown between paste submissions.  Keep
# the same 10-second floor SteamArchiver used.
_PB_RATE_LIMIT = 10.0


EventCallback = Callable[[DownloadEvent], None]


def _emit(on_event: EventCallback | None, **kw) -> None:
    if on_event is not None:
        on_event(DownloadEvent(**kw))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _shorten_url(url: str) -> str:
    """Trim a MultiUp `download/<hash>/<filename>` URL to its short form."""
    parts = url.split("/")
    if len(parts) >= 5 and parts[3] == "download":
        return f"https://multiup.io/{parts[4]}"
    return url


def _archive_stem(path: Path | str) -> str:
    """Strip archive + numeric-part extensions, e.g. game.7z.001 -> game."""
    p = Path(path)
    while p.suffix and (p.suffix in _ARCHIVE_EXTS or p.suffix.lstrip(".").isdigit()):
        p = p.with_suffix("")
    return p.name


# ---------------------------------------------------------------------------
# MultiUp HTTP wrappers
# ---------------------------------------------------------------------------

def _login(username: str, password: str) -> str:
    import requests
    resp = requests.post(
        f"{MULTIUP_API}/login",
        data={"username": username, "password": password},
    )
    resp.raise_for_status()
    data = resp.json()
    if data.get("error") != "success":
        raise RuntimeError(f"MultiUp login failed: {data.get('error')}")
    return str(data["user"])


def _get_hosts(username: str | None, password: str | None) -> list[str]:
    import requests
    params: dict[str, str] = {}
    if username and password:
        params = {"username": username, "password": password}
    resp = requests.post(f"{MULTIUP_API}/get-list-hosts", data=params)
    resp.raise_for_status()
    data = resp.json()
    selected = [
        name for name, info in data.get("hosts", {}).items()
        if str(info.get("selected", "false")).lower() == "true"
    ]
    if not selected:
        raise RuntimeError(
            "No MultiUp hosts selected — log in to multiup.io and pick at least one."
        )
    return selected


def _get_fastest_server() -> str:
    import requests
    resp = requests.get(f"{MULTIUP_API}/get-fastest-server")
    resp.raise_for_status()
    data = resp.json()
    if data.get("error") != "success":
        raise RuntimeError(f"MultiUp upload server query failed: {data.get('error')}")
    return data["server"]


def _create_project(name: str, description: str | None, user_id: str | None) -> str:
    import requests
    fields: dict[str, str] = {"name": name}
    if description:
        fields["description"] = description
    if user_id:
        fields["user-id"] = user_id
    resp = requests.post(f"{MULTIUP_API}/add-project", data=fields)
    resp.raise_for_status()
    result = resp.json()
    if result.get("error") != "success":
        raise RuntimeError(
            f"MultiUp project create failed for {name!r}: {result.get('error')}"
        )
    return result["hash"]


def _upload_file(server: str, file_path: Path, hosts: list[str],
                 on_event: EventCallback | None,
                 user_id: str | None = None,
                 description: str | None = None,
                 project_hash: str | None = None) -> str:
    import requests
    from requests_toolbelt import MultipartEncoder, MultipartEncoderMonitor

    fields: dict = {host: "true" for host in hosts}
    if user_id:
        fields["user"] = user_id
    if description:
        fields["description"] = description
    if project_hash:
        fields["project-hash"] = project_hash

    label = file_path.name

    with file_path.open("rb") as fh:
        fields["files[]"] = (label, fh, "application/octet-stream")
        encoder = MultipartEncoder(fields=fields)
        total = encoder.len

        _emit(on_event, kind="upload_started", name=label, total=total)

        def _progress(monitor):
            _emit(on_event,
                  kind="upload_progress",
                  name=label,
                  total=total,
                  done=monitor.bytes_read)

        monitor = MultipartEncoderMonitor(encoder, _progress)
        try:
            resp = requests.post(
                server,
                data=monitor,
                headers={"Content-Type": monitor.content_type},
            )
        finally:
            _emit(on_event,
                  kind="upload_finished",
                  name=label,
                  total=total,
                  done=monitor.bytes_read)

    resp.raise_for_status()
    result = resp.json()
    # MultiUp's response shape is one of: {"files": [...]}, [...], {"url": ...}
    if "files" in result:
        files = result["files"]
        if not files:
            raise RuntimeError("MultiUp upload response had empty files list")
        result = files[0]
    elif isinstance(result, list):
        if not result:
            raise RuntimeError("MultiUp upload response was an empty list")
        result = result[0]
    if "url" not in result:
        raise RuntimeError("MultiUp upload response missing URL field")
    return result["url"]


# ---------------------------------------------------------------------------
# PrivateBin
# ---------------------------------------------------------------------------

def _create_paste(bin_url: str, urls: list[str], password: str | None = None) -> str:
    import privatebinapi
    kwargs: dict = {"expiration": "never"}
    if password:
        kwargs["password"] = password
    resp = privatebinapi.send(bin_url, text="\n".join(urls), **kwargs)
    if "full_url" not in resp:
        raise RuntimeError(f"PrivateBin response missing full_url: {resp}")
    return resp["full_url"]


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def upload_archives(archive_paths: list[Path],
                    *,
                    username: str | None = None,
                    password: str | None = None,
                    description: str | None = None,
                    max_concurrent: int = 1,
                    links_dir: Path | None = None,
                    bin_url:  str | None = None,
                    bin_pass: str | None = None,
                    delete_archives: bool = False,
                    on_event: EventCallback | None = None,
                    ) -> dict[str, str]:
    """Upload archive files to MultiUp, optionally consolidating per-stem
    download URLs into a PrivateBin paste.

    Multi-part archives (game.7z.001/002/...) are grouped by stem so each
    project gets a single MultiUp project hash and (if `bin_url` is set) a
    single PrivateBin paste.

    Returns:
        Mapping of archive stem -> the canonical URL for that build.  If a
        PrivateBin paste was created the paste URL is used; otherwise the
        first MultiUp short URL.  Stems that fail every upload are absent.
    """
    user_id: str | None = None
    if username:
        _emit(on_event, kind="stage", stage_msg=f"MultiUp login as {username}")
        user_id = _login(username, password or "")

    _emit(on_event, kind="stage", stage_msg="Fetching MultiUp host list")
    hosts = _get_hosts(username, password)

    _emit(on_event, kind="stage",
          stage_msg=f"Fetching fastest MultiUp server ({len(hosts)} hosts)")
    server = _get_fastest_server()

    # Group by archive stem so multi-part archives share a MultiUp project.
    groups: dict[str, list[Path]] = defaultdict(list)
    for p in archive_paths:
        groups[_archive_stem(p)].append(Path(p))

    stem_to_url: dict[str, str] = {}
    last_paste_at: float = 0.0

    for project_name, files in groups.items():
        _emit(on_event, kind="stage", stage_msg=f"Uploading: {project_name}")
        try:
            project_hash = _create_project(
                project_name, description=description, user_id=user_id,
            )
        except RuntimeError as e:
            _emit(on_event, kind="error", name=project_name,
                  error_msg=f"create-project failed: {e}; skipping {len(files)} file(s)")
            continue

        queue = [(fp, project_hash) for fp in files]

        def _upload_one(args):
            file_path, ph = args
            try:
                url = _upload_file(
                    server, file_path, hosts, on_event,
                    user_id=user_id, description=description, project_hash=ph,
                )
                return file_path, url, None
            except (RuntimeError, OSError) as e:
                # _upload_finished fired in _upload_file's finally — no extra
                # close needed.  Just record the failure for the caller loop.
                return file_path, None, str(e)

        # max_concurrent is the per-project parallelism.  The default of 1
        # keeps the live UI legible; bump it for fast outbound links.
        with ThreadPoolExecutor(max_workers=max_concurrent) as ex:
            results = list(ex.map(_upload_one, queue))

        urls: list[str] = []
        uploaded: list[Path] = []
        for file_path, url, error in results:
            if url:
                urls.append(_shorten_url(url))
                uploaded.append(file_path)
            else:
                _emit(on_event, kind="error", name=file_path.name,
                      error_msg=f"upload failed: {error}")

        if not urls:
            continue

        canonical_url = urls[0]
        saved_to_links = False

        if links_dir:
            links_dir.mkdir(parents=True, exist_ok=True)
            links_path = links_dir / f"{project_name}.txt"
            if bin_url:
                # Honour PrivateBin's per-IP rate limit between consecutive
                # pastes (the server returns 429 if we go too fast).
                elapsed = time.time() - last_paste_at
                if elapsed < _PB_RATE_LIMIT:
                    time.sleep(_PB_RATE_LIMIT - elapsed)
                try:
                    paste_url = _create_paste(bin_url, urls, password=bin_pass)
                    last_paste_at = time.time()
                    canonical_url = paste_url
                    with links_path.open("a") as fh:
                        fh.write(paste_url + "\n")
                    _emit(on_event, kind="paste_created",
                          name=project_name, stage_msg=paste_url)
                    saved_to_links = True
                except Exception as e:
                    _emit(on_event, kind="error", name=project_name,
                          error_msg=f"PrivateBin paste failed: {e}")
            else:
                with links_path.open("a") as fh:
                    for u in urls:
                        fh.write(u + "\n")
                saved_to_links = True

        if delete_archives and (saved_to_links or not links_dir):
            for archive in uploaded:
                try:
                    archive.unlink()
                except OSError as e:
                    _emit(on_event, kind="error", name=archive.name,
                          error_msg=f"delete failed: {e}")

        stem_to_url[project_name] = canonical_url

    return stem_to_url
