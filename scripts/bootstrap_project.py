"""Set the project up on a fresh machine, including the hidden folders.

    python scripts/bootstrap_project.py

Creates any missing directories, copies .env.example to .env, verifies the
Python version, installs dependencies, and validates the config. Safe to run
repeatedly - it never overwrites a file that already exists.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

REQUIRED_DIRS = [
    ROOT / "config",
    ROOT / "src",
    ROOT / "scripts",
    ROOT / "state",
    ROOT / ".github" / "workflows",
]

REQUIRED_FILES = [
    ROOT / "config" / "schedule.yaml",
    ROOT / "config" / "messages.yaml",
    ROOT / "config" / "deadlines.yaml",
    ROOT / "src" / "main.py",
    ROOT / ".github" / "workflows" / "human_os_bot.yml",
]


def check(label: str, ok: bool, detail: str = "") -> bool:
    print(f"  [{'ok' if ok else 'XX'}] {label}{f' - {detail}' if detail else ''}")
    return ok


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    print(f"Human OS bootstrap\n  root: {ROOT}\n")

    print("Python")
    version_ok = sys.version_info >= (3, 11)
    check(f"Python {sys.version.split()[0]}", version_ok, "" if version_ok else "3.11+ required")

    print("\nDirectories")
    for directory in REQUIRED_DIRS:
        existed = directory.exists()
        directory.mkdir(parents=True, exist_ok=True)
        check(str(directory.relative_to(ROOT)), True, "already there" if existed else "created")

    gitkeep = ROOT / "state" / ".gitkeep"
    if not gitkeep.exists() and not (ROOT / "state" / "state.json").exists():
        gitkeep.write_text("", encoding="utf-8")

    print("\nEnvironment file")
    env_path = ROOT / ".env"
    example = ROOT / ".env.example"
    if env_path.exists():
        check(".env", True, "already there - not touched")
    elif example.exists():
        shutil.copyfile(example, env_path)
        check(".env", True, "created from .env.example - fill in your token and chat id")
    else:
        check(".env", False, ".env.example is missing")

    print("\nDependencies")
    requirements = ROOT / "requirements.txt"
    if requirements.exists():
        result = subprocess.run(
            [sys.executable, "-m", "pip", "install", "-r", str(requirements)],
            capture_output=True,
            text=True,
        )
        check("pip install -r requirements.txt", result.returncode == 0, result.stderr.strip()[-200:])
    else:
        check("requirements.txt", False, "missing")

    print("\nRequired files")
    missing = [f for f in REQUIRED_FILES if not f.exists()]
    for path in REQUIRED_FILES:
        check(str(path.relative_to(ROOT)), path.exists())

    print("\nConfig validation")
    validator = ROOT / "scripts" / "validate_config.py"
    if validator.exists() and not missing:
        result = subprocess.run([sys.executable, str(validator)], text=True)
        check("validate_config.py", result.returncode == 0)

    print("\nNext: open MANUAL_ACTIONS.md and work down the checklist.")
    print("Then run:  python scripts/test_send.py")
    return 1 if missing else 0


if __name__ == "__main__":
    raise SystemExit(main())
