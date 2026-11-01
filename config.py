import os
from pathlib import Path

BASE_DIR = Path(os.path.abspath(os.path.dirname(__file__)))
INSTANCE_DIR = BASE_DIR / "instance"
SUBMISSION_DB = INSTANCE_DIR / "phase9_smartflow_submission.db"
DEMO_DB = INSTANCE_DIR / "smartflow_demo.db"
V2_DB = INSTANCE_DIR / "phase9_smartflow_v2.db"
DATABASE_PATH = SUBMISSION_DB


class Config:
    SECRET_KEY = os.environ.get("SECRET_KEY", "smartflow-secret-key-2026")
    SQLALCHEMY_DATABASE_URI = os.environ.get(
        "DATABASE_URL",
        "sqlite:///" + (DEMO_DB if os.environ.get("SMARTFLOW_DEMO") == "1" else DATABASE_PATH).as_posix(),
    )
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    RANDOM_SEED = 20261022
