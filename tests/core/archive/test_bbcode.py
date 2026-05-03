"""archive.bbcode — template + placeholder rendering for forum posts."""
from __future__ import annotations


def test_bbcode_render_full_links():
    from src.core.archive import bbcode
    template = (
        "[size=150]{APP_NAME}[/size]\n"
        "Build: {BUILDID} (was {PREVIOUS_BUILDID})\n"
        "Released: {DATETIME}\n"
        "Platforms: {PLATFORMS}\n"
        "Links: {ALL_LINKS}\n"
        "Linux only: {LINUX_LINK}\n"
        "{MANIFESTS}\n"
    )
    data = bbcode.build_data(
        name="My Game",
        appid=12345,
        buildid="9999",
        previous_buildid="9998",
        timeupdated=1_700_000_000,
        upload_links={"windows": "https://w", "linux": "https://l"},
        manifests={"windows": [(100, "Game", "abc123")]},
    )
    out = bbcode.render(template, data)
    assert "My Game" in out
    assert "Build: 9999 (was 9998)" in out
    assert "Windows" in out and "Linux" in out
    assert "https://l" in out
    assert "Depots & Manifests - Windows" in out
    assert "100 - Game [Manifest abc123]" in out


def test_bbcode_render_drops_empty_optional_lines():
    """Lines containing an optional placeholder whose value is empty are
    dropped entirely.  A template can reference {LINUX_LINK} on its own
    line and the line silently disappears when no Linux build was
    uploaded — same for {MACOS_LINK}, {HEADER_IMAGE}, etc."""
    from src.core.archive import bbcode
    template = (
        "Name: {APP_NAME}\n"
        "Linux: {LINUX_LINK}\n"
        "Windows: {WINDOWS_LINK}\n"
        "[b]Header[/b]: {HEADER_IMAGE}\n"
    )
    data = bbcode.build_data(
        name="X", appid=1, buildid="1", previous_buildid="0",
        timeupdated=0,
        upload_links={"windows": "https://w"},  # no linux
    )
    out = bbcode.render(template, data)
    assert "Linux:" not in out          # dropped (no linux link)
    assert "Windows: https://w" in out
    assert "Header" not in out          # dropped (no header image)


def test_bbcode_wrapped_all_links_repeats_tags_per_link():
    """When {ALL_LINKS} is wrapped in inline tags, the renderer applies
    those tags to each individual link label rather than the whole list."""
    from src.core.archive import bbcode
    data = bbcode.build_data(
        name="X", appid=1, buildid="1", previous_buildid="0",
        timeupdated=0,
        upload_links={"linux": "https://l", "windows": "https://w"},
    )
    out = bbcode.render("Links: [b]{ALL_LINKS}[/b]\n", data)
    # Tags must wrap each label individually inside the [url=...] tag.
    assert "[url=https://l][b]Linux[/b][/url]"   in out
    assert "[url=https://w][b]Windows[/b][/url]" in out


def test_bbcode_safe_name_strips_disallowed_chars():
    from src.core.archive import bbcode
    assert bbcode.safe_name("My Game!  v2") == "My.Game.v2"
    assert bbcode.safe_name("a/b\\c.txt")    == "abc.txt"


def test_bbcode_default_template_loads():
    """The vendored default template is read from disk and contains the
    expected placeholders.  Catches missing data file at packaging time."""
    from src.core.archive import bbcode
    body = bbcode.load_default_template()
    assert "{APP_NAME}" in body and "{BUILDID}" in body


# ---------------------------------------------------------------------------
# bbcode_to_html — preview-mode forum render
# ---------------------------------------------------------------------------

def test_bbcode_to_html_inline_pairs_and_color_size():
    """The forum-preview converter handles the tag subset the in-app
    editor produces: b/i/u/s, color, size, url, list, spoiler."""
    from src.core.archive import bbcode
    src = (
        "[b]bold[/b] [i]i[/i] [u]u[/u] [s]s[/s] "
        "[color=#FF0000]r[/color] [size=150]big[/size] "
        "[url=https://x.example]ex[/url] "
        "[list][*]a[*]b[/list] "
        "[spoiler=\"t\"]secret[/spoiler]"
    )
    html = bbcode.bbcode_to_html(src)
    assert "<b>bold</b>"          in html
    assert "<i>i</i>"             in html
    assert "<u>u</u>"             in html
    assert "<s>s</s>"             in html
    assert "color:#FF0000"        in html
    assert "font-size:150%"       in html
    assert "<a href='https://x.example'>ex</a>" in html
    assert "<ul><li>a</li><li>b</li></ul>"      in html
    # Spoiler with title falls back to a labelled blockquote.
    assert "Spoiler:" in html and "t"      in html and "secret" in html


def test_bbcode_to_html_escapes_raw_lt_gt_amp():
    """Source text containing < > & must arrive as &lt; &gt; &amp; in
    HTML so the converter doesn't accidentally inject markup."""
    from src.core.archive import bbcode
    html = bbcode.bbcode_to_html("a<b>c & d>e")
    assert "&lt;" in html and "&gt;" in html and "&amp;" in html
    # And the literal "<b>" doesn't appear as live HTML.
    assert "<b>c</b>" not in html
