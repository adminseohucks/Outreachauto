"""LinkedPilot v2 — First-time setup script.

Run this once: python setup.py
"""

import subprocess
import sys
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent


def main():
    print("=" * 60)
    print("  LinkedPilot v2 — First-Time Setup")
    print("=" * 60)
    print()

    # 1. Check Python version
    print("[1/5] Checking Python version...")
    v = sys.version_info
    if v.major < 3 or (v.major == 3 and v.minor < 10):
        print(f"  ERROR: Python 3.10+ required, got {v.major}.{v.minor}")
        sys.exit(1)
    print(f"  OK: Python {v.major}.{v.minor}.{v.micro}")

    # 2. Install dependencies
    print("\n[2/5] Installing Python dependencies...")
    req_file = BASE_DIR / "requirements.txt"
    subprocess.check_call([sys.executable, "-m", "pip", "install", "-r", str(req_file)])
    print("  OK: Dependencies installed")

    # 3. Install Playwright browsers
    print("\n[3/5] Installing Playwright Chromium browser...")
    try:
        subprocess.check_call([sys.executable, "-m", "playwright", "install", "chromium"])
        print("  OK: Playwright Chromium installed")
    except subprocess.CalledProcessError:
        print("  WARNING: Playwright install failed. Run manually:")
        print("  python -m playwright install chromium")

    # 4. Create data directories
    print("\n[4/5] Creating data directories...")
    dirs = [
        BASE_DIR / "data",
        BASE_DIR / "data" / "exports",
        BASE_DIR / "data" / "browser_profiles",
    ]
    for d in dirs:
        d.mkdir(parents=True, exist_ok=True)
        print(f"  Created: {d}")

    # 5. Create .env if not exists
    print("\n[5/5] Checking .env file...")
    env_file = BASE_DIR / ".env"
    env_example = BASE_DIR / ".env.example"
    if not env_file.exists() and env_example.exists():
        import shutil
        shutil.copy(env_example, env_file)
        print("  Created .env from .env.example")
        print("  IMPORTANT: Edit .env to set your VPS API key and URL")
    elif env_file.exists():
        print("  OK: .env already exists")
    else:
        print("  WARNING: No .env.example found")

    print()
    print("=" * 60)
    print("  Setup complete!")
    print()
    print("  Next steps:")
    print("  1. Edit .env file with your VPS settings")
    print("  2. Start the dashboard: python start.py")
    print("  3. Open http://localhost:8080 in your browser")
    print("=" * 60)


if __name__ == "__main__":
    main()
