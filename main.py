"""PatchForge — video game binary patch maker.

Entry point: launches CLI if arguments are given, GUI otherwise.
"""

# Archive-mode requires gevent's socket/ssl monkey-patches applied BEFORE
# anything else imports urllib3 / ssl / requests.  Patching afterwards
# leaves stale ssl references in already-imported modules, causing
# RecursionError on first network IO under the patched stack.  Run this
# unconditionally so both CLI and GUI startup paths are covered; no-op
# when steam[client] isn't installed.
try:
    import steam.monkey  # type: ignore
    steam.monkey.patch_minimal()
except ImportError:
    pass

import sys  # noqa: E402


def main():
    # If any non-empty args are passed (excluding the script name), use CLI
    if len(sys.argv) > 1:
        from src.cli.main import run_cli
        run_cli()
    else:
        from src.gui.main_window import run_gui
        run_gui()


if __name__ == "__main__":
    main()
