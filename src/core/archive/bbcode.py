"""BBCode template renderer for archive-mode forum posts.

A template is a plain-text file with `{PLACEHOLDER}` tokens.  Lines that
contain an *optional* placeholder whose value is empty are dropped
entirely, so the same template can render with or without per-platform
upload links, manifests, header image, etc.

Two placeholders support inline-tag wrapping: when `{ALL_LINKS}` or
`{PLATFORMS}` appears between an opening and closing tag pair on the
same line, the renderer applies that tag pair to each individual link
or platform label, e.g.:

    [b]{ALL_LINKS}[/b]
       -> [url=...][b]Windows[/b][/url], [url=...][b]Linux[/b][/url]

The default template (used when a project doesn't provide its own
`bbcode_template` body) lives in src/core/archive/data/template.txt.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path


_OPTIONAL = frozenset({
    "{WINDOWS_LINK}",
    "{LINUX_LINK}",
    "{MACOS_LINK}",
    "{ALL_LINKS}",
    "{PLATFORMS}",
    "{MANIFESTS}",
    "{HEADER_IMAGE}",
})

_PLATFORM_ORDER = [
    ("windows", "Windows"),
    ("linux",   "Linux"),
    ("macos",   "macOS"),
]

_WRAPPED_ALL_LINKS = re.compile(
    r"((?:\[[^\]/\[]+\])+)\{ALL_LINKS\}((?:\[/[^\]/\[]+\])+)"
)
_WRAPPED_PLATFORMS = re.compile(
    r"((?:\[[^\]/\[]+\])+)\{PLATFORMS\}((?:\[/[^\]/\[]+\])+)"
)


def default_template_path() -> Path:
    """Bundled default template — `src/core/archive/data/template.txt`."""
    return Path(__file__).parent / "data" / "template.txt"


def load_default_template() -> str:
    return default_template_path().read_text(encoding="utf-8")


def _render_manifests(manifests: dict) -> str:
    parts: list[str] = []
    for plat, label in _PLATFORM_ORDER:
        entries = manifests.get(plat) or []
        if not entries:
            continue
        lines = [f'[spoiler="Depots & Manifests - {label}"]']
        for depot_id, depot_name, gid in entries:
            if depot_name:
                lines.append(f"{depot_id} - {depot_name} [Manifest {gid}]")
            else:
                lines.append(f"[{depot_id}] - [{gid}]")
        lines.append("[/spoiler]")
        parts.append("\n".join(lines))
    return "\n".join(parts)


def build_data(name: str,
               appid: str | int,
               buildid: str,
               previous_buildid: str,
               timeupdated: str | int,
               upload_links: dict | None = None,
               manifests: dict | None = None,
               header_image: str | None = None) -> dict:
    """Return the placeholder-to-value map used by render()."""
    try:
        dt = datetime.fromtimestamp(int(timeupdated), tz=timezone.utc)
    except (TypeError, ValueError):
        dt = datetime.now(tz=timezone.utc)

    links = upload_links or {}
    raw_links = [
        (links[plat], label)
        for plat, label in _PLATFORM_ORDER
        if links.get(plat)
    ]
    link_items = [f"[url={url}]{label}[/url]" for url, label in raw_links]

    return {
        "{APP_NAME}":         str(name),
        "{APPID}":            str(appid),
        "{BUILDID}":          str(buildid),
        "{PREVIOUS_BUILDID}": str(previous_buildid),
        "{DATE}":             dt.strftime("%Y-%m-%d"),
        "{TIME}":             dt.strftime("%H:%M:%S UTC"),
        "{DATETIME}":         dt.strftime("%Y-%m-%d %H:%M:%S UTC"),
        "{STEAMDB_URL}":      f"https://steamdb.info/patchnotes/{buildid}/",
        "{WINDOWS_LINK}":     links.get("windows", ""),
        "{LINUX_LINK}":       links.get("linux", ""),
        "{MACOS_LINK}":       links.get("macos", ""),
        "{ALL_LINKS}":        ", ".join(link_items),
        "{PLATFORMS}":        ", ".join(label for _, label in raw_links),
        "{MANIFESTS}":        _render_manifests(manifests or {}),
        "{HEADER_IMAGE}":     header_image or "",
        # Internal helpers consumed by render()'s wrapped-tag fallbacks.
        "_raw_links":         raw_links,
        "_platform_labels":   [label for _, label in raw_links],
    }


def render(template: str, data: dict) -> str:
    """Fill placeholders in `template` using `data` (from build_data()).

    Lines containing an optional placeholder whose value is empty are
    dropped entirely, so a template line like '[b]Linux:[/b] {LINUX_LINK}'
    disappears when no Linux build was uploaded.
    """
    out: list[str] = []
    for line in template.splitlines(keepends=True):
        if any(ph in line and not data.get(ph) for ph in _OPTIONAL):
            continue

        if "{ALL_LINKS}" in line and _WRAPPED_ALL_LINKS.search(line):
            raw = data.get("_raw_links", [])
            def _all(m, _raw=raw):
                pre, post = m.group(1), m.group(2)
                return ", ".join(
                    f"[url={url}]{pre}{label}{post}[/url]" for url, label in _raw
                )
            line = _WRAPPED_ALL_LINKS.sub(_all, line)

        if "{PLATFORMS}" in line and _WRAPPED_PLATFORMS.search(line):
            labels = data.get("_platform_labels", [])
            def _plats(m, _labels=labels):
                pre, post = m.group(1), m.group(2)
                return ", ".join(f"{pre}{lbl}{post}" for lbl in _labels)
            line = _WRAPPED_PLATFORMS.sub(_plats, line)

        for key, value in data.items():
            if key.startswith("_"):
                continue
            line = line.replace(key, value)
        out.append(line)
    return "".join(out)


def safe_name(app_name: str) -> str:
    """Return a filesystem-safe variant of an app name (used for output filenames)."""
    s = re.sub(r"[^a-zA-Z0-9.\-]", "", app_name.replace(" ", "."))
    return re.sub(r"\.{2,}", ".", s).strip(".")


# ---------------------------------------------------------------------------
# BBCode -> HTML preview (forum-style rendering)
# ---------------------------------------------------------------------------
# Subset rendered by Qt's rich-text engine (QTextEdit):
#   bold, italic, underline, strikethrough, color, size, url, img, list,
#   quote, code, spoiler (rendered as <blockquote> with header), youtube
#   (rendered as a thumbnail+link block).  Tags not in the subset pass
#   through verbatim so the user still sees them.

_HTML_ESCAPE = {"&": "&amp;", "<": "&lt;", ">": "&gt;"}


def _escape_html(s: str) -> str:
    for k, v in _HTML_ESCAPE.items():
        s = s.replace(k, v)
    return s


_INLINE_PAIRS = [
    (re.compile(r"\[b\](.*?)\[/b\]",      re.DOTALL | re.IGNORECASE), r"<b>\1</b>"),
    (re.compile(r"\[i\](.*?)\[/i\]",      re.DOTALL | re.IGNORECASE), r"<i>\1</i>"),
    (re.compile(r"\[u\](.*?)\[/u\]",      re.DOTALL | re.IGNORECASE), r"<u>\1</u>"),
    (re.compile(r"\[s\](.*?)\[/s\]",      re.DOTALL | re.IGNORECASE), r"<s>\1</s>"),
    (re.compile(r"\[code\](.*?)\[/code\]", re.DOTALL | re.IGNORECASE),
     r"<pre style='background:#1d1d28;padding:6px;border-radius:3px;'>\1</pre>"),
    (re.compile(r"\[quote\](.*?)\[/quote\]", re.DOTALL | re.IGNORECASE),
     r"<blockquote style='border-left:3px solid #555;padding-left:8px;margin:4px 0;color:#bbb;'>\1</blockquote>"),
]

_COLOR_RE   = re.compile(r"\[color=([^\]]+)\](.*?)\[/color\]", re.DOTALL | re.IGNORECASE)
_SIZE_RE    = re.compile(r"\[size=(\d+)\](.*?)\[/size\]", re.DOTALL | re.IGNORECASE)
_URL_NAMED  = re.compile(r"\[url=([^\]]+)\](.*?)\[/url\]", re.DOTALL | re.IGNORECASE)
_URL_BARE   = re.compile(r"\[url\](.*?)\[/url\]", re.DOTALL | re.IGNORECASE)
_IMG_RE     = re.compile(r"\[img\](.*?)\[/img\]", re.DOTALL | re.IGNORECASE)
_SPOIL_NAMED = re.compile(r'\[spoiler="([^"]*)"\](.*?)\[/spoiler\]', re.DOTALL | re.IGNORECASE)
_SPOIL_BARE  = re.compile(r"\[spoiler\](.*?)\[/spoiler\]", re.DOTALL | re.IGNORECASE)
_YOUTUBE_RE  = re.compile(r"\[youtube\](.*?)\[/youtube\]", re.DOTALL | re.IGNORECASE)
_LIST_NUM    = re.compile(r"\[list=1\](.*?)\[/list\]", re.DOTALL | re.IGNORECASE)
_LIST_BUL    = re.compile(r"\[list\](.*?)\[/list\]", re.DOTALL | re.IGNORECASE)


def _render_list(body: str, ordered: bool) -> str:
    items = re.split(r"\[\*\]", body)
    items = [it.strip() for it in items if it.strip()]
    tag = "ol" if ordered else "ul"
    lis = "".join(f"<li>{it}</li>" for it in items)
    return f"<{tag}>{lis}</{tag}>"


def bbcode_to_html(text: str) -> str:
    """Convert BBCode source to HTML suitable for QTextEdit's rich-text mode.

    Best-effort, not a full BBCode parser — handles the tag subset the
    in-app editor produces.  Unknown tags pass through verbatim so the
    user still sees them in the preview.
    """
    s = _escape_html(text)

    # Inline pairs first.
    for pat, repl in _INLINE_PAIRS:
        # Repeat until stable so nested same-name tags collapse.
        prev = None
        while prev != s:
            prev = s
            s = pat.sub(repl, s)

    # color / size — Qt supports inline style on <span>.
    s = _COLOR_RE.sub(lambda m: f"<span style='color:{m.group(1)}'>{m.group(2)}</span>", s)
    s = _SIZE_RE.sub(lambda m: f"<span style='font-size:{m.group(1)}%'>{m.group(2)}</span>", s)

    # url / img.
    s = _URL_NAMED.sub(lambda m: f"<a href='{m.group(1)}'>{m.group(2)}</a>", s)
    s = _URL_BARE.sub(lambda m: f"<a href='{m.group(1)}'>{m.group(1)}</a>", s)
    s = _IMG_RE.sub(lambda m: f"<img src='{m.group(1)}'>", s)

    # Spoilers — Qt has no <details>, so render as a labelled blockquote.
    spoiler_style = ("border:1px solid #555;padding:4px 8px;margin:4px 0;"
                     "background:#252535;border-radius:3px;")
    s = _SPOIL_NAMED.sub(
        lambda m: (f"<div style='{spoiler_style}'>"
                   f"<b>Spoiler:</b> <i>{m.group(1)}</i><br>{m.group(2)}</div>"), s)
    s = _SPOIL_BARE.sub(
        lambda m: f"<div style='{spoiler_style}'><b>Spoiler</b><br>{m.group(1)}</div>", s)

    # YouTube — render a small placeholder card.
    s = _YOUTUBE_RE.sub(
        lambda m: (f"<div style='border:1px solid #555;padding:6px;margin:4px 0;'>"
                   f"<b>YouTube:</b> {m.group(1)}</div>"), s)

    # Lists last so item bodies have already been rendered.
    s = _LIST_NUM.sub(lambda m: _render_list(m.group(1), ordered=True),  s)
    s = _LIST_BUL.sub(lambda m: _render_list(m.group(1), ordered=False), s)

    # Newlines -> <br> outside block elements (ul/ol/blockquote/pre/div
    # already break visually).  Cheap approach: replace remaining \n.
    s = s.replace("\n", "<br>")
    return s
