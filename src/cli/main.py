"""PatchForge CLI — mirrors all GUI options."""

import sys

# Archive-mode early monkey-patch: `steam[client]` requires gevent's
# socket/ssl monkey-patches to be applied BEFORE anything else imports
# urllib3 / ssl.  Sniff sys.argv directly so we can patch before argparse
# (which imports os, which is fine) but more importantly before
# src.core.archive._extras runs its missing_extras() probe (which imports
# steam.client → urllib3 → ssl).  No-op when the archive extras aren't
# installed or when the user isn't running an archive subcommand.
if len(sys.argv) >= 2 and sys.argv[1] == "archive":
    try:
        import steam.monkey  # type: ignore
        steam.monkey.patch_minimal()
    except ImportError:
        pass

import argparse                              # noqa: E402
import json                                  # noqa: E402
from pathlib import Path                     # noqa: E402

from ..core.fmt import format_size as _fmt_size   # noqa: E402


def run_cli():
    parser = _build_parser()
    # Optional shell-completion hook.  If `argcomplete` is installed and the
    # user has run `eval "$(register-python-argcomplete patchforge)"` in
    # their shell rc, this short-circuits the call when the shell is asking
    # for completions.  No-op otherwise.
    try:
        import argcomplete
        argcomplete.autocomplete(parser)
    except ImportError:
        pass
    args = parser.parse_args()

    if not hasattr(args, "func"):
        parser.print_help()
        sys.exit(0)

    args.func(args)


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------

def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="patchforge",
        description="PatchForge — video game binary patch maker",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=_EPILOG,
    )
    sub = parser.add_subparsers(metavar="COMMAND")

    _add_build(sub)
    _add_new_project(sub)
    _add_show_project(sub)
    _add_repack(sub)
    _add_new_repack_project(sub)
    _add_show_repack_project(sub)
    _add_archive(sub)

    return parser


_EPILOG = """\
Examples:
  # Build a patch directly from two game folders
  patchforge build --source-dir game_v1/ --target-dir game_v2/ --app-name "My Game" --version 1.1

  # Create a project file, then build from it
  patchforge new-project --output patch.xpm
  patchforge build --project patch.xpm --source-dir game_v1/ --target-dir game_v2/

  # Show a saved patch project
  patchforge show-project patch.xpm

  # Build a repack installer from a game directory
  patchforge repack --game-dir game/ --output-dir dist/ --app-name "My Game" --threads 8

  # Repack with optional components
  patchforge repack --game-dir game/ --app-name "My Game" \\
    --component '{"label":"High-res textures","folder":"hires/","default_checked":false,"group":""}' \\
    --component '{"label":"English voices","folder":"voices_en/","default_checked":true,"group":"voices"}' \\
    --component '{"label":"Japanese voices","folder":"voices_jp/","default_checked":false,"group":"voices"}'

  # Create/show a repack project file
  patchforge new-repack-project --output installer.xpr --app-name "My Game"
  patchforge show-repack-project installer.xpr

  # Archive mode (Steam depot downloader; requires `pip install patchforge[archive]`)
  patchforge archive login
  patchforge archive info 730 570
  patchforge archive new-project --output tracker.xarchive
  patchforge archive show-project tracker.xarchive
"""


# ---------------------------------------------------------------------------
# build
# ---------------------------------------------------------------------------

def _add_build(sub):
    p = sub.add_parser("build", help="Generate a patch executable")

    # Project file (optional — individual flags override it)
    p.add_argument("--project", metavar="FILE",
                   help="Load settings from a .xpm project file (flags override)")

    # Directory paths
    p.add_argument("--source-dir", metavar="DIR", dest="source_dir",
                   help="Original (old) game folder")
    p.add_argument("--target-dir", metavar="DIR", dest="target_dir",
                   help="Patched (new) game folder")
    p.add_argument("--output-dir", metavar="DIR",
                   help="Directory to write the output .exe (default: current directory)")

    # Metadata
    p.add_argument("--app-name",    metavar="NAME", help="Application name shown in patcher")
    p.add_argument("--app-note",    metavar="TEXT", dest="app_note",
                   help="Short subtitle shown next to the app name")
    p.add_argument("--version",     metavar="VER",  help="Version string (e.g. 1.2.3)")
    p.add_argument("--description", metavar="TEXT", help="Description shown in patcher")
    p.add_argument("--copyright",   metavar="TEXT", help="Copyright notice")
    p.add_argument("--contact",     metavar="TEXT", help="Contact email or URL")
    p.add_argument("--company-info", metavar="TEXT", dest="company_info",
                   help="Publisher / company name")
    p.add_argument("--window-title", metavar="TEXT", dest="window_title",
                   help="Patcher title bar text (defaults to app name)")
    p.add_argument("--patch-exe-name", metavar="STEM", dest="patch_exe_name",
                   help="Output exe filename stem (default: auto from app name + version)")
    p.add_argument("--patch-exe-version", metavar="VER", dest="patch_exe_version",
                   help="Informational version string for the patch exe (e.g. 1.0.0.0)")

    # Engine + compression
    p.add_argument("--engine", choices=["hdiffpatch", "xdelta3", "jojodiff"],
                   help="Patch engine (default: hdiffpatch)")
    p.add_argument("--compression", metavar="PRESET",
                   help="Compression preset key for the chosen engine "
                        "(hdiffpatch: set1_lzma2…set6_bzip2; "
                        "xdelta3: none/paul44/lzma_mem; "
                        "jojodiff: minimal/good/optimal)")
    p.add_argument("--threads", metavar="N", type=int,
                   help="Worker threads for patch generation (default: 1)")
    p.add_argument("--quality", metavar="LEVEL", dest="compressor_quality",
                   choices=["fast", "normal", "max"],
                   help="HDiffPatch compressor quality: fast/normal/max (default: max)")

    # Verification
    p.add_argument("--verify", choices=["crc32c", "md5", "filesize"],
                   dest="verify_method",
                   help="Checksum method (default: crc32c)")

    # Target file discovery
    p.add_argument("--find-method", choices=["manual", "registry", "ini"],
                   help="How the patcher finds the target file (default: manual)")
    p.add_argument("--registry-key",   metavar="KEY",
                   help="Registry key path (for --find-method registry)")
    p.add_argument("--registry-value", metavar="VALUE",
                   help="Registry value name (default: InstallPath)")
    p.add_argument("--ini-path",    metavar="FILE",    help="INI file path")
    p.add_argument("--ini-section", metavar="SECTION", help="INI section name")
    p.add_argument("--ini-key",     metavar="KEY",     help="INI key name")

    # Architecture
    p.add_argument("--arch", choices=["x64", "x86"], help="Output exe architecture (default: x64)")

    # Icon
    p.add_argument("--icon-path", metavar="FILE", dest="icon_path",
                   help="Optional .ico file to embed as the patcher's application icon")

    # Feature: custom diff args
    p.add_argument("--extra-args", metavar="ARGS", dest="extra_diff_args",
                   help="Extra CLI arguments passed verbatim to the diff engine")

    # Feature: patching behaviour
    p.add_argument("--delete-extra-files", action="store_true", default=None,
                   dest="delete_extra_files",
                   help="Delete game files absent from the target version (default: on)")
    p.add_argument("--no-delete-extra-files", action="store_false",
                   dest="delete_extra_files",
                   help="Keep game files absent from the target version")
    p.add_argument("--preserve-timestamps", action="store_true", default=False,
                   dest="preserve_timestamps",
                   help="Restore original file modification times after patching")
    p.add_argument("--run-on-startup", metavar="CMD", dest="run_on_startup",
                   help="Shell command to run when the patcher window opens")
    p.add_argument("--run-before", metavar="CMD", dest="run_before",
                   help="Shell command to run before patching starts")
    p.add_argument("--run-after",  metavar="CMD", dest="run_after",
                   help="Shell command to run after patching succeeds")
    p.add_argument("--run-on-finish", metavar="CMD", dest="run_on_finish",
                   help="Shell command to run after successful patch + close dialog")
    p.add_argument("--extra-file", metavar="SRC[:DEST]", action="append",
                   dest="extra_files",
                   help="Embed an extra file in the patcher exe. DEST is the path the "
                        "patcher writes it to; defaults to the basename of SRC. Repeatable.")

    # Feature: backup
    p.add_argument("--backup-at", choices=["disabled", "same_folder", "custom"],
                   dest="backup_at",
                   help="Backup behaviour: disabled / same_folder (default) / custom")
    p.add_argument("--backup-path", metavar="DIR", dest="backup_path",
                   help="Backup directory (used when --backup-at custom)")

    # Feature: backdrop
    p.add_argument("--backdrop", metavar="FILE", dest="backdrop_path",
                   help="Background image for the patcher window (PNG/JPEG/BMP)")

    # Save project after build
    p.add_argument("--save-project", metavar="FILE",
                   help="Save resolved settings to a .xpm project file after building")

    # Validate without building
    p.add_argument("--check", action="store_true",
                   help="Validate inputs and resolved settings without running the engine")

    # Output verbosity
    out_grp = p.add_mutually_exclusive_group()
    out_grp.add_argument("--quiet", "-q", action="store_true",
                         help="Suppress progress output (errors still go to stderr)")
    out_grp.add_argument("--verbose", "-v", action="store_true",
                         help="Verbose output (reserved for future debug logging)")
    p.add_argument("--json", action="store_true",
                   help="Emit a JSON result object on stdout (implies --quiet for progress)")

    p.set_defaults(func=_cmd_build)


def _cmd_build(args):
    from src.core.project import ProjectSettings, load, save
    from src.core.patch_builder import build

    # Start with defaults or loaded project
    if args.project:
        try:
            settings = load(Path(args.project))
        except Exception as exc:
            _die(f"Failed to load project '{args.project}': {exc}", EXIT_INPUT)
    else:
        settings = ProjectSettings()

    _apply_truthy(settings, args, _BUILD_FIELDS)
    if args.delete_extra_files is not None:
        settings.delete_extra_files = args.delete_extra_files
    if args.preserve_timestamps:
        settings.preserve_timestamps = True
    if args.extra_files:
        parsed = []
        for entry in args.extra_files:
            # On Windows the SRC may begin with a drive letter "X:"; skip the
            # first 2 chars when scanning for the SRC:DEST separator so the
            # drive colon isn't mistaken for it.
            sep_offset = 2 if (len(entry) >= 2 and entry[0].isalpha()
                               and entry[1] == ":") else 0
            sep_idx = entry.find(":", sep_offset)
            if sep_idx >= 0:
                src, dest = entry[:sep_idx], entry[sep_idx + 1:]
            else:
                src, dest = entry, Path(entry).name
            parsed.append({"src": src.strip(), "dest": dest.strip()})
        settings.extra_files = parsed

    # Save project if requested
    if args.save_project:
        try:
            save(settings, Path(args.save_project))
            if not (args.quiet or args.json):
                print(f"Project saved: {args.save_project}")
        except Exception as exc:
            _warn(f"Could not save project: {exc}")

    # --json implies --quiet for progress (we still emit one JSON object on stdout).
    quiet = args.quiet or args.json

    # U5: front-loaded validation runs on the normal build path too, not
    # just under --check.  Bad inputs fail before the engine is invoked.
    errors = _validate_build_inputs(settings)
    if errors:
        _emit_validation_errors(errors, args.json)

    # --check: short-circuit before running the engine.
    if args.check:
        if args.json:
            print(json.dumps({
                "success":     True,
                "checked":     True,
                "app_name":    settings.app_name,
                "engine":      settings.engine,
                "compression": settings.compression,
                "source_dir":  settings.source_dir,
                "target_dir":  settings.target_dir,
                "output_dir":  settings.output_dir or "",
            }))
        elif not args.quiet:
            print(f"OK — settings valid for '{settings.app_name}' using {settings.engine}.")
            print(f"  source:      {settings.source_dir}")
            print(f"  target:      {settings.target_dir}")
            print(f"  output_dir:  {settings.output_dir or '(current dir)'}")
            print(f"  engine:      {settings.engine}")
            print(f"  compression: {settings.compression}")
        return

    # Build
    def progress(pct: int, msg: str, kind: str = "phase"):  # noqa: ARG001  kind ignored on CLI
        bar_len = 30
        filled = int(bar_len * pct / 100)
        bar = "#" * filled + "-" * (bar_len - filled)
        # U7: ANSI clear-to-EOL after the message so a long-then-short
        # message sequence doesn't leave trailing chars from the prior line.
        print(f"\r[{bar}] {pct:3d}%  {msg}\033[K", end="", flush=True)

    if not quiet:
        print(f"Building patch for '{settings.app_name}' using {settings.engine}...")
    result = build(settings, progress=None if quiet else progress)
    if not quiet:
        print()  # newline after progress bar

    if args.json:
        out = {
            "success":     result.success,
            "output_path": str(result.output_path) if result.output_path else None,
            "patch_size":  result.patch_size,
            "output_size": result.output_size,
        }
        if not result.success:
            out["error"] = result.error
        print(json.dumps(out))
        if not result.success:
            sys.exit(EXIT_BUILD)
        return

    if not result.success:
        _die(f"Build failed: {result.error}", EXIT_BUILD)

    if not quiet:
        print(f"\nOutput:      {result.output_path}")
        print(f"Patch size:  {_fmt_size(result.patch_size)}")
        print(f"Output size: {_fmt_size(result.output_size)}")


# ---------------------------------------------------------------------------
# new-project
# ---------------------------------------------------------------------------

def _add_new_project(sub):
    p = sub.add_parser("new-project",
                       help="Create a new project file with default settings")
    p.add_argument("--output", metavar="FILE", required=True,
                   help="Path to write the .xpm project file")
    p.add_argument("--app-name",    metavar="NAME")
    p.add_argument("--app-note",    metavar="TEXT", dest="app_note")
    p.add_argument("--version",     metavar="VER")
    p.add_argument("--description", metavar="TEXT")
    p.add_argument("--copyright",   metavar="TEXT")
    p.add_argument("--contact",     metavar="TEXT")
    p.add_argument("--company-info", metavar="TEXT", dest="company_info")
    p.add_argument("--window-title", metavar="TEXT", dest="window_title")
    p.add_argument("--patch-exe-name",    metavar="STEM", dest="patch_exe_name")
    p.add_argument("--patch-exe-version", metavar="VER",  dest="patch_exe_version")
    p.add_argument("--engine",      choices=["hdiffpatch", "xdelta3", "jojodiff"])
    p.add_argument("--compression", metavar="PRESET")
    p.add_argument("--threads",     metavar="N", type=int)
    p.add_argument("--quality",     metavar="LEVEL", dest="compressor_quality",
                   choices=["fast", "normal", "max"])
    p.add_argument("--verify",      choices=["crc32c", "md5", "filesize"],
                   dest="verify_method")
    p.add_argument("--arch",        choices=["x64", "x86"])
    p.add_argument("--icon-path",   metavar="FILE", dest="icon_path")
    p.add_argument("--extra-args",  metavar="ARGS", dest="extra_diff_args")
    p.add_argument("--run-before",  metavar="CMD",  dest="run_before")
    p.add_argument("--run-after",   metavar="CMD",  dest="run_after")
    p.add_argument("--backup-at",   choices=["disabled", "same_folder", "custom"],
                   dest="backup_at")
    p.add_argument("--backup-path", metavar="DIR",  dest="backup_path")
    p.add_argument("--backdrop",    metavar="FILE", dest="backdrop_path")
    p.set_defaults(func=_cmd_new_project)


def _cmd_new_project(args):
    from src.core.project import ProjectSettings, save

    s = ProjectSettings()
    _apply_truthy(s, args, _NEW_PROJECT_FIELDS)

    out = Path(args.output)
    save(s, out)
    print(f"Project created: {out}")
    print(f"  engine:      {s.engine}")
    print(f"  compression: {s.compression}")
    print(f"  verify:      {s.verify_method}")
    print(f"  arch:        {s.arch}")


# ---------------------------------------------------------------------------
# show-project
# ---------------------------------------------------------------------------

def _add_show_project(sub):
    p = sub.add_parser("show-project", help="Display settings from a .xpm project file")
    p.add_argument("project", metavar="FILE", help="Path to .xpm project file")
    p.set_defaults(func=_cmd_show_project)


def _cmd_show_project(args):
    from src.core.project import load
    from dataclasses import asdict

    try:
        s = load(Path(args.project))
    except Exception as exc:
        _die(f"Failed to load project: {exc}", EXIT_INPUT)

    print(f"Project: {args.project}")
    extra_files = asdict(s).pop("extra_files", [])
    for key, val in asdict(s).items():
        if key == "extra_files":
            continue
        if val is not None and val != "" and val != []:
            print(f"  {key:<20} {val}")
    # U9: pretty-print extra_files instead of dumping the raw dict list.
    if extra_files:
        print(f"  {'extra_files':<20} ({len(extra_files)})")
        for ef in extra_files:
            src = ef.get("src", "")
            dest = ef.get("dest", "")
            print(f"    {dest}  ←  {src}")


# ---------------------------------------------------------------------------
# repack
# ---------------------------------------------------------------------------

def _add_repack(sub):
    p = sub.add_parser("repack", help="Build a self-contained installer exe from a game directory")

    p.add_argument("--project", metavar="FILE",
                   help="Load settings from a .xpr repack project file (flags override)")

    # Paths
    p.add_argument("--game-dir",   metavar="DIR", dest="game_dir",
                   help="Game directory to compress and install")
    p.add_argument("--output-dir", metavar="DIR", dest="output_dir",
                   help="Directory to write the output .exe (default: current directory)")

    # Metadata
    p.add_argument("--app-name",    metavar="NAME", dest="app_name",
                   help="Application name shown in the installer")
    p.add_argument("--app-note",    metavar="TEXT", dest="app_note",
                   help="Short subtitle shown next to the app name")
    p.add_argument("--version",     metavar="VER",
                   help="Version string (e.g. 1.0)")
    p.add_argument("--description", metavar="TEXT",
                   help="Description shown in the installer")
    p.add_argument("--copyright",   metavar="TEXT",
                   help="Copyright notice")
    p.add_argument("--contact",     metavar="TEXT",
                   help="Contact email or URL")
    p.add_argument("--company-info", metavar="TEXT", dest="company_info",
                   help="Publisher / company name")
    p.add_argument("--window-title", metavar="TEXT", dest="window_title",
                   help="Installer title bar text (defaults to app name)")
    p.add_argument("--installer-exe-name", metavar="STEM", dest="installer_exe_name",
                   help="Output exe filename stem (default: auto from app name + version)")
    p.add_argument("--installer-exe-version", metavar="VER", dest="installer_exe_version",
                   help="Informational version string for the exe (e.g. 1.0.0.0)")

    # Compression
    p.add_argument("--codec", choices=["lzma", "zstd"], default=None,
                   help="Compression codec: lzma (XZ/LZMA2) or zstd (default: lzma)")
    p.add_argument("--compression",
                   choices=["fast", "normal", "max", "ultra"],
                   help="Quality preset — lzma: fast/normal/max  |  zstd: fast/normal/max/ultra  (default: max)")
    p.add_argument("--threads", metavar="N", type=int,
                   help="Compression threads (default: 1)")
    p.add_argument("--arch", choices=["x64", "x86"],
                   help="Output exe architecture (default: x64)")

    # Visual
    p.add_argument("--icon-path", metavar="FILE", dest="icon_path",
                   help="Optional .ico file to embed as the installer's icon")
    p.add_argument("--backdrop",  metavar="FILE", dest="backdrop_path",
                   help="Background image for the installer window (PNG/JPEG/BMP)")

    # Post-install behaviour
    p.add_argument("--install-registry-key", metavar="KEY", dest="install_registry_key",
                   help=r"Registry key written to HKCU after install (e.g. SOFTWARE\Company\Game)")
    p.add_argument("--run-after", metavar="CMD", dest="run_after_install",
                   help="Shell command to run after successful install")
    p.add_argument("--detect-running", metavar="EXE", dest="detect_running_exe",
                   help="Warn if this process is running before install (e.g. MyGame.exe)")
    p.add_argument("--close-delay", metavar="N", type=int, dest="close_delay",
                   help="Seconds before auto-closing after success (default: 0 = stay open)")
    p.add_argument("--required-free-space", metavar="GB", type=float,
                   dest="required_free_space_gb",
                   help="Warn if available disk space is below this threshold in GB (default: 0 = disabled)")

    # Integrity
    p.add_argument("--no-verify-crc32", action="store_true", dest="no_verify_crc32",
                   help="Skip CRC32 integrity check after installation (default: verify enabled)")

    # Shortcuts
    p.add_argument("--shortcut-target", metavar="REL_PATH", dest="shortcut_target",
                   help=r"Relative path to the exe for shortcuts (e.g. Bin\Game.exe)")
    p.add_argument("--shortcut-name", metavar="NAME", dest="shortcut_name",
                   help="Shortcut display name (default: app name)")
    p.add_argument("--shortcut-desktop", action="store_true", default=None,
                   dest="shortcut_desktop",
                   help="Create a Desktop shortcut (default: off)")
    p.add_argument("--no-shortcut-desktop", action="store_false",
                   dest="shortcut_desktop",
                   help="Do not create a Desktop shortcut")
    p.add_argument("--shortcut-startmenu", action="store_true", default=None,
                   dest="shortcut_startmenu",
                   help="Create a Start Menu shortcut (default: on)")
    p.add_argument("--no-shortcut-startmenu", action="store_false",
                   dest="shortcut_startmenu",
                   help="Do not create a Start Menu shortcut")

    # Optional components
    p.add_argument("--component", metavar="JSON", action="append", dest="components_json",
                   help=(
                       "Add an optional component as a JSON object. Repeatable. "
                       'Keys: label, folder, default_checked, group, shortcut_target, '
                       'external (bool — write stream to <group_or_label>.bin sidecar). '
                       'Example: \'{"label":"Crack","folder":"crack/","external":true}\''
                   ))

    # Uninstaller
    p.add_argument("--no-uninstaller", action="store_true", dest="no_uninstaller",
                   help="Omit the uninstaller and Add/Remove Programs registration")

    # Data file split
    p.add_argument("--split-bin", action="store_true", dest="split_bin",
                   help="Write compressed game data to a separate base_game.bin file "
                        "(required for games > 3.5 GB; applied automatically above that threshold)")
    p.add_argument("--max-part-size-mb", metavar="MB", type=int, dest="max_part_size_mb",
                   help="Split base_game.bin into <name>.bin.001, .002, ... parts of this "
                        "size in MB (0 = no split). Useful for file hosts with upload caps")

    # Save project after build
    p.add_argument("--save-project", metavar="FILE",
                   help="Save resolved settings to a .xpr project file after building")

    # Validate without building
    p.add_argument("--check", action="store_true",
                   help="Validate inputs and resolved settings without running the engine")

    # Output verbosity
    out_grp = p.add_mutually_exclusive_group()
    out_grp.add_argument("--quiet", "-q", action="store_true",
                         help="Suppress progress output (errors still go to stderr)")
    out_grp.add_argument("--verbose", "-v", action="store_true",
                         help="Verbose output (reserved for future debug logging)")
    p.add_argument("--json", action="store_true",
                   help="Emit a JSON result object on stdout (implies --quiet for progress)")

    p.set_defaults(func=_cmd_repack)


def _cmd_repack(args):
    from src.core.repack_project import RepackSettings, load as load_repack, save as save_repack
    from src.core.repack_builder import build as build_repack

    # Start from defaults or a loaded project
    if args.project:
        try:
            settings = load_repack(Path(args.project))
        except Exception as exc:
            _die(f"Failed to load project '{args.project}': {exc}", EXIT_INPUT)
    else:
        settings = RepackSettings()

    _apply_truthy(settings, args, _REPACK_FIELDS)
    _apply_optional(settings, args, ("close_delay", "required_free_space_gb",
                                     "max_part_size_mb"))
    if args.no_uninstaller:                  settings.include_uninstaller       = False
    if args.no_verify_crc32:                 settings.verify_crc32              = False
    if args.split_bin:                       settings.split_bin                 = True
    if args.shortcut_desktop is not None:    settings.shortcut_create_desktop   = args.shortcut_desktop
    if args.shortcut_startmenu is not None:  settings.shortcut_create_startmenu = args.shortcut_startmenu

    # Parse --component flags
    if args.components_json:
        parsed = []
        for raw in args.components_json:
            try:
                c = json.loads(raw)
            except json.JSONDecodeError as exc:
                _die(f"Invalid --component JSON: {exc}\n  value: {raw}", EXIT_INPUT)
            if "label" not in c or "folder" not in c:
                _die(f"--component JSON must have 'label' and 'folder' keys: {raw}", EXIT_INPUT)
            parsed.append({
                "label":            str(c["label"]),
                "folder":           str(c["folder"]),
                "default_checked":  bool(c.get("default_checked", True)),
                "group":            str(c.get("group", "")),
                "shortcut_target":  str(c.get("shortcut_target", "")),
            })
        settings.components = parsed

    # Save project if requested
    if args.save_project:
        try:
            save_repack(settings, Path(args.save_project))
            if not (args.quiet or args.json):
                print(f"Project saved: {args.save_project}")
        except Exception as exc:
            _warn(f"Could not save project: {exc}")

    # --json implies --quiet for progress (we still emit one JSON object on stdout).
    quiet = args.quiet or args.json

    # U5: front-loaded validation runs on the normal repack path too.
    errors = _validate_repack_inputs(settings)
    if errors:
        _emit_validation_errors(errors, args.json)

    # --check: short-circuit before running the engine.
    if args.check:
        if args.json:
            print(json.dumps({
                "success":     True,
                "checked":     True,
                "app_name":    settings.app_name,
                "codec":       settings.codec,
                "compression": settings.compression,
                "threads":     settings.threads,
                "game_dir":    settings.game_dir,
                "output_dir":  settings.output_dir or "",
                "components":  len(settings.components or []),
            }))
        elif not args.quiet:
            print(f"OK — settings valid for '{settings.app_name}'.")
            print(f"  game_dir:    {settings.game_dir}")
            print(f"  output_dir:  {settings.output_dir or '(current dir)'}")
            print(f"  codec:       {settings.codec}")
            print(f"  compression: {settings.compression}")
            print(f"  threads:     {settings.threads}")
            print(f"  components:  {len(settings.components or [])}")
        return

    # Build
    def progress(pct: int, msg: str, kind: str = "phase"):  # noqa: ARG001  kind ignored on CLI
        bar_len = 30
        filled = int(bar_len * pct / 100)
        bar = "#" * filled + "-" * (bar_len - filled)
        # U7: ANSI clear-to-EOL.
        print(f"\r[{bar}] {pct:3d}%  {msg}\033[K", end="", flush=True)

    if not quiet:
        print(f"Building repack installer for '{settings.app_name}'...")
    result = build_repack(settings, progress=None if quiet else progress)
    if not quiet:
        print()  # newline after progress bar

    if args.json:
        out = {
            "success":           result.success,
            "output_path":       str(result.output_path) if result.output_path else None,
            "bin_path":          str(result.bin_path) if result.bin_path else None,
            "total_files":       result.total_files,
            "uncompressed_size": result.uncompressed_size,
            "output_size":       result.output_size,
        }
        if not result.success:
            out["error"] = result.error
        print(json.dumps(out))
        if not result.success:
            sys.exit(EXIT_BUILD)
        return

    if not result.success:
        _die(f"Repack failed: {result.error}", EXIT_BUILD)

    if not quiet:
        print(f"\nOutput:       {result.output_path}")
        if result.bin_path:
            print(f"Data file:    {result.bin_path}")
        print(f"Files packed: {result.total_files}")
        print(f"Game size:    {_fmt_size(result.uncompressed_size)}")
        print(f"Installer:    {_fmt_size(result.output_size)}")
        ratio = result.output_size / result.uncompressed_size * 100 if result.uncompressed_size else 0
        print(f"Compression:  {ratio:.1f}% of original")


# ---------------------------------------------------------------------------
# new-repack-project
# ---------------------------------------------------------------------------

def _add_new_repack_project(sub):
    p = sub.add_parser("new-repack-project",
                       help="Create a new repack project file with default settings")
    p.add_argument("--output", metavar="FILE", required=True,
                   help="Path to write the .xpr project file")
    p.add_argument("--app-name",    metavar="NAME", dest="app_name")
    p.add_argument("--app-note",    metavar="TEXT", dest="app_note")
    p.add_argument("--version",     metavar="VER")
    p.add_argument("--description", metavar="TEXT")
    p.add_argument("--copyright",   metavar="TEXT")
    p.add_argument("--contact",     metavar="TEXT")
    p.add_argument("--company-info", metavar="TEXT", dest="company_info")
    p.add_argument("--window-title", metavar="TEXT", dest="window_title")
    p.add_argument("--installer-exe-name",    metavar="STEM", dest="installer_exe_name")
    p.add_argument("--installer-exe-version", metavar="VER",  dest="installer_exe_version")
    p.add_argument("--game-dir",    metavar="DIR",  dest="game_dir")
    p.add_argument("--output-dir",  metavar="DIR",  dest="output_dir")
    p.add_argument("--codec", choices=["lzma", "zstd"])
    p.add_argument("--compression", choices=["fast", "normal", "max", "ultra"],
                   metavar="PRESET",
                   help="lzma: fast/normal/max  |  zstd: fast/normal/max/ultra")
    p.add_argument("--threads",     metavar="N",    type=int)
    p.add_argument("--arch",        choices=["x64", "x86"])
    p.add_argument("--icon-path",   metavar="FILE", dest="icon_path")
    p.add_argument("--backdrop",    metavar="FILE", dest="backdrop_path")
    p.add_argument("--install-registry-key", metavar="KEY",  dest="install_registry_key")
    p.add_argument("--run-after",   metavar="CMD",  dest="run_after_install")
    p.add_argument("--detect-running", metavar="EXE", dest="detect_running_exe")
    p.add_argument("--close-delay", metavar="N",    type=int,   dest="close_delay")
    p.add_argument("--required-free-space", metavar="GB", type=float,
                   dest="required_free_space_gb")
    p.add_argument("--no-uninstaller", action="store_true", dest="no_uninstaller")
    p.add_argument("--no-verify-crc32", action="store_true", dest="no_verify_crc32")
    p.add_argument("--shortcut-target",    metavar="REL_PATH", dest="shortcut_target")
    p.add_argument("--shortcut-name",      metavar="NAME",     dest="shortcut_name")
    p.add_argument("--shortcut-desktop",   action="store_true", default=None,
                   dest="shortcut_desktop")
    p.add_argument("--no-shortcut-desktop", action="store_false", dest="shortcut_desktop")
    p.add_argument("--shortcut-startmenu", action="store_true", default=None,
                   dest="shortcut_startmenu")
    p.add_argument("--no-shortcut-startmenu", action="store_false", dest="shortcut_startmenu")
    p.add_argument("--component", metavar="JSON", action="append", dest="components_json",
                   help="Add an optional component (same format as 'repack --component')")
    p.set_defaults(func=_cmd_new_repack_project)


def _cmd_new_repack_project(args):
    from src.core.repack_project import RepackSettings, save as save_repack

    s = RepackSettings()
    _apply_truthy(s, args, _NEW_REPACK_PROJECT_FIELDS)
    _apply_optional(s, args, ("close_delay", "required_free_space_gb"))
    if args.no_uninstaller:                  s.include_uninstaller       = False
    if args.no_verify_crc32:                 s.verify_crc32              = False
    if args.shortcut_desktop is not None:    s.shortcut_create_desktop   = args.shortcut_desktop
    if args.shortcut_startmenu is not None:  s.shortcut_create_startmenu = args.shortcut_startmenu
    if args.components_json:
        parsed = []
        for raw in args.components_json:
            try:
                c = json.loads(raw)
            except json.JSONDecodeError as exc:
                _die(f"Invalid --component JSON: {exc}\n  value: {raw}", EXIT_INPUT)
            if "label" not in c or "folder" not in c:
                _die(f"--component JSON must have 'label' and 'folder' keys: {raw}", EXIT_INPUT)
            parsed.append({
                "label":           str(c["label"]),
                "folder":          str(c["folder"]),
                "default_checked": bool(c.get("default_checked", True)),
                "group":           str(c.get("group", "")),
                "shortcut_target": str(c.get("shortcut_target", "")),
            })
        s.components = parsed

    out = Path(args.output)
    save_repack(s, out)
    print(f"Repack project created: {out}")
    print(f"  compression: {s.compression}")
    print(f"  threads:     {s.threads}")
    print(f"  arch:        {s.arch}")


# ---------------------------------------------------------------------------
# show-repack-project
# ---------------------------------------------------------------------------

def _add_show_repack_project(sub):
    p = sub.add_parser("show-repack-project",
                       help="Display settings from a .xpr repack project file")
    p.add_argument("project", metavar="FILE", help="Path to .xpr project file")
    p.set_defaults(func=_cmd_show_repack_project)


def _cmd_show_repack_project(args):
    from src.core.repack_project import load as load_repack
    from dataclasses import asdict

    try:
        s = load_repack(Path(args.project))
    except Exception as exc:
        _die(f"Failed to load project: {exc}", EXIT_INPUT)

    print(f"Repack project: {args.project}")
    d = asdict(s)
    components = d.pop("components", [])
    for key, val in d.items():
        if val is not None and val != "" and val != []:
            print(f"  {key:<28} {val}")
    if components:
        print(f"  {'components':<28} ({len(components)})")
        for i, c in enumerate(components):
            grp = f"  [group: {c['group']}]" if c.get("group") else ""
            chk = "checked" if c.get("default_checked", True) else "unchecked"
            print(f"    [{i + 1}] {c['label']}  ({c['folder']})  {chk}{grp}")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

# Exit-code conventions for callers driving the CLI from scripts:
#   1 — generic / unspecified runtime error
#   2 — argparse uses this for command-line usage errors (untouched)
#   3 — input error: missing file/dir, malformed project, malformed
#       --component JSON, or any failure while reading user-supplied data
#   4 — build error: engine ran but produced an error or non-zero output
EXIT_GENERIC = 1
EXIT_INPUT   = 3
EXIT_BUILD   = 4


def _die(msg: str, code: int = EXIT_GENERIC):
    print(f"error: {msg}", file=sys.stderr)
    sys.exit(code)


def _warn(msg: str):
    print(f"warning: {msg}", file=sys.stderr)


def _apply_truthy(settings, args, names):
    """For each name, copy args.<name> → settings.<name> when args.<name> is
    truthy. Names are listed once instead of one `if` line per field; values
    that should override on falsy (0, False, '') belong in _apply_optional."""
    for name in names:
        v = getattr(args, name, None)
        if v:
            setattr(settings, name, v)


def _apply_optional(settings, args, names):
    """Same as _apply_truthy but tests `is not None` so 0 / False / '' can
    override existing project values where those are meaningful."""
    for name in names:
        v = getattr(args, name, None)
        if v is not None:
            setattr(settings, name, v)


def _apply_renamed(settings, args, mapping):
    """For each (arg_attr, settings_attr), copy when the arg is truthy.
    Use this when the CLI flag dest differs from the settings field name."""
    for arg_attr, settings_attr in mapping:
        v = getattr(args, arg_attr, None)
        if v:
            setattr(settings, settings_attr, v)


def _emit_validation_errors(errors: list[str], json_mode: bool) -> None:
    """Print validation errors and exit EXIT_INPUT.  In --json mode the
    errors come out as a single object on stdout; otherwise each error is
    written to stderr with the standard `error: ` prefix."""
    if json_mode:
        print(json.dumps({"success": False, "errors": errors}))
    else:
        for e in errors:
            print(f"error: {e}", file=sys.stderr)
    sys.exit(EXIT_INPUT)


def _validate_build_inputs(settings) -> list[str]:
    """Front-loaded path/field validation for `build` (and `build --check`).
    Returns a list of human-readable error messages; empty means OK.
    Mirrors the early checks in core.patch_builder.build()."""
    errors: list[str] = []
    if not settings.app_name.strip():
        errors.append("App name is required")
    if not settings.source_dir or not Path(settings.source_dir).is_dir():
        errors.append(f"Source directory not found or not a directory: {settings.source_dir!r}")
    if not settings.target_dir or not Path(settings.target_dir).is_dir():
        errors.append(f"Target directory not found or not a directory: {settings.target_dir!r}")
    if settings.icon_path and not Path(settings.icon_path).is_file():
        errors.append(f"Icon file not found: {settings.icon_path}")
    if settings.backdrop_path and not Path(settings.backdrop_path).is_file():
        errors.append(f"Backdrop file not found: {settings.backdrop_path}")
    for ef in (settings.extra_files or []):
        src = ef.get("src", "")
        if not src or not Path(src).exists():
            errors.append(f"Extra file source not found: {src!r}")
    if settings.arch not in ("x64", "x86"):
        errors.append(f"Invalid architecture: {settings.arch!r} (expected x64 or x86)")
    return errors


def _validate_repack_inputs(settings) -> list[str]:
    """Front-loaded validation for `repack` (and `repack --check`)."""
    errors: list[str] = []
    if not settings.app_name.strip():
        errors.append("App name is required")
    if not settings.game_dir or not Path(settings.game_dir).is_dir():
        errors.append(f"Game directory not found or not a directory: {settings.game_dir!r}")
    if settings.icon_path and not Path(settings.icon_path).is_file():
        errors.append(f"Icon file not found: {settings.icon_path}")
    if settings.backdrop_path and not Path(settings.backdrop_path).is_file():
        errors.append(f"Backdrop file not found: {settings.backdrop_path}")
    for i, c in enumerate(settings.components or []):
        folder = c.get("folder", "")
        if not folder or not Path(folder).is_dir():
            errors.append(f"Component {i + 1} folder not found: {folder!r}")
    if settings.codec not in ("lzma", "zstd"):
        errors.append(f"Invalid codec: {settings.codec!r} (expected lzma or zstd)")
    if settings.arch not in ("x64", "x86"):
        errors.append(f"Invalid architecture: {settings.arch!r} (expected x64 or x86)")
    if settings.threads < 1 or settings.threads > 256:
        errors.append(f"Invalid thread count: {settings.threads} (must be 1–256)")
    if settings.max_part_size_mb < 0:
        errors.append(f"max_part_size_mb must be ≥ 0, got {settings.max_part_size_mb}")
    return errors


# Flag/field name lists for _apply_truthy. Booleans with paired --flag /
# --no-flag args are handled inline in their command functions, since the
# semantics need is-not-None checks.

_BUILD_FIELDS = (
    "source_dir", "target_dir", "output_dir",
    "app_name", "app_note", "version", "description", "copyright",
    "contact", "company_info", "window_title",
    "patch_exe_name", "patch_exe_version",
    "engine", "compression", "threads", "compressor_quality",
    "verify_method", "find_method",
    "registry_key", "registry_value",
    "ini_path", "ini_section", "ini_key",
    "arch", "icon_path", "extra_diff_args",
    "run_on_startup", "run_before", "run_after", "run_on_finish",
    "backup_at", "backup_path", "backdrop_path",
)

_NEW_PROJECT_FIELDS = (
    "app_name", "app_note", "version", "description", "copyright",
    "contact", "company_info", "window_title",
    "patch_exe_name", "patch_exe_version",
    "engine", "compression", "threads", "compressor_quality",
    "verify_method", "arch", "icon_path", "extra_diff_args",
    "run_before", "run_after",
    "backup_at", "backup_path", "backdrop_path",
)

_REPACK_FIELDS = (
    "game_dir", "output_dir",
    "app_name", "app_note", "version", "description", "copyright",
    "contact", "company_info", "window_title",
    "installer_exe_name", "installer_exe_version",
    "codec", "compression", "threads", "arch",
    "icon_path", "backdrop_path",
    "install_registry_key", "run_after_install", "detect_running_exe",
    "shortcut_target", "shortcut_name",
)

_NEW_REPACK_PROJECT_FIELDS = (
    "app_name", "app_note", "version", "description", "copyright",
    "contact", "company_info", "window_title",
    "installer_exe_name", "installer_exe_version",
    "game_dir", "output_dir",
    "codec", "compression", "threads", "arch",
    "icon_path", "backdrop_path",
    "install_registry_key", "run_after_install", "detect_running_exe",
    "shortcut_target", "shortcut_name",
)


# ---------------------------------------------------------------------------
# archive (Steam depot downloader — Phase 1 minimal slice)
# ---------------------------------------------------------------------------

def _add_archive(sub):
    p = sub.add_parser(
        "archive",
        help="Steam depot downloader (requires `pip install patchforge[archive]`)",
    )
    asub = p.add_subparsers(dest="archive_cmd", metavar="SUBCOMMAND")

    p_login = asub.add_parser("login", help="Log in to Steam and save refresh tokens")
    p_login.set_defaults(func=_cmd_archive_login)

    p_logout = asub.add_parser("logout", help="Delete saved Steam login tokens")
    p_logout.add_argument("--all", action="store_true",
                          help="Also delete the saved Web API key")
    p_logout.set_defaults(func=_cmd_archive_logout)

    p_check = asub.add_parser("check", help="Verify saved tokens still log in to Steam")
    p_check.set_defaults(func=_cmd_archive_check)

    p_info = asub.add_parser("info", help="Print product info for one or more app IDs")
    p_info.add_argument("app_ids", metavar="APPID", nargs="+", type=int,
                        help="Steam app IDs")
    p_info.set_defaults(func=_cmd_archive_info)

    p_dl = asub.add_parser("download",
                           help="Download depots for one or more app IDs into 7z archives")
    p_dl.add_argument("app_ids", metavar="APPID", nargs="*", type=int, default=[],
                      help="Steam app IDs (or omit and pass --project / --appid-file)")
    p_dl.add_argument("--output-dir", metavar="DIR",
                      help="Where the resulting .7z archives are written. "
                           "Falls back to project.output_dir then to current dir.")
    p_dl.add_argument("--platform", default="windows",
                      choices=["windows", "linux", "macos", "all"],
                      help="Platform filter (default: windows)")
    p_dl.add_argument("--workers", type=int, default=8, metavar="N",
                      help="Parallel CDN connections per depot (default: 8)")
    p_dl.add_argument("--branch", default="public", metavar="NAME",
                      help="Branch to download (default: public)")
    p_dl.add_argument("--branch-password", default=None, metavar="PASS",
                      dest="branch_password",
                      help="Password for restricted branches")
    p_dl.add_argument("--compression", type=int, default=9, metavar="0..9",
                      help="7z compression level (default: 9)")
    p_dl.add_argument("--archive-password", default=None, metavar="PASS",
                      dest="archive_password",
                      help="Password to encrypt the resulting 7z archive")
    p_dl.add_argument("--volume-size", default=None, metavar="SIZE",
                      dest="volume_size",
                      help="Split archive into volumes (e.g. 4g, 700m, 1024k)")
    p_dl.add_argument("--language", default="english", metavar="LANG",
                      help="Language depot filter (default: english)")
    p_dl.add_argument("--max-retries", type=int, default=1, metavar="N",
                      dest="max_retries",
                      help="Retries on CM/manifest timeouts (default: 1)")
    p_dl.add_argument("--project", metavar="FILE",
                      help="Pull app IDs and overrides from a .xarchive project")
    p_dl.add_argument("--appid-file", metavar="FILE", dest="appid_file",
                      help="Read app IDs from FILE (one per line, comma-separated OK)")
    p_dl.add_argument("--no-progress", action="store_true", dest="no_progress",
                      help="Plain log mode instead of tqdm progress bars")
    p_dl.add_argument("--crack", choices=["coldclient", "gse"], default=None,
                      help="(Phase 3 — not yet implemented; raises an error today)")
    p_dl.set_defaults(func=_cmd_archive_download)

    p_new = asub.add_parser("new-project",
                            help="Create a new .xarchive project file with default settings")
    p_new.add_argument("--output", metavar="FILE", required=True,
                       help="Path to write the .xarchive project file")
    p_new.add_argument("--name", metavar="NAME", help="Project name")
    p_new.add_argument("--app-id", metavar="APPID", action="append", type=int, default=[],
                       dest="app_ids", help="Add an app ID (repeatable)")
    p_new.set_defaults(func=_cmd_archive_new_project)

    p_show = asub.add_parser("show-project", help="Display settings from a .xarchive file")
    p_show.add_argument("project", metavar="FILE", help="Path to .xarchive file")
    p_show.set_defaults(func=_cmd_archive_show_project)

    p.set_defaults(func=lambda args: p.print_help())


def _archive_require_extras_or_die():
    from src.core.archive._extras import missing_extras
    missing = missing_extras()
    if missing:
        _die(
            "archive-mode requires optional dependencies: "
            + ", ".join(missing)
            + "\n  Install with: pip install patchforge[archive]",
            EXIT_INPUT,
        )


def _cmd_archive_login(args):
    _archive_require_extras_or_die()
    from src.core.archive.auth import fresh_login
    from src.core.archive import credentials as creds_mod

    print("Starting Steam login...")
    tokens = fresh_login()
    creds = creds_mod.load()
    creds.username             = tokens["username"]
    creds.steam_id             = int(tokens["steam_id"])
    creds.client_refresh_token = tokens["client_refresh_token"]
    creds_mod.save(creds)
    print(f"Logged in as {tokens['username']!r}  ·  SteamID {tokens['steam_id']}")
    print(f"Tokens saved to {creds_mod.credentials_path()}")


def _cmd_archive_logout(args):
    from src.core.archive import credentials as creds_mod

    if args.all:
        creds_mod.clear_all()
        print(f"All credentials deleted ({creds_mod.credentials_path()}).")
    else:
        creds_mod.clear_login_tokens()
        print("Login tokens cleared.  Web API key (if any) preserved.")


def _cmd_archive_check(args):
    _archive_require_extras_or_die()
    from src.core.archive import credentials as creds_mod
    from src.core.archive.appinfo import login as cm_login

    creds = creds_mod.load()
    if not creds.has_login_tokens():
        _die("No saved tokens.  Run `patchforge archive login` first.", EXIT_INPUT)

    text, _ = creds_mod.refresh_token_expiry_text(creds.client_refresh_token)
    if text:
        print(text)

    tokens = {
        "username":             creds.username,
        "steam_id":             creds.steam_id,
        "client_refresh_token": creds.client_refresh_token,
    }
    try:
        client, _cdn = cm_login(tokens)
    except Exception as exc:
        _die(f"CM login failed: {exc}", EXIT_INPUT)

    name = client.user.name if client.user else "?"
    print(f"OK — logged in as {name!r}  ·  SteamID {client.steam_id}")
    client.logout()


def _cmd_archive_info(args):
    _archive_require_extras_or_die()
    from src.core.archive import credentials as creds_mod
    from src.core.archive.appinfo import login as cm_login, query_app_info_batch

    creds = creds_mod.load()
    if not creds.has_login_tokens():
        _die("No saved tokens.  Run `patchforge archive login` first.", EXIT_INPUT)

    tokens = {
        "username":             creds.username,
        "steam_id":             creds.steam_id,
        "client_refresh_token": creds.client_refresh_token,
    }
    try:
        client, cdn = cm_login(tokens)
    except Exception as exc:
        _die(f"CM login failed: {exc}", EXIT_INPUT)

    try:
        for app_id, info in query_app_info_batch(client, cdn, list(args.app_ids)):
            if info is None:
                _warn(f"app {app_id}: no info returned")
    finally:
        client.logout()


def _cmd_archive_new_project(args):
    from src.core.archive.project import (
        AppEntry, new_project, save as save_proj,
    )

    proj = new_project(name=args.name or "")
    for app_id in args.app_ids or []:
        proj.apps.append(AppEntry(app_id=int(app_id)))

    out = Path(args.output)
    save_proj(proj, out)
    print(f"Archive project created: {out}")
    print(f"  apps:        {len(proj.apps)}")
    print(f"  default_platform: {proj.default_platform}")


def _cmd_archive_show_project(args):
    from dataclasses import asdict
    from src.core.archive.project import load as load_proj

    try:
        proj = load_proj(Path(args.project))
    except Exception as exc:
        _die(f"Failed to load archive project: {exc}", EXIT_INPUT)

    d = asdict(proj)
    apps  = d.pop("apps", [])
    crack = d.pop("crack", {})
    bbcode_template = d.pop("bbcode_template", "")
    print(f"Archive project: {args.project}")
    for key, val in d.items():
        if val is None or val == "" or val == []:
            continue
        print(f"  {key:<20} {val}")
    if apps:
        print(f"  {'apps':<20} ({len(apps)})")
        for a in apps:
            line = f"    {a.get('app_id')}"
            extras = []
            if a.get("branch") and a["branch"] != "public":
                extras.append(f"branch={a['branch']}")
            if a.get("platform"):
                extras.append(f"platform={a['platform']}")
            if a.get("current_buildid"):
                extras.append(f"buildid={a['current_buildid']}")
            if extras:
                line += "  [" + ", ".join(extras) + "]"
            print(line)
    if any(crack.values()):
        print(f"  {'crack':<20}")
        for k, v in crack.items():
            if v:
                print(f"    {k:<20} {v}")
    if bbcode_template:
        line_count = bbcode_template.count("\n") + 1
        print(f"  {'bbcode_template':<20} ({line_count} lines)")


# ---------------------------------------------------------------------------
# archive download (Phase 2)
# ---------------------------------------------------------------------------

def _resolve_download_app_ids(args) -> list[int]:
    """Collect app IDs from positional args, --appid-file, and/or --project."""
    app_ids: list[int] = list(args.app_ids or [])

    if args.appid_file:
        appid_path = Path(args.appid_file)
        if not appid_path.is_file():
            _die(f"--appid-file not found: {appid_path}", EXIT_INPUT)
        text = appid_path.read_text(encoding="utf-8")
        for token in text.replace(",", "\n").split("\n"):
            token = token.strip()
            if not token or token.startswith("#"):
                continue
            try:
                app_ids.append(int(token))
            except ValueError:
                _die(f"--appid-file contains non-integer: {token!r}", EXIT_INPUT)

    if args.project:
        from src.core.archive.project import load as load_proj
        proj_path = Path(args.project)
        if not proj_path.is_file():
            _die(f"--project not found: {proj_path}", EXIT_INPUT)
        try:
            proj = load_proj(proj_path)
        except Exception as exc:
            _die(f"Failed to load archive project: {exc}", EXIT_INPUT)
        for entry in proj.apps:
            if entry.app_id:
                app_ids.append(int(entry.app_id))

    # Dedupe while preserving order.
    seen: set[int] = set()
    deduped: list[int] = []
    for aid in app_ids:
        if aid not in seen:
            seen.add(aid)
            deduped.append(aid)
    return deduped


def _resolve_output_dir(args) -> Path:
    """Resolve --output-dir, falling back to project.output_dir, then cwd."""
    if args.output_dir:
        return Path(args.output_dir).resolve()

    if args.project:
        from src.core.archive.project import load as load_proj
        try:
            proj = load_proj(Path(args.project))
        except Exception:
            proj = None
        if proj and proj.output_dir:
            return Path(proj.output_dir).resolve()

    return Path.cwd().resolve()


_PLATFORM_LABELS = {"windows": "Windows", "linux": "Linux", "macos": "macOS"}


def _platform_from_archive_stem(stem: str) -> str | None:
    """Pull the platform key out of an archive stem.

    Stems are built by compress.py as `<game>.<buildid>.<platform>.<branch>`.
    We look for any known platform key in the dot-separated parts (the
    second-to-last is the convention but a sanitised game name with dots
    can shift positions, so just scan)."""
    for part in stem.split("."):
        if part.lower() in _PLATFORM_LABELS:
            return part.lower()
    return None


def _archive_run_post_pipeline(archives, app_meta, previous_buildid, creds,
                                *, upload_mod, notify_mod,
                                output_dir, subscriber) -> None:
    """Upload archives and fire build-change notifications for one app run.

    No-ops when the corresponding credential blocks are blank.  Shared
    across `archive download` and (Phase 5) the polling loop."""
    if not archives or not app_meta:
        return

    # ---- upload --------------------------------------------------------
    stem_to_url: dict[str, str] = {}
    if creds.multiup.is_set():
        try:
            stem_to_url = upload_mod.upload_archives(
                archives,
                username=creds.multiup.username or None,
                password=creds.multiup.password or None,
                description=str(app_meta.get("name", "")) or None,
                links_dir=output_dir,
                bin_url=creds.privatebin.url or None,
                bin_pass=creds.privatebin.password or None,
                on_event=subscriber,
            )
        except Exception as exc:
            _warn(f"Upload failed for app {app_meta.get('appid')}: {exc}")

    # Build platform -> url map for notify + any future bbcode rendering.
    platform_links: dict[str, str] = {}
    for stem, url in stem_to_url.items():
        plat = _platform_from_archive_stem(stem)
        if plat:
            platform_links[plat] = url

    # ---- notify --------------------------------------------------------
    if not (creds.discord.is_set() or creds.telegram.is_set()):
        return
    notify_data = {
        "appid":            app_meta.get("appid"),
        "name":             app_meta.get("name", ""),
        "previous_buildid": previous_buildid or "",
        "current_buildid":  app_meta.get("buildid", ""),
        "timeupdated":      app_meta.get("timeupdated", 0),
    }
    if creds.discord.is_set():
        try:
            notify_mod.send_discord_notification(
                creds.discord.webhook_url,
                notify_data,
                mention_role_ids=creds.discord.mention_role_ids or None,
                upload_links=platform_links or None,
            )
        except Exception as exc:
            _warn(f"Discord notify failed: {exc}")
    if creds.telegram.is_set():
        try:
            notify_mod.send_telegram_notification(
                creds.telegram.token,
                creds.telegram.chat_ids,
                notify_data,
                upload_links=platform_links or None,
            )
        except Exception as exc:
            _warn(f"Telegram notify failed: {exc}")


def _cmd_archive_download(args):
    _archive_require_extras_or_die()

    from src.core.archive            import credentials   as creds_mod
    from src.core.archive            import depots_ini
    from src.core.archive            import notify        as notify_mod
    from src.core.archive            import project       as project_mod
    from src.core.archive            import upload        as upload_mod
    from src.core.archive.appinfo    import login as cm_login
    from src.core.archive.cli_progress import build_subscriber
    from src.core.archive.compress   import parse_size
    from src.core.archive.download   import download_app

    creds = creds_mod.load()
    if not creds.has_login_tokens():
        _die("No saved tokens.  Run `patchforge archive login` first.", EXIT_INPUT)

    app_ids = _resolve_download_app_ids(args)
    if not app_ids:
        _die("No app IDs provided — pass APPID positional, --appid-file, or --project",
             EXIT_INPUT)

    output_dir = _resolve_output_dir(args)
    output_dir.mkdir(parents=True, exist_ok=True)

    try:
        volume_size = parse_size(args.volume_size) if args.volume_size else None
    except ValueError:
        _die(f"Invalid --volume-size: {args.volume_size!r}", EXIT_INPUT)

    if args.compression < 0 or args.compression > 9:
        _die(f"--compression must be 0..9, got {args.compression}", EXIT_INPUT)

    depot_names = depots_ini.load()

    # Crack identity — loaded from the .xarchive when --project is given, or
    # a fresh CrackIdentity that prompts will fill in.  The crack functions
    # mutate it in place; we save back to .xarchive after the run so users
    # don't get re-prompted on every invocation.
    project_path = Path(args.project) if args.project else None
    project_obj = None
    crack_identity = None
    if args.crack:
        if project_path:
            try:
                project_obj = project_mod.load(project_path)
            except Exception as exc:
                _die(f"Failed to load archive project: {exc}", EXIT_INPUT)
            crack_identity = project_obj.crack
        else:
            crack_identity = project_mod.CrackIdentity()

    tokens = {
        "username":             creds.username,
        "steam_id":             creds.steam_id,
        "client_refresh_token": creds.client_refresh_token,
    }
    try:
        client, cdn = cm_login(tokens)
    except Exception as exc:
        _die(f"CM login failed: {exc}", EXIT_INPUT)

    subscriber = build_subscriber(plain=args.no_progress)
    all_archives: list[Path] = []
    all_unknown_depot_ids: set[str] = set()

    # Per-app entry lookup (for previous_buildid + post-run buildid persist).
    # Built lazily so non-project runs (CLI app-id positional) skip it.
    apps_by_id: dict[int, project_mod.AppEntry] = {}
    if project_obj is not None:
        for entry in project_obj.apps:
            apps_by_id[entry.app_id] = entry

    try:
        for app_id in app_ids:
            print(f"=== app {app_id} ===")
            entry = apps_by_id.get(app_id)
            previous_buildid = entry.current_buildid if entry else ""
            try:
                archives, platform_manifests, app_meta = download_app(
                    client, cdn, app_id, output_dir,
                    platform=args.platform, workers=args.workers,
                    password=args.archive_password,
                    compression_level=args.compression,
                    volume_size=volume_size,
                    branch=args.branch,
                    crack=args.crack,
                    depot_names=depot_names,
                    max_retries=args.max_retries,
                    language=args.language,
                    crack_identity=crack_identity,
                    on_event=subscriber,
                )
            except NotImplementedError as exc:
                _die(str(exc), EXIT_INPUT)
            except Exception as exc:
                _warn(f"app {app_id} failed: {exc}")
                continue
            all_archives.extend(archives)
            for plat_records in platform_manifests.values():
                for depot_id, depot_name, _gid in plat_records:
                    if not depot_name:
                        all_unknown_depot_ids.add(str(depot_id))

            _archive_run_post_pipeline(
                archives, app_meta, previous_buildid, creds,
                upload_mod=upload_mod, notify_mod=notify_mod,
                output_dir=output_dir, subscriber=subscriber,
            )

            # Persist current buildid so the next polling run can detect change.
            if entry is not None and app_meta.get("buildid"):
                entry.current_buildid = str(app_meta["buildid"])
    finally:
        subscriber.close() if hasattr(subscriber, "close") else None
        try:
            client.logout()
        except Exception:
            pass

    if all_unknown_depot_ids:
        added = depots_ini.record_unknown(sorted(all_unknown_depot_ids))
        if added:
            print(f"Added {len(added)} unknown depot ID(s) to {depots_ini.depots_path()}")

    # Persist any crack-identity fields that prompts filled in back into the
    # project so subsequent runs don't re-ask for the same values.
    if args.crack and project_obj is not None and project_path is not None:
        project_obj.crack = crack_identity
        try:
            project_mod.save(project_obj, project_path)
            print(f"Updated crack identity in {project_path}")
        except Exception as exc:
            _warn(f"Could not persist crack identity to {project_path}: {exc}")

    print()
    print(f"Done.  {len(all_archives)} archive file(s) written to {output_dir}")
    for a in all_archives:
        print(f"  {a.name}")


