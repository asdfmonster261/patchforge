"""archive.upload — MultiUp.io uploader + PrivateBin paste consolidation.

External services are mocked at the requests-toolbelt /
DiscordWebhook / origamibot boundary so these tests stay offline.
"""
from __future__ import annotations

from unittest import mock


# ---------------------------------------------------------------------------
# Pure helpers — _archive_stem, _shorten_url
# ---------------------------------------------------------------------------

def test_upload_archive_stem_strips_volume_and_archive_extensions():
    from src.core.archive.upload import _archive_stem
    assert _archive_stem("Game.123.windows.public.7z.001") == "Game.123.windows.public"
    assert _archive_stem("Game.123.windows.public.7z")     == "Game.123.windows.public"
    assert _archive_stem("foo.tar.zst")                    == "foo"
    # Non-archive extensions must be left alone.
    assert _archive_stem("readme.txt") == "readme.txt"


def test_upload_shorten_url_collapses_download_path():
    from src.core.archive.upload import _shorten_url
    assert _shorten_url("https://multiup.io/download/abc/foo.7z") == "https://multiup.io/abc"
    # Already-short URLs are returned unchanged.
    assert _shorten_url("https://multiup.io/abc") == "https://multiup.io/abc"


# ---------------------------------------------------------------------------
# upload_archives — grouping + dispatch
# ---------------------------------------------------------------------------

def test_upload_archives_groups_by_stem(monkeypatch, tmp_path):
    """Multi-part archives sharing a stem must collapse into one MultiUp
    project and one PrivateBin paste."""
    from src.core.archive import upload as up

    # Stub the HTTP boundary.
    monkeypatch.setattr(up, "_login",              lambda u, p: "uid42")
    monkeypatch.setattr(up, "_get_hosts",          lambda u, p: ["uploaded.net"])
    monkeypatch.setattr(up, "_get_fastest_server", lambda: "https://srv/upload")
    created_projects: list[str] = []
    def _create(name, description, user_id):
        created_projects.append(name)
        return f"hash-{name}"
    monkeypatch.setattr(up, "_create_project", _create)

    sent = []
    def _send(server, file_path, hosts, on_event, **kw):
        sent.append((file_path.name, kw["project_hash"]))
        # Use the filename inside the path so _shorten_url's collapse
        # leaves us with distinguishable URLs per file.
        return f"https://multiup.io/download/{file_path.name}/x"
    monkeypatch.setattr(up, "_upload_file", _send)

    pasted = []
    def _paste(bin_url, urls, password=None):
        pasted.append((bin_url, list(urls)))
        return "https://pb/aaa"
    monkeypatch.setattr(up, "_create_paste", _paste)

    parts = [
        tmp_path / "Game.1.windows.public.7z.001",
        tmp_path / "Game.1.windows.public.7z.002",
        tmp_path / "Game.1.linux.public.7z",
    ]
    for p in parts:
        p.write_bytes(b"x")

    events = []
    result = up.upload_archives(
        parts, username="alice", password="pw",
        links_dir=tmp_path / "links",
        bin_url="https://pb",
        on_event=events.append,
    )

    # Two stems → two MultiUp projects → two PrivateBin pastes.
    assert sorted(created_projects) == ["Game.1.linux.public", "Game.1.windows.public"]
    assert len(pasted) == 2
    # Windows project bundled both .001 and .002 — three uploads total
    # but only two project hashes, the windows hash repeated twice.
    project_hashes = [ph for _, ph in sent]
    assert project_hashes.count("hash-Game.1.windows.public") == 2
    assert project_hashes.count("hash-Game.1.linux.public")   == 1
    win_paste = next(urls for url, urls in pasted
                     if any("windows" in u for u in urls))
    assert len(win_paste) == 2
    # Returned canonical URLs come from the paste responses.
    assert result["Game.1.windows.public"] == "https://pb/aaa"
    assert result["Game.1.linux.public"]   == "https://pb/aaa"
    # Stage events emitted for login + host-list + per-stem upload heading.
    kinds = [e.kind for e in events]
    assert "stage" in kinds
    assert kinds.count("paste_created") == 2


def test_upload_archives_uses_gevent_pool_when_max_concurrent_gt_1(monkeypatch, tmp_path):
    """max_concurrent > 1 must dispatch via gevent.pool.Pool, not
    concurrent.futures.ThreadPoolExecutor — patch_minimal() leaves
    threading unpatched, so a thread-pool-based upload would block the
    main hub and starve the live-display redraw greenlet."""
    from src.core.archive import upload as up

    monkeypatch.setattr(up, "_login",              lambda u, p: "uid")
    monkeypatch.setattr(up, "_get_hosts",          lambda u, p: ["h"])
    monkeypatch.setattr(up, "_get_fastest_server", lambda: "https://srv")
    monkeypatch.setattr(up, "_create_project",     lambda *a, **kw: "ph")
    monkeypatch.setattr(up, "_upload_file",
                        lambda *a, **kw: "https://multiup.io/download/x/y")

    # Sentinel: ThreadPoolExecutor must NOT be touched on the parallel
    # path.  If a future change reintroduces it the test fails loudly.
    import concurrent.futures as cf
    boom = mock.Mock(side_effect=AssertionError(
        "ThreadPoolExecutor must not be used for upload concurrency"
    ))
    monkeypatch.setattr(cf, "ThreadPoolExecutor", boom)

    pool_calls: list[int] = []
    real_pool_cls = __import__("gevent.pool", fromlist=["Pool"]).Pool
    class _SpyPool(real_pool_cls):
        def __init__(self, size):
            pool_calls.append(size)
            super().__init__(size)
    monkeypatch.setattr("gevent.pool.Pool", _SpyPool)

    files = [tmp_path / f"Game.7z.{i:03d}" for i in (1, 2, 3)]
    for f in files:
        f.write_bytes(b"x")

    result = up.upload_archives(
        files, username="alice", password="pw", max_concurrent=3,
        links_dir=None, bin_url=None,
        on_event=None,
    )
    assert pool_calls == [3]
    assert "Game" in result


def test_upload_archives_inline_path_when_max_concurrent_le_1(monkeypatch, tmp_path):
    """max_concurrent <= 1 calls _upload_one directly on the main
    greenlet so socket writes inside requests.post yield to the live
    display's redraw loop chunk-by-chunk."""
    from src.core.archive import upload as up

    monkeypatch.setattr(up, "_login",              lambda u, p: "uid")
    monkeypatch.setattr(up, "_get_hosts",          lambda u, p: ["h"])
    monkeypatch.setattr(up, "_get_fastest_server", lambda: "https://srv")
    monkeypatch.setattr(up, "_create_project",     lambda *a, **kw: "ph")
    monkeypatch.setattr(up, "_upload_file",
                        lambda *a, **kw: "https://multiup.io/download/x/y")

    boom_pool = mock.Mock(side_effect=AssertionError(
        "gevent.pool.Pool must not be used when max_concurrent <= 1"
    ))
    monkeypatch.setattr("gevent.pool.Pool", boom_pool)

    f = tmp_path / "Game.7z"
    f.write_bytes(b"x")
    result = up.upload_archives(
        [f], username="alice", password="pw", max_concurrent=1,
        links_dir=None, bin_url=None, on_event=None,
    )
    assert "Game" in result


def test_upload_emits_started_progress_finished_per_archive(monkeypatch, tmp_path):
    """_upload_file must bracket every archive in started/finished
    events and forward MultipartEncoderMonitor progress through
    upload_progress."""
    from src.core.archive import upload as up

    monkeypatch.setattr(up, "_login",              lambda u, p: "uid")
    monkeypatch.setattr(up, "_get_hosts",          lambda u, p: ["h"])
    monkeypatch.setattr(up, "_get_fastest_server", lambda: "https://srv")
    monkeypatch.setattr(up, "_create_project",     lambda *a, **k: "ph")
    monkeypatch.setattr(up, "_create_paste",       lambda *a, **k: "https://pb/x")

    # Stub _upload_file to drive the events directly so we don't need
    # the requests-toolbelt monitor stack.
    def _stub(server, file_path, hosts, on_event, **kw):
        from src.core.archive.download import DownloadEvent
        on_event(DownloadEvent(kind="upload_started",  name=file_path.name, total=100))
        on_event(DownloadEvent(kind="upload_progress", name=file_path.name, total=100, done=42))
        on_event(DownloadEvent(kind="upload_finished", name=file_path.name, total=100, done=100))
        return f"https://multiup.io/download/h/{file_path.name}"
    monkeypatch.setattr(up, "_upload_file", _stub)

    f = tmp_path / "Game.1.windows.public.7z"
    f.write_bytes(b"x")
    events = []
    up.upload_archives([f], username="u", password="p",
                       links_dir=None, bin_url=None,
                       on_event=events.append)
    kinds = [e.kind for e in events]
    assert "upload_started"  in kinds
    assert "upload_progress" in kinds
    assert "upload_finished" in kinds
    assert kinds.index("upload_started") < kinds.index("upload_finished")
