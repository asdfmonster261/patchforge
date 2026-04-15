"""PatchForge — video game binary patch maker.

Entry point: launches CLI if arguments are given, GUI otherwise.
"""

import sys


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
