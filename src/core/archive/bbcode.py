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
