"""Global kill switch for Poe orchestration loops.

A sentinel file at memory/STOP engages the kill switch. Any running loop
checks this file at each step boundary and exits cleanly if present.
New loops refuse to start while the sentinel exists.

Usage:
    from killswitch import is_active, engage, clear, status

    # In a loop:
    if is_active():
        break  # stop gracefully

CLI:
    poe-stop              # engage + post STOP interrupt to any running loop
    poe-stop --clear      # clear the sentinel, allow loops to run again
    poe-stop --status     # show current state
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

log = logging.getLogger("poe.killswitch")

_SENTINEL_NAME = "STOP"


def _sentinel_path() -> Path:
    try:
        from orch_items import memory_dir as _md
        return _md() / _SENTINEL_NAME
    except Exception:
        return Path("memory") / _SENTINEL_NAME


def is_active() -> bool:
    """Return True if the kill switch sentinel file exists."""
    try:
        return _sentinel_path().exists()
    except Exception:
        return False


def engage(reason: str = "kill switch engaged") -> Path:
    """Write the sentinel file. All running loops will stop at their next step."""
    path = _sentinel_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(reason.strip() + "\n", encoding="utf-8")
    log.warning("kill switch engaged: %s", path)
    return path


def clear() -> None:
    """Remove the sentinel file to allow loops to run again."""
    path = _sentinel_path()
    path.unlink(missing_ok=True)
    log.info("kill switch cleared: %s", path)


def read_reason() -> str:
    """Return the reason written to the sentinel, or '' if not active."""
    try:
        path = _sentinel_path()
        if path.exists():
            return path.read_text(encoding="utf-8").strip()
    except Exception:
        pass
    return ""


def status() -> dict:
    """Return a dict describing current kill switch + running loop state."""
    active = is_active()
    result: dict = {
        "active": active,
        "sentinel_path": str(_sentinel_path()),
    }
    if active:
        result["reason"] = read_reason()

    # Check for a running loop via the interrupt module's lock
    try:
        from interrupt import get_running_loop
        running = get_running_loop()
        result["running_loop"] = running
    except Exception:
        result["running_loop"] = None

    return result


def post_stop_interrupt(source: str = "killswitch") -> bool:
    """Post a STOP interrupt to any currently running loop. Returns True if posted."""
    try:
        from interrupt import InterruptQueue
        q = InterruptQueue()
        q.post("stop immediately — kill switch engaged", source=source)
        log.info("STOP interrupt posted from killswitch")
        return True
    except Exception as exc:
        log.warning("could not post STOP interrupt: %s", exc)
        return False


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def main(argv: Optional[list] = None) -> int:
    import sys
    import argparse

    parser = argparse.ArgumentParser(
        prog="poe-stop",
        description="Engage or clear the Poe orchestration kill switch.",
    )
    sub = parser.add_subparsers(dest="cmd")

    p_engage = sub.add_parser("engage", help="Engage kill switch (default action)")
    p_engage.add_argument("--reason", default="manual poe-stop", help="Reason to record in sentinel")
    p_engage.add_argument("--no-interrupt", action="store_true", help="Write sentinel only; skip posting interrupt")

    sub.add_parser("clear", help="Clear kill switch, allow loops to run")
    sub.add_parser("status", help="Show kill switch and running loop state")

    args = parser.parse_args(argv if argv is not None else sys.argv[1:])

    # Default action with no subcommand: engage
    cmd = args.cmd or "engage"
    reason = getattr(args, "reason", "manual poe-stop")
    no_interrupt = getattr(args, "no_interrupt", False)

    if cmd == "engage":
        path = engage(reason)
        print(f"kill switch engaged: {path}")
        print(f"reason: {reason}")
        if not no_interrupt:
            posted = post_stop_interrupt()
            print(f"interrupt posted: {posted}")
        print("Run 'poe-stop clear' to re-enable loops.")
        return 0

    if cmd == "clear":
        clear()
        print("kill switch cleared — loops may run again")
        return 0

    if cmd == "status":
        import json
        s = status()
        print(json.dumps(s, indent=2))
        return 0

    parser.print_help()
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
