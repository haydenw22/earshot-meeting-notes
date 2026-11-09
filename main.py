"""Entry point for the Earshot desktop app.

Run with:  python main.py
"""
import earshot_boot

if __name__ == "__main__":
    # Packaged macOS builds may run a newer, signature-verified code update
    # from Application Support (see earshot_boot). It has to be switched on
    # before anything imports meeting_notes.
    earshot_boot.activate_code_update()
    from meeting_notes.app import run

    raise SystemExit(run())
