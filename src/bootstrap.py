"""Bootstrap poe-orchestration from a fresh machine.

Handles:
- Creating workspace directory structure
- Writing systemd (Linux) or launchd (macOS) service files
- Smoke-testing the install by running poe-heartbeat once

Entry points:
  poe-bootstrap install    -- full install (dirs + services + first heartbeat)
  poe-bootstrap dirs       -- create workspace dirs only
  poe-bootstrap services   -- write service files only
  poe-bootstrap status     -- show current workspace and service status
  poe-bootstrap smoke      -- run a dry-run NOW-lane task and verify output
"""

from __future__ import annotations

import os
import platform
import subprocess
import sys
from pathlib import Path
from typing import Optional

from config import workspace_root, deploy_dir


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
# Service file templates
# ---------------------------------------------------------------------------

_SRC_DIR = Path(__file__).resolve().parent
_PYTHON = sys.executable


def _systemd_service(name: str, description: str, exec_cmd: str, workspace: Path) -> str:
    return f"""[Unit]
Description={description}
After=network.target

[Service]
Type=simple
User={os.getenv('USER', 'poe')}
WorkingDirectory={_SRC_DIR}
Environment=POE_WORKSPACE={workspace}
ExecStart={exec_cmd}
Restart=on-failure
RestartSec=30
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
"""


def _launchd_plist(label: str, description: str, exec_args: list[str], workspace: Path) -> str:
    args_xml = "\n".join(f"        <string>{a}</string>" for a in exec_args)
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>{label}</string>
    <key>ProgramArguments</key>
    <array>
{args_xml}
    </array>
    <key>EnvironmentVariables</key>
    <dict>
        <key>POE_WORKSPACE</key>
        <string>{workspace}</string>
    </dict>
    <key>WorkingDirectory</key>
    <string>{_SRC_DIR}</string>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <true/>
    <key>StandardOutPath</key>
    <string>{workspace}/logs/{label.split('.')[-1]}.log</string>
    <key>StandardErrorPath</key>
    <string>{workspace}/logs/{label.split('.')[-1]}.err.log</string>
</dict>
</plist>
"""


_SERVICES: list[dict] = [
    {
        "name": "poe-heartbeat",
        "label": "com.poe.heartbeat",
        "description": "Poe Orchestration — Heartbeat",
        "exec_cmd": f"{_PYTHON} {_SRC_DIR}/sheriff.py --heartbeat",
        "exec_args": [_PYTHON, str(_SRC_DIR / "sheriff.py"), "--heartbeat"],
    },
    {
        "name": "poe-telegram",
        "label": "com.poe.telegram",
        "description": "Poe Orchestration — Telegram Listener",
        "exec_cmd": f"{_PYTHON} {_SRC_DIR}/telegram_listener.py",
        "exec_args": [_PYTHON, str(_SRC_DIR / "telegram_listener.py")],
    },
    {
        "name": "poe-inspector",
        "label": "com.poe.inspector",
        "description": "Poe Orchestration — Inspector",
        "exec_cmd": f"{_PYTHON} {_SRC_DIR}/inspector.py --loop",
        "exec_args": [_PYTHON, str(_SRC_DIR / "inspector.py"), "--loop"],
    },
]


def write_service_files(workspace: Optional[Path] = None) -> list[Path]:
    """Write service files appropriate for the current OS. Returns list of written paths."""
    ws = workspace or workspace_root()
    is_linux = platform.system() == "Linux"
    written: list[Path] = []

    if is_linux:
        out_dir = deploy_dir() / "systemd"
        out_dir.mkdir(parents=True, exist_ok=True)
        for svc in _SERVICES:
            content = _systemd_service(
                name=svc["name"],
                description=svc["description"],
                exec_cmd=svc["exec_cmd"],
                workspace=ws,
            )
            path = out_dir / f"{svc['name']}.service"
            path.write_text(content)
            written.append(path)
    else:
        # macOS launchd
        out_dir = deploy_dir() / "launchd"
        out_dir.mkdir(parents=True, exist_ok=True)
        for svc in _SERVICES:
            content = _launchd_plist(
                label=svc["label"],
                description=svc["description"],
                exec_args=svc["exec_args"],
                workspace=ws,
            )
            path = out_dir / f"{svc['label']}.plist"
            path.write_text(content)
            written.append(path)

    return written


# ---------------------------------------------------------------------------
# Smoke test
# ---------------------------------------------------------------------------

def run_smoke_test() -> bool:
    """Dry-run a NOW-lane task. Returns True if it exits 0."""
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
            env={**os.environ, "POE_WORKSPACE": str(workspace_root())},
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

def _service_status(name: str) -> str:
    if platform.system() != "Linux":
        return "unknown (non-Linux)"
    try:
        r = subprocess.run(
            ["systemctl", "is-active", name],
            capture_output=True, text=True, timeout=5,
        )
        return r.stdout.strip() or "inactive"
    except Exception:
        return "unknown"


def show_status() -> None:
    ws = workspace_root()
    print(f"Workspace:  {ws}")
    print(f"Exists:     {ws.exists()}")
    if ws.exists():
        for sub in _WORKSPACE_SUBDIRS:
            exists = (ws / sub).exists()
            print(f"  {sub}/: {'ok' if exists else 'MISSING'}")
    print()
    print("Services:")
    for svc in _SERVICES:
        status = _service_status(svc["name"])
        print(f"  {svc['name']}: {status}")


# ---------------------------------------------------------------------------
# Full install
# ---------------------------------------------------------------------------

def install(run_smoke: bool = True) -> None:
    ws = workspace_root()
    print(f"Installing poe-orchestration into {ws}")

    print("  Creating workspace directories...")
    create_workspace_dirs(ws)
    print("  Done.")

    print("  Writing service files...")
    written = write_service_files(ws)
    for path in written:
        print(f"    {path}")
    print("  Done.")

    if run_smoke:
        print("  Running smoke test...")
        run_smoke_test()

    print()
    print("Installation complete.")
    if platform.system() == "Linux":
        print("  To enable services, run:")
        for svc in _SERVICES:
            service_path = deploy_dir() / "systemd" / f"{svc['name']}.service"
            print(f"    sudo cp {service_path} /etc/systemd/system/")
            print(f"    sudo systemctl enable --now {svc['name']}")
    else:
        print("  To load launchd agents, run:")
        for svc in _SERVICES:
            plist_path = deploy_dir() / "launchd" / f"{svc['label']}.plist"
            print(f"    cp {plist_path} ~/Library/LaunchAgents/")
            print(f"    launchctl load ~/Library/LaunchAgents/{svc['label']}.plist")


# ---------------------------------------------------------------------------
# CLI (invoked directly or via poe-bootstrap entry point)
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> None:
    import argparse
    parser = argparse.ArgumentParser(
        prog="poe-bootstrap",
        description="Bootstrap poe-orchestration on a new machine",
    )
    sub = parser.add_subparsers(dest="cmd")

    sub.add_parser("install", help="Full install: dirs + services + smoke test")
    sub.add_parser("dirs", help="Create workspace directories only")
    sub.add_parser("services", help="Write service files only")
    sub.add_parser("status", help="Show workspace and service status")
    p_smoke = sub.add_parser("smoke", help="Run smoke test (dry-run NOW-lane task)")
    p_smoke  # noqa: B018

    args = parser.parse_args(argv)

    if args.cmd == "install":
        install()
    elif args.cmd == "dirs":
        ws = create_workspace_dirs()
        print(f"Workspace dirs created at {ws}")
    elif args.cmd == "services":
        written = write_service_files()
        for p in written:
            print(f"Wrote: {p}")
    elif args.cmd == "status":
        show_status()
    elif args.cmd == "smoke":
        ok = run_smoke_test()
        sys.exit(0 if ok else 1)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
