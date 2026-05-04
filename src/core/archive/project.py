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
class BuildIdRecord:
    """Nested per-build record on AppEntry.{current,previous}_buildid.

    Holds the Steam buildid string plus the PICS branches.<branch>.timeupdated
    timestamp (Unix seconds) for that buildid.  An empty record means
    "never observed" — buildid="" + timeupdated=0.

    Stored nested in JSON so the timestamp lives next to the buildid it
    describes.  _load_app_entry accepts legacy string form
    ("current_buildid": "200") and lifts it into BuildIdRecord(buildid="200")
    for forward compat with .xarchive files predating this field.
    """
    buildid:     str = ""
    timeupdated: int = 0


@dataclass
class ManifestRecord:
    """One historical (depot, manifest) pair captured after a successful
    download.  Lets future runs replay an old build via `archive depot
    --app/--depot/--manifest` even after Steam advances the live buildid.

    Recorded per-platform: when --platform all triggers Windows + Linux,
    each platform produces its own row, never the literal 'all'.  The
    same depot under two platforms (shared content depot) appears twice
    with the same depot_id/manifest_gid but different platform.
    """
    buildid:      str = ""
    branch:       str = "public"
    platform:     str = ""             # actual platform name, never "all"
    depot_id:     int = 0
    depot_name:   str = ""
    manifest_gid: str = ""
    timeupdated:  int = 0              # PICS branches.<branch>.timeupdated; 0 = unknown


@dataclass
class AppEntry:
    """One Steam app being tracked.  Per-app overrides go here; missing fields
    fall back to project-level defaults."""
    app_id:           int  = 0
    # Display name from the Steam product-info common section.  Auto-
    # populated by poll.detect_changes / runner.run_one_app the first time
    # we ever see this app, then refreshed each successful run.  User can
    # override in the GUI if Steam's name is stale or wrong.
    name:             str  = ""
    branch:           str  = "public"      # public, beta, etc.
    branch_password:  str  = ""            # for password-protected branches
    platform:         str  = ""            # "" = use project default
    # Per-app crack mode override.  "" = inherit project.crack_mode (or
    # CLI --crack); "off" = explicitly skip the crack pipeline for this
    # app even when the project default sets one.  Other values:
    # "gse" / "coldclient" / "all".
    crack_mode:       str  = ""

    # Last seen build for poll-on-change, plus the PICS timeupdated for
    # that build.  Nested record so the timestamp lives next to its
    # buildid.  Embedded here rather than in a sidecar buildids.json
    # file (D3 decision, 2026-04-28).
    current_buildid:  BuildIdRecord = field(default_factory=BuildIdRecord)
    # The build before the latest change.  Set by runner.run_one_app
    # when current_buildid moves so BBCode posts and notifications can
    # reference the actual previous version.
    previous_buildid: BuildIdRecord = field(default_factory=BuildIdRecord)

    # Append-only history of (buildid, branch, platform, depot, manifest_gid)
    # tuples seen across all runs.  Lets users feed an old build back into
    # `archive depot` later.  Dedup key = full tuple — same buildid under
    # two platforms intentionally produces two rows.
    manifest_history: list[ManifestRecord] = field(default_factory=list)

    def __post_init__(self):
        # Allow ergonomic construction with bare strings:
        # `AppEntry(current_buildid="200")` still works and lifts to
        # BuildIdRecord(buildid="200").  Keeps callers (tests, GUI
        # row-readers) from having to wrap every assignment.
        if isinstance(self.current_buildid, str):
            self.current_buildid = BuildIdRecord(buildid=self.current_buildid)
        if isinstance(self.previous_buildid, str):
            self.previous_buildid = BuildIdRecord(buildid=self.previous_buildid)


@dataclass
class UnstubOptions:
    """SteamStub unpacker tunables.  Mirror of base_unpacker.options keys —
    note that `keepstub` is the inverse of the underlying `zerodostub`
    flag (so the default of "zero the DOS stub" is keepstub=False).
    """
    keepbind:       bool = False
    keepstub:       bool = False
    dumppayload:    bool = False
    dumpdrmp:       bool = False
    realign:        bool = False
    recalcchecksum: bool = False


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

    # Notify mode (Phase 4 parity with SteamArchiver --notify / --notify-delay):
    #   "pre"   — fire one notification before download starts; no upload links
    #   "delay" — fire one notification after upload completes; with links
    #   "both"  — fire both
    #   ""      — auto: "delay" if MultiUp creds set, else "pre"
    # CLI --notify / --notify-delay / --notify-both override this field.
    notify_mode: str = ""

    # ----- Persistent run-time knobs ------------------------------------
    # These mirror the CLI flags of the same name.  When the CLI doesn't
    # supply a value, the project's stored value is used instead;
    # otherwise the CLI value wins AND is written back here so the next
    # run defaults to the same setting.

    # Download / archive shape
    workers:          int = 8           # --workers
    compression:      int = 9           # --compression
    archive_password: str = ""          # --archive-password
    volume_size:      str = ""          # --volume-size (raw string, parsed at use)
    language:         str = "english"   # --language
    max_retries:      int = 1           # --max-retries

    # Upload knobs
    upload_description:     str  = ""    # --description (MultiUp project + file)
    max_concurrent_uploads: int  = 1     # --max-concurrent-uploads
    delete_archives:        bool = False # --delete-archives

    # Crack tunables
    experimental: bool          = False                 # --experimental
    unstub:       UnstubOptions = field(default_factory=UnstubOptions)

    # Crack mode default for runs against this project.  "" = no crack;
    # "coldclient" / "gse" pick the unpacker.  CLI's --crack flag and
    # the GUI's per-run combo override this on a per-run basis but
    # also persist back here when supplied (sticky default).
    crack_mode:   str           = ""

    # Polling loop (Phase 5):
    #   restart_delay > 0 enables poll-on-change mode — the CLI loops
    #   forever, fetching product-info every restart_delay seconds and
    #   only triggering downloads for apps whose Steam buildid differs
    #   from the AppEntry.current_buildid we persisted last time.
    #   restart_delay == 0 (default) = legacy single-pass mode.
    #   batch_size > 0 chunks the product-info RPC; 0 = single batch.
    restart_delay: int = 0           # --restart-delay (seconds)
    batch_size:    int = 0           # --batch-size

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

    apps_raw   = data.pop("apps", [])
    crack_raw  = data.pop("crack", {})
    unstub_raw = data.pop("unstub", {})

    known = set(ArchiveProject.__dataclass_fields__) - {"apps", "crack", "unstub"}
    filtered = {k: v for k, v in data.items() if k in known}

    proj = ArchiveProject(**filtered)
    proj.apps   = [_load_app_entry(a) for a in apps_raw]
    proj.crack  = _load_crack_identity(crack_raw)
    proj.unstub = _load_unstub_options(unstub_raw)
    return proj


def _load_app_entry(d: dict) -> AppEntry:
    if not isinstance(d, dict):
        return AppEntry()
    known = set(AppEntry.__dataclass_fields__)
    history_raw = d.get("manifest_history", []) or []
    nested_keys = {"current_buildid", "previous_buildid", "manifest_history"}
    filtered = {
        k: v for k, v in d.items()
        if k in known and k not in nested_keys
    }

    # Backward compat: pre-nesting .xarchive files stored the matching
    # timestamps as flat top-level fields.  Lift them into the new nested
    # records when loading legacy projects.
    legacy_curr_ts = int(d.get("current_buildid_timeupdated") or 0)
    legacy_prev_ts = int(d.get("previous_buildid_timeupdated") or 0)

    entry = AppEntry(**filtered)
    entry.current_buildid  = _load_buildid_record(
        d.get("current_buildid"),  legacy_ts=legacy_curr_ts,
    )
    entry.previous_buildid = _load_buildid_record(
        d.get("previous_buildid"), legacy_ts=legacy_prev_ts,
    )
    entry.manifest_history = [_load_manifest_record(r) for r in history_raw]
    return entry


def _load_buildid_record(d, *, legacy_ts: int = 0) -> BuildIdRecord:
    """Accept either the new nested dict, the legacy bare string, or
    None.  legacy_ts gets folded in only when d came in as a string."""
    if d is None or d == "":
        return BuildIdRecord(timeupdated=legacy_ts) if legacy_ts else BuildIdRecord()
    if isinstance(d, str):
        return BuildIdRecord(buildid=d, timeupdated=legacy_ts)
    if isinstance(d, dict):
        return BuildIdRecord(
            buildid     = str(d.get("buildid", "") or ""),
            timeupdated = int(d.get("timeupdated", 0) or 0),
        )
    return BuildIdRecord()


def _load_manifest_record(d: dict) -> ManifestRecord:
    if not isinstance(d, dict):
        return ManifestRecord()
    known = set(ManifestRecord.__dataclass_fields__)
    return ManifestRecord(**{k: v for k, v in d.items() if k in known})


def _load_crack_identity(d: dict) -> CrackIdentity:
    if not isinstance(d, dict):
        return CrackIdentity()
    known = set(CrackIdentity.__dataclass_fields__)
    return CrackIdentity(**{k: v for k, v in d.items() if k in known})


def _load_unstub_options(d: dict) -> UnstubOptions:
    if not isinstance(d, dict):
        return UnstubOptions()
    known = set(UnstubOptions.__dataclass_fields__)
    return UnstubOptions(**{k: v for k, v in d.items() if k in known})


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
