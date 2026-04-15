"""PatchForge CLI — mirrors all GUI options."""

import argparse
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

    return parser


_EPILOG = """\
Examples:
  # Build a patch directly
  patchforge build --source v1.0.exe --target v1.1.exe --app-name "My Game" --version 1.1

  # Create a project file, then build from it
  patchforge new-project --output patch.xpm
  patchforge build --project patch.xpm --source v1.0.exe --target v1.1.exe

  # Show a saved project
  patchforge show-project patch.xpm
"""


# ---------------------------------------------------------------------------
# build
# ---------------------------------------------------------------------------

def _add_build(sub):
    p = sub.add_parser("build", help="Generate a patch executable")

    # Project file (optional — individual flags override it)
    p.add_argument("--project", metavar="FILE",
                   help="Load settings from a .xpm project file (flags override)")

    # File paths
    p.add_argument("--source", metavar="FILE", help="Original (old) file")
    p.add_argument("--target", metavar="FILE", help="Patched (new) file")
    p.add_argument("--output-dir", metavar="DIR",
                   help="Directory to write the output .exe (default: same as source)")

    # Metadata
    p.add_argument("--app-name", metavar="NAME", help="Application name shown in patcher")
    p.add_argument("--version",  metavar="VER",  help="Version string (e.g. 1.2.3)")
    p.add_argument("--description", metavar="TEXT", help="Description shown in patcher")

    # Engine + compression
    p.add_argument("--engine", choices=["hdiffpatch", "xdelta3", "jojodiff"],
                   help="Patch engine (default: hdiffpatch)")
    p.add_argument("--compression",
                   choices=["none", "zip/1", "zip/9", "bzip/5", "bzip/9",
                             "lzma/fast", "lzma/normal", "lzma/ultra", "lzma/ultra64"],
                   help="Compression level (default: lzma/ultra)")

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

    # Save project after build
    p.add_argument("--save-project", metavar="FILE",
                   help="Save resolved settings to a .xpm project file after building")

    p.set_defaults(func=_cmd_build)


def _cmd_build(args):
    from src.core.project import ProjectSettings, load, save
    from src.core.patch_builder import build
    from src.core.compression import requires_full_stub

    # Start with defaults or loaded project
    if args.project:
        try:
            settings = load(Path(args.project))
        except Exception as exc:
            _die(f"Failed to load project '{args.project}': {exc}")
    else:
        settings = ProjectSettings()

    # Apply flag overrides
    if args.source:        settings.source_file  = args.source
    if args.target:        settings.target_file  = args.target
    if args.output_dir:    settings.output_dir   = args.output_dir
    if args.app_name:      settings.app_name     = args.app_name
    if args.version:       settings.version      = args.version
    if args.description:   settings.description  = args.description
    if args.engine:        settings.engine       = args.engine
    if args.compression:   settings.compression  = args.compression
    if args.verify_method: settings.verify_method = args.verify_method
    if args.find_method:   settings.find_method  = args.find_method
    if args.registry_key:  settings.registry_key = args.registry_key
    if args.registry_value: settings.registry_value = args.registry_value
    if args.ini_path:      settings.ini_path     = args.ini_path
    if args.ini_section:   settings.ini_section  = args.ini_section
    if args.ini_key:       settings.ini_key      = args.ini_key
    if args.arch:          settings.arch         = args.arch

    # Warn about stub limitations
    if settings.engine == "hdiffpatch" and requires_full_stub(settings.compression):
        _warn(f"Compression '{settings.compression}' requires the full HDiffPatch stub "
              f"(hdiffpatch_full_{settings.arch}.exe). "
              f"Run 'make full' in stub/ if you haven't already.")

    if settings.engine == "jojodiff" and settings.compression != "none":
        _die("JojoDiff does not support compression. Use --compression none.")

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
    print(f"Orig {settings.verify_method.upper()}: {result.orig_checksum}")
    print(f"New  {settings.verify_method.upper()}: {result.new_checksum}")


# ---------------------------------------------------------------------------
# new-project
# ---------------------------------------------------------------------------

def _add_new_project(sub):
    p = sub.add_parser("new-project",
                       help="Create a new project file with default settings")
    p.add_argument("--output", metavar="FILE", required=True,
                   help="Path to write the .xpm project file")
    p.add_argument("--app-name",    metavar="NAME")
    p.add_argument("--version",     metavar="VER")
    p.add_argument("--description", metavar="TEXT")
    p.add_argument("--engine",      choices=["hdiffpatch", "xdelta3", "jojodiff"])
    p.add_argument("--compression",
                   choices=["none", "zip/1", "zip/9", "bzip/5", "bzip/9",
                             "lzma/fast", "lzma/normal", "lzma/ultra", "lzma/ultra64"])
    p.add_argument("--verify",      choices=["crc32c", "md5", "filesize"],
                   dest="verify_method")
    p.add_argument("--arch",        choices=["x64", "x86"])
    p.set_defaults(func=_cmd_new_project)


def _cmd_new_project(args):
    from src.core.project import ProjectSettings, save

    s = ProjectSettings()
    if args.app_name:      s.app_name     = args.app_name
    if args.version:       s.version      = args.version
    if args.description:   s.description  = args.description
    if args.engine:        s.engine       = args.engine
    if args.compression:   s.compression  = args.compression
    if args.verify_method: s.verify_method = args.verify_method
    if args.arch:          s.arch         = args.arch

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
        if val:
            print(f"  {key:<20} {val}")


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
