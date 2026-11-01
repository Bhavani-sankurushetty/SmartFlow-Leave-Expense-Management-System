"""SmartFlow Demo Application Entry Point.

Runs the SmartFlow web interface exclusively against the isolated demo database:
instance/smartflow_demo.db.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

# Enforce demo database mode
os.environ["SMARTFLOW_DEMO"] = "1"

BASE_DIR = Path(os.path.abspath(os.path.dirname(__file__)))
DEMO_DB = BASE_DIR / "instance" / "smartflow_demo.db"
os.environ["DATABASE_URL"] = f"sqlite:///{DEMO_DB.as_posix()}"

from app import app, db
from sqlalchemy import create_engine

# Rebind engine directly to guarantee demo DB is targeted
_demo_engine = create_engine(f"sqlite:///{DEMO_DB.as_posix()}")
db._app_engines[app] = {None: _demo_engine}


def get_demo_app():
    """Return configured demo application instance."""
    return app


if __name__ == "__main__":
    port = int(os.environ.get("PORT", os.environ.get("DEMO_PORT", "5000")))
    print("=" * 64)
    print(" SmartFlow Interactive Demo Server")
    print(f" Active Database: {DEMO_DB}")
    print(f" Target URL:      http://127.0.0.1:{port}/")
    print("=" * 64)
    try:
        app.run(host="127.0.0.1", port=port, debug=False)
    except OSError as e:
        if "address already in use" in str(e).lower() or "10048" in str(e):
            alt_port = 5001
            print(f"\nPort {port} is currently in use.")
            print(f"Starting demo server on fallback port {alt_port}...")
            print(f"Target URL: http://127.0.0.1:{alt_port}/")
            print("=" * 64)
            app.run(host="127.0.0.1", port=alt_port, debug=False)
        else:
            raise
