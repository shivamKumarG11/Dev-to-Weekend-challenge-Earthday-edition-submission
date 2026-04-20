"""
run.py — TERRA-STATE: VOX ATLAS
One-command launcher: sets up venv, installs deps, starts the server.

Usage:
    python run.py
"""
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).parent
APP_DIR = ROOT / "terra-state"
VENV_DIR = ROOT / ".venv"
REQUIREMENTS = APP_DIR / "requirements.txt"

# Resolve the python/pip/uvicorn executables inside the venv
if sys.platform == "win32":
    PYTHON = VENV_DIR / "Scripts" / "python.exe"
    PIP    = VENV_DIR / "Scripts" / "pip.exe"
else:
    PYTHON = VENV_DIR / "bin" / "python"
    PIP    = VENV_DIR / "bin" / "pip"


def run(cmd: list, **kwargs):
    result = subprocess.run(cmd, **kwargs)
    if result.returncode != 0:
        sys.exit(result.returncode)


def main():
    print("\n  TERRA-STATE: VOX ATLAS v2.0\n")

    # 1. Create venv if missing
    if not VENV_DIR.exists():
        print("  [1/3] Creating virtual environment...")
        run([sys.executable, "-m", "venv", str(VENV_DIR)])
    else:
        print("  [1/3] Virtual environment ready.")

    # 2. Install dependencies
    print("  [2/3] Installing dependencies...")
    run([str(PIP), "install", "-q", "-r", str(REQUIREMENTS)])

    # 3. Start uvicorn
    print("  [3/3] Starting server at http://localhost:8000\n")
    run(
        [str(PYTHON), "-m", "uvicorn", "app:app", "--reload", "--host", "0.0.0.0", "--port", "8000"],
        cwd=str(APP_DIR),
    )


if __name__ == "__main__":
    main()
