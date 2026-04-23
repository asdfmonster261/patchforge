"""PatchForge CLI — mirrors all GUI options."""

import argparse
import json
import sys
from pathlib import Path


def run_cli():
    parser = _build_parser()
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

    p.set_defaults(func=_cmd_build)


def _cmd_build(args):
    from src.core.project import ProjectSettings, load, save
    from src.core.patch_builder import build

    # Start with defaults or loaded project
    if args.project:
        try:
            settings = load(Path(args.project))
        except Exception as exc:
            _die(f"Failed to load project '{args.project}': {exc}")
    else:
        settings = ProjectSettings()

    # Apply flag overrides
    if args.source_dir:     settings.source_dir    = args.source_dir
    if args.target_dir:     settings.target_dir    = args.target_dir
    if args.output_dir:     settings.output_dir    = args.output_dir
    if args.app_name:       settings.app_name      = args.app_name
    if args.app_note:       settings.app_note      = args.app_note
    if args.version:        settings.version       = args.version
    if args.description:    settings.description   = args.description
    if args.copyright:      settings.copyright     = args.copyright
    if args.contact:        settings.contact       = args.contact
    if args.company_info:   settings.company_info  = args.company_info
    if args.window_title:   settings.window_title  = args.window_title
    if args.patch_exe_name:    settings.patch_exe_name    = args.patch_exe_name
    if args.patch_exe_version: settings.patch_exe_version = args.patch_exe_version
    if args.engine:         settings.engine        = args.engine
    if args.compression:    settings.compression   = args.compression
    if args.threads:              settings.threads            = args.threads
    if args.compressor_quality:   settings.compressor_quality = args.compressor_quality
    if args.verify_method:  settings.verify_method = args.verify_method
    if args.find_method:    settings.find_method   = args.find_method
    if args.registry_key:   settings.registry_key  = args.registry_key
    if args.registry_value: settings.registry_value = args.registry_value
    if args.ini_path:       settings.ini_path      = args.ini_path
    if args.ini_section:    settings.ini_section   = args.ini_section
    if args.ini_key:        settings.ini_key       = args.ini_key
    if args.arch:           settings.arch          = args.arch
    if args.icon_path:      settings.icon_path     = args.icon_path
    if args.extra_diff_args:    settings.extra_diff_args    = args.extra_diff_args
    if args.delete_extra_files is not None:
                            settings.delete_extra_files  = args.delete_extra_files
    if args.preserve_timestamps:
                            settings.preserve_timestamps = True
    if args.run_on_startup: settings.run_on_startup = args.run_on_startup
    if args.run_before:     settings.run_before     = args.run_before
    if args.run_after:      settings.run_after      = args.run_after
    if args.run_on_finish:  settings.run_on_finish  = args.run_on_finish
    if args.backup_at:      settings.backup_at      = args.backup_at
    if args.backup_path:    settings.backup_path    = args.backup_path
    if args.backdrop_path:  settings.backdrop_path  = args.backdrop_path
    if args.extra_files:
        parsed = []
        for entry in args.extra_files:
            if ":" in entry:
                src, dest = entry.split(":", 1)
            else:
                src, dest = entry, Path(entry).name
            parsed.append({"src": src.strip(), "dest": dest.strip()})
        settings.extra_files = parsed

    # Save project if requested
    if args.save_project:
        try:
            save(settings, Path(args.save_project))
            print(f"Project saved: {args.save_project}")
        except Exception as exc:
            _warn(f"Could not save project: {exc}")

    # Build
    def progress(pct: int, msg: str):
        bar_len = 30
        filled = int(bar_len * pct / 100)
        bar = "#" * filled + "-" * (bar_len - filled)
        print(f"\r[{bar}] {pct:3d}%  {msg:<45}", end="", flush=True)

    print(f"Building patch for '{settings.app_name}' using {settings.engine}...")
    result = build(settings, progress=progress)
    print()  # newline after progress bar

    if not result.success:
        _die(f"Build failed: {result.error}")

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
    if args.app_name:      s.app_name      = args.app_name
    if args.app_note:      s.app_note      = args.app_note
    if args.version:       s.version       = args.version
    if args.description:   s.description   = args.description
    if args.copyright:     s.copyright     = args.copyright
    if args.contact:       s.contact       = args.contact
    if args.company_info:  s.company_info  = args.company_info
    if args.window_title:  s.window_title  = args.window_title
    if args.patch_exe_name:    s.patch_exe_name    = args.patch_exe_name
    if args.patch_exe_version: s.patch_exe_version = args.patch_exe_version
    if args.engine:        s.engine        = args.engine
    if args.compression:   s.compression   = args.compression
    if args.threads:              s.threads            = args.threads
    if args.compressor_quality:   s.compressor_quality = args.compressor_quality
    if args.verify_method: s.verify_method = args.verify_method
    if args.arch:          s.arch          = args.arch
    if args.icon_path:     s.icon_path     = args.icon_path
    if args.extra_diff_args:   s.extra_diff_args   = args.extra_diff_args
    if args.run_before:        s.run_before        = args.run_before
    if args.run_after:         s.run_after         = args.run_after
    if args.backup_at:         s.backup_at         = args.backup_at
    if args.backup_path:       s.backup_path       = args.backup_path
    if args.backdrop_path:     s.backdrop_path     = args.backdrop_path

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
        _die(f"Failed to load project: {exc}")

    print(f"Project: {args.project}")
    for key, val in asdict(s).items():
        if val is not None and val != "" and val != []:
            print(f"  {key:<20} {val}")


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

    # Save project after build
    p.add_argument("--save-project", metavar="FILE",
                   help="Save resolved settings to a .xpr project file after building")

    p.set_defaults(func=_cmd_repack)


def _cmd_repack(args):
    from src.core.repack_project import RepackSettings, load as load_repack, save as save_repack
    from src.core.repack_builder import build as build_repack

    # Start from defaults or a loaded project
    if args.project:
        try:
            settings = load_repack(Path(args.project))
        except Exception as exc:
            _die(f"Failed to load project '{args.project}': {exc}")
    else:
        settings = RepackSettings()

    # Apply flag overrides
    if args.game_dir:              settings.game_dir              = args.game_dir
    if args.output_dir:            settings.output_dir            = args.output_dir
    if args.app_name:              settings.app_name              = args.app_name
    if args.app_note:              settings.app_note              = args.app_note
    if args.version:               settings.version               = args.version
    if args.description:           settings.description           = args.description
    if args.copyright:             settings.copyright             = args.copyright
    if args.contact:               settings.contact               = args.contact
    if args.company_info:          settings.company_info          = args.company_info
    if args.window_title:          settings.window_title          = args.window_title
    if args.installer_exe_name:    settings.installer_exe_name    = args.installer_exe_name
    if args.installer_exe_version: settings.installer_exe_version = args.installer_exe_version
    if args.codec:                 settings.codec                 = args.codec
    if args.compression:           settings.compression           = args.compression
    if args.threads:               settings.threads               = args.threads
    if args.arch:                  settings.arch                  = args.arch
    if args.icon_path:             settings.icon_path             = args.icon_path
    if args.backdrop_path:         settings.backdrop_path         = args.backdrop_path
    if args.install_registry_key:  settings.install_registry_key  = args.install_registry_key
    if args.run_after_install:     settings.run_after_install     = args.run_after_install
    if args.detect_running_exe:    settings.detect_running_exe    = args.detect_running_exe
    if args.close_delay is not None:            settings.close_delay            = args.close_delay
    if args.required_free_space_gb is not None: settings.required_free_space_gb = args.required_free_space_gb
    if args.no_uninstaller:                     settings.include_uninstaller    = False
    if args.no_verify_crc32:                    settings.verify_crc32           = False
    if args.split_bin:                          settings.split_bin              = True
    if args.shortcut_target:                    settings.shortcut_target        = args.shortcut_target
    if args.shortcut_name:                      settings.shortcut_name          = args.shortcut_name
    if args.shortcut_desktop is not None:       settings.shortcut_create_desktop   = args.shortcut_desktop
    if args.shortcut_startmenu is not None:     settings.shortcut_create_startmenu = args.shortcut_startmenu

    # Parse --component flags
    if args.components_json:
        parsed = []
        for raw in args.components_json:
            try:
                c = json.loads(raw)
            except json.JSONDecodeError as exc:
                _die(f"Invalid --component JSON: {exc}\n  value: {raw}")
            if "label" not in c or "folder" not in c:
                _die(f"--component JSON must have 'label' and 'folder' keys: {raw}")
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
            print(f"Project saved: {args.save_project}")
        except Exception as exc:
            _warn(f"Could not save project: {exc}")

    # Build
    def progress(pct: int, msg: str):
        bar_len = 30
        filled = int(bar_len * pct / 100)
        bar = "#" * filled + "-" * (bar_len - filled)
        print(f"\r[{bar}] {pct:3d}%  {msg:<55}", end="", flush=True)

    print(f"Building repack installer for '{settings.app_name}'...")
    result = build_repack(settings, progress=progress)
    print()  # newline after progress bar

    if not result.success:
        _die(f"Repack failed: {result.error}")

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
    if args.app_name:             s.app_name             = args.app_name
    if args.app_note:             s.app_note             = args.app_note
    if args.version:              s.version              = args.version
    if args.description:          s.description          = args.description
    if args.copyright:            s.copyright            = args.copyright
    if args.contact:              s.contact              = args.contact
    if args.company_info:         s.company_info         = args.company_info
    if args.window_title:         s.window_title         = args.window_title
    if args.installer_exe_name:   s.installer_exe_name   = args.installer_exe_name
    if args.installer_exe_version: s.installer_exe_version = args.installer_exe_version
    if args.game_dir:             s.game_dir             = args.game_dir
    if args.output_dir:           s.output_dir           = args.output_dir
    if args.codec:                s.codec                = args.codec
    if args.compression:          s.compression          = args.compression
    if args.threads:              s.threads              = args.threads
    if args.arch:                 s.arch                 = args.arch
    if args.icon_path:            s.icon_path            = args.icon_path
    if args.backdrop_path:        s.backdrop_path        = args.backdrop_path
    if args.install_registry_key: s.install_registry_key = args.install_registry_key
    if args.run_after_install:    s.run_after_install    = args.run_after_install
    if args.detect_running_exe:   s.detect_running_exe   = args.detect_running_exe
    if args.close_delay is not None:            s.close_delay            = args.close_delay
    if args.required_free_space_gb is not None: s.required_free_space_gb = args.required_free_space_gb
    if args.no_uninstaller:                     s.include_uninstaller    = False
    if args.no_verify_crc32:                    s.verify_crc32           = False
    if args.shortcut_target:                    s.shortcut_target        = args.shortcut_target
    if args.shortcut_name:                      s.shortcut_name          = args.shortcut_name
    if args.shortcut_desktop is not None:       s.shortcut_create_desktop   = args.shortcut_desktop
    if args.shortcut_startmenu is not None:     s.shortcut_create_startmenu = args.shortcut_startmenu
    if args.components_json:
        parsed = []
        for raw in args.components_json:
            try:
                c = json.loads(raw)
            except json.JSONDecodeError as exc:
                _die(f"Invalid --component JSON: {exc}\n  value: {raw}")
            if "label" not in c or "folder" not in c:
                _die(f"--component JSON must have 'label' and 'folder' keys: {raw}")
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
        _die(f"Failed to load project: {exc}")

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

def _die(msg: str):
    print(f"error: {msg}", file=sys.stderr)
    sys.exit(1)


def _warn(msg: str):
    print(f"warning: {msg}", file=sys.stderr)


def _fmt_size(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"
