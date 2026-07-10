"""Bootstrap maro-orchestration from a fresh machine.

Handles:
- Creating workspace directory structure
- Printing host-scheduler hook instructions for the heartbeat (Maro is an
  app, not a daemon — it never installs its own systemd/launchd/cron unit;
  supervision belongs to the host. Decided 2026-07-09.)
- Smoke-testing the install by running a manual NOW-lane request once

Entry points:
  maro-bootstrap install    -- workspace + scheduler instructions + smoke test
  maro-bootstrap dirs       -- create workspace dirs only
  maro-bootstrap services   -- print host-scheduler hook instructions
  maro-bootstrap status     -- show current workspace status
  maro-bootstrap smoke      -- run a live NOW-lane request and verify output
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import Optional

from config import workspace_root


# ---------------------------------------------------------------------------
# Directory structure
# ---------------------------------------------------------------------------

_WORKSPACE_SUBDIRS = [
    "memory",
    "skills",
    "projects",
    "output",
    "secrets",
    "logs",
]


def create_workspace_dirs(root: Optional[Path] = None) -> Path:
    """Create workspace directory structure. Returns workspace root."""
    ws = root or workspace_root()
    ws.mkdir(parents=True, exist_ok=True)
    for subdir in _WORKSPACE_SUBDIRS:
        (ws / subdir).mkdir(exist_ok=True)
    return ws


# ---------------------------------------------------------------------------
# Starter config
# ---------------------------------------------------------------------------

_STARTER_CONFIG = """\
# Maro user-level config (~/.maro/config.yml)
# Workspace-level overrides live in ~/.maro/workspace/config.yml (same keys,
# workspace wins). Every key, its default, and what flipping it does:
# docs/DEFAULTS.md in the repo. Everything below is OPTIONAL — sensible
# defaults apply while it stays commented out.

# --- LLM backend ------------------------------------------------------------
# Auto-detect order: anthropic (ANTHROPIC_API_KEY) -> subprocess (claude CLI)
# -> openrouter (OPENROUTER_API_KEY) -> openai (OPENAI_API_KEY). API keys are
# read from the environment, never from this file.
#model:
#  backend_order: [%(backend_order)s]

# --- Spend caps (defaults: $%(per_run).0f/run, $%(daily).0f/day; 0 = uncapped) ---------------------
#budget:
#  per_run_usd: %(per_run).1f
#  daily_usd: %(daily).1f

# --- Notifications / escalations ----------------------------------------------
# How Maro reaches you when a run finishes or needs a human. Without this,
# escalations land only in events.jsonl (inspect via `maro-runs status`).
# The command receives the run_card JSON on stdin.
#notify:
#  command: "maro-notify-telegram"   # or any shell command
#  events: [run_completed, escalation]

# --- Telegram (used by maro-notify-telegram and the listener) -----------------
# TELEGRAM_BOT_TOKEN comes from the environment; chat_id doubles as the
# allowlist of chats the listener may answer — never wildcard it.
#telegram:
#  chat_id: "123456789"
"""


def render_starter_config() -> str:
    """Starter config text with the REAL defaults interpolated.

    The values come from the code constants (loop_init caps, llm backend
    order) so the template a fresh install reads can never drift from what
    the code actually does.
    """
    from llm import DEFAULT_BACKEND_ORDER
    from loop_init import DEFAULT_PER_RUN_USD, DEFAULT_DAILY_USD
    return _STARTER_CONFIG % {
        "backend_order": ", ".join(DEFAULT_BACKEND_ORDER),
        "per_run": DEFAULT_PER_RUN_USD,
        "daily": DEFAULT_DAILY_USD,
    }


def write_starter_config() -> Optional[Path]:
    """Write a commented starter config to the user tier if none exists.

    Never overwrites: an existing ~/.maro/config.yml wins, even if empty.
    Returns the path written, or None if one already existed.
    """
    from config import config_paths
    cfg_path = Path(config_paths()["user"])
    if cfg_path.exists():
        return None
    cfg_path.parent.mkdir(parents=True, exist_ok=True)
    cfg_path.write_text(render_starter_config(), encoding="utf-8")
    return cfg_path


# ---------------------------------------------------------------------------
# Host-scheduler hook instructions
# ---------------------------------------------------------------------------
# History: bootstrap used to *generate* systemd/launchd units here. The
# generated heartbeat unit exec'd `sheriff.py --heartbeat` — a flag that never
# existed in any commit — so an enabled unit crash-looped every 30s from day
# one. Per the 2026-07-09 supervision decision ("app, not systemic": Maro
# ships entrypoints, the host owns scheduling; no self-rearming timers), the
# generator is gone entirely. The real one-shot entrypoint is `maro heartbeat`
# (cli.py `_cmd_heartbeat`): fires exactly one beat, exits.

_SRC_DIR = Path(__file__).resolve().parent
_PYTHON = sys.executable


def scheduler_hook_instructions(workspace: Optional[Path] = None) -> str:
    """Instructions for hooking the host's scheduler to the heartbeat.

    Maro never installs its own daemon or timer. `maro heartbeat` fires
    exactly one beat (health check + tiered recovery) and exits — wire it to
    whatever already wakes up on your host.
    """
    ws = workspace or workspace_root()
    return f"""\
Supervision — Maro is an app, not a daemon. It installs no systemd unit,
launchd agent, or cron entry. If you want a recurring heartbeat, hook your
host's existing scheduler to the one-shot entrypoint:

    maro heartbeat        # fires exactly one beat, then exits
                          # (flags: --dry-run, --no-escalate)

Example hooks (pick one; both call the same entrypoint):

  OpenClaw substrate — deliver on its next scheduled wake:
    openclaw system event --mode next-heartbeat --text "Run: maro heartbeat"

  Plain cron — every 30 minutes:
    */30 * * * * maro heartbeat >> {ws}/logs/heartbeat.log 2>&1

Manual CLI / mission runs never require a heartbeat. Not pip-installed?
Substitute: cd <repo> && PYTHONPATH=src {_PYTHON} -m cli heartbeat
"""


def print_scheduler_instructions(workspace: Optional[Path] = None) -> None:
    print(scheduler_hook_instructions(workspace))


# ---------------------------------------------------------------------------
# Smoke test
# ---------------------------------------------------------------------------

def run_smoke_test() -> bool:
    """Run a real NOW-lane task through a live LLM backend. Returns True if it
    exits 0. Deliberately not a --dry-run: the point is to prove a fresh
    install's configured backend (API key / CLI) actually works, which a
    dry run (canned response, no API call) can't verify."""
    script = _SRC_DIR / "handle.py"
    if not script.exists():
        print("  [smoke] handle.py not found — skipping", file=sys.stderr)
        return False
    try:
        result = subprocess.run(
            [_PYTHON, str(script), "What time is it?"],
            capture_output=True,
            text=True,
            timeout=30,
            env={**os.environ, "MARO_WORKSPACE": str(workspace_root())},
        )
        if result.returncode == 0:
            print("  [smoke] NOW-lane task succeeded.")
            return True
        print(f"  [smoke] exit code {result.returncode}: {result.stderr[:200]}", file=sys.stderr)
        return False
    except Exception as e:
        print(f"  [smoke] error: {e}", file=sys.stderr)
        return False


# ---------------------------------------------------------------------------
# Status
# ---------------------------------------------------------------------------

def show_status() -> None:
    ws = workspace_root()
    print(f"Workspace:  {ws}")
    print(f"Exists:     {ws.exists()}")
    if ws.exists():
        for sub in _WORKSPACE_SUBDIRS:
            exists = (ws / sub).exists()
            print(f"  {sub}/: {'ok' if exists else 'MISSING'}")
    print()
    print("Supervision: none installed by Maro (by design — see `maro-bootstrap services`).")


# ---------------------------------------------------------------------------
# Full install
# ---------------------------------------------------------------------------

def install(run_smoke: bool = True) -> None:
    ws = workspace_root()
    print(f"Installing maro-orchestration into {ws}")

    print("  Creating workspace directories...")
    create_workspace_dirs(ws)
    print("  Done.")

    print("  Writing starter config...")
    try:
        cfg_written = write_starter_config()
        if cfg_written:
            print(f"    {cfg_written} (commented template — edit to taste)")
        else:
            print("    existing config found — left untouched")
    except OSError as exc:
        # User tier lives under HOME, which can be read-only where the
        # workspace isn't (systemd ProtectHome, odd container HOMEs). The
        # config is optional — keep installing.
        print(f"    could not write user config ({exc}) — continuing without it")
    print("  Done.")

    if run_smoke:
        print("  Running smoke test...")
        run_smoke_test()

    print()
    print("Installation complete.")
    print()
    print_scheduler_instructions(ws)


# ---------------------------------------------------------------------------
# CLI (invoked directly or via maro-bootstrap entry point)
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> None:
    import argparse
    parser = argparse.ArgumentParser(
        prog="maro-bootstrap",
        description="Bootstrap maro-orchestration on a new machine",
    )
    sub = parser.add_subparsers(dest="cmd")

    sub.add_parser("install", help="Workspace install: dirs + scheduler instructions + smoke test")
    sub.add_parser("dirs", help="Create workspace directories only")
    sub.add_parser("services", help="Print host-scheduler hook instructions (Maro installs no unit files)")
    sub.add_parser("status", help="Show workspace status")
    p_smoke = sub.add_parser("smoke", help="Run smoke test (live NOW-lane request; needs a working LLM backend)")
    p_smoke  # noqa: B018

    args = parser.parse_args(argv)

    if args.cmd == "install":
        install()
    elif args.cmd == "dirs":
        ws = create_workspace_dirs()
        print(f"Workspace dirs created at {ws}")
    elif args.cmd == "services":
        print_scheduler_instructions()
    elif args.cmd == "status":
        show_status()
    elif args.cmd == "smoke":
        ok = run_smoke_test()
        sys.exit(0 if ok else 1)
    else:
        parser.print_help()


def _smoke_main() -> None:
    """Entry point for the `maro-test` console script — runs the smoke test directly."""
    main(["smoke"])


if __name__ == "__main__":
    main()
