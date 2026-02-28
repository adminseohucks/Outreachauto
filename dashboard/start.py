"""LinkedPilot v2 — Dashboard launcher.

Run: python start.py
"""

import sys
import os
import webbrowser
import threading
import time
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
os.chdir(str(BASE_DIR))

# Add parent to path so 'app' package is importable
sys.path.insert(0, str(BASE_DIR))


def open_browser(port: int):
    """Open browser after a short delay to let server start."""
    time.sleep(2)
    url = f"http://localhost:{port}"
    print(f"\n  Opening {url} in your browser...")
    webbrowser.open(url)


def main():
    print("=" * 60)
    print("  LinkedPilot v2 — Dashboard")
    print("=" * 60)
    print()

    # Load config
    from dotenv import load_dotenv
    load_dotenv(BASE_DIR / ".env")

    host = os.getenv("DASHBOARD_HOST", "0.0.0.0")
    port = int(os.getenv("DASHBOARD_PORT", "8080"))

    print(f"  Starting server on {host}:{port}")
    print(f"  Dashboard: http://localhost:{port}")
    print(f"  Press Ctrl+C to stop")
    print()

    # Open browser in background thread
    threading.Thread(target=open_browser, args=(port,), daemon=True).start()

    # Start uvicorn
    import uvicorn
    uvicorn.run(
        "app.main:app",
        host=host,
        port=port,
        reload=False,
        log_level="info",
    )


if __name__ == "__main__":
    main()
