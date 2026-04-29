"""PatchForge archive project file (.xarchive) — save/load JSON state.

A .xarchive captures everything needed to drive `patchforge archive` for one
workflow:

  - The list of Steam app IDs to track, with per-app overrides.
  - Crack identity (Steam64 ID, username, language, listen port, achievement
    language) — NOT credentials.  See note below.
  - Buildids state (last-seen build ID per app/branch — embedded directly,
    not in a sidecar file).
  - BBCode template content (copied from the vendored default on project
    create; user edits in the BBCode page).
  - Output dir override (falls back to app_settings.archive_output_dir, then
    the --download CLI flag).

  CREDENTIALS (Steam refresh tokens, Steam Web API key) live in
  archive_credentials.json under the user config dir.  They MUST NEVER be
  written into a .xarchive — the project file is meant to be shareable and
  version-controllable.  See src/core/archive/credentials.py.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

# Bump when an existing field's semantics change in a non-additive way.
# Pure additions of new fields don't require a bump — defaults handle backfill.
_CURRENT_SCHEMA = 1


@dataclass
class AppEntry:
    """One Steam app being tracked.  Per-app overrides go here; missing fields
    fall back to project-level defaults."""
    app_id:           int  = 0
    branch:           str  = "public"      # public, beta, etc.
    branch_password:  str  = ""            # for password-protected branches
    platform:         str  = ""            # "" = use project default

    # Last seen build for poll-on-change.  Embedded here rather than in a
    # sidecar buildids.json file (D3 decision, 2026-04-28).
    current_buildid:  str  = ""


@dataclass
class CrackIdentity:
    """Per-project identity used by Goldberg / ColdClient config generation.

    These are NOT credentials.  Steam64 ID and username are public; language
    / listen port / achievement language are user preferences.  Safe to share
    along with the .xarchive.

    The Steam Web API key — which IS a credential — is stored separately in
    archive_credentials.json (web_api_key field).
    """
    steam_id:             int = 0          # public SteamID64
    username:             str = ""         # display username (not login)
    language:             str = "english"
    listen_port:          int = 47584
    achievement_language: str = "english"


@dataclass
class ArchiveProject:
    schema_version: int = _CURRENT_SCHEMA

    # Project-level metadata
    name:        str = ""
    description: str = ""

    # App tracking
    apps: list[AppEntry] = field(default_factory=list)

    # Project-wide defaults (overridable per AppEntry)
    default_platform: str = "windows"   # "windows" | "linux" | "macos" | "all"

    # Output
    output_dir: str = ""    # "" = fall back to app_settings.archive_output_dir

    # Crack identity
    crack: CrackIdentity = field(default_factory=CrackIdentity)

    # BBCode template body (copied from vendored default on project create)
    bbcode_template: str = ""

    # Free-form CLI args appended to `patchforge archive` invocations
    extra_args: str = ""


def save(project: ArchiveProject, path: Path) -> None:
    """Serialize project to disk as JSON."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(asdict(project), f, indent=2)


def load(path: Path) -> ArchiveProject:
    """Load a .xarchive file.  Forward-compatibility: unknown fields are dropped."""
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)

    version = int(data.get("schema_version", 1))
    if version > _CURRENT_SCHEMA:
        raise ValueError(
            f"{path} was written by a newer PatchForge "
            f"(schema {version}, this build supports {_CURRENT_SCHEMA}). "
            f"Upgrade PatchForge to open this project."
        )

    apps_raw = data.pop("apps", [])
    crack_raw = data.pop("crack", {})

    known = set(ArchiveProject.__dataclass_fields__) - {"apps", "crack"}
    filtered = {k: v for k, v in data.items() if k in known}

    proj = ArchiveProject(**filtered)
    proj.apps = [_load_app_entry(a) for a in apps_raw]
    proj.crack = _load_crack_identity(crack_raw)
    return proj


def _load_app_entry(d: dict) -> AppEntry:
    if not isinstance(d, dict):
        return AppEntry()
    known = set(AppEntry.__dataclass_fields__)
    return AppEntry(**{k: v for k, v in d.items() if k in known})


def _load_crack_identity(d: dict) -> CrackIdentity:
    if not isinstance(d, dict):
        return CrackIdentity()
    known = set(CrackIdentity.__dataclass_fields__)
    return CrackIdentity(**{k: v for k, v in d.items() if k in known})


def default_bbcode_template() -> str:
    """Return the small vendored BBCode template that new .xarchive files start with."""
    template_path = Path(__file__).parent / "data" / "template.txt"
    try:
        return template_path.read_text(encoding="utf-8")
    except OSError:
        return ""


def new_project(name: str = "") -> ArchiveProject:
    """Build a new ArchiveProject pre-populated with the default BBCode template."""
    return ArchiveProject(name=name, bbcode_template=default_bbcode_template())
