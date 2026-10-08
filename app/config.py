"""Application configuration loaded from environment variables."""

from pathlib import Path
import os

from dotenv import load_dotenv


PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(PROJECT_ROOT / ".env")

DATA_DIR = PROJECT_ROOT / "data"
DATABASE_PATH = DATA_DIR / "reader.db"
BROWSER_PROFILE_PATH = DATA_DIR / "browser_profile"
PLAYWRIGHT_BROWSER_CHANNEL = "msedge"
BILIBILI_SESSDATA = os.getenv("BILIBILI_SESSDATA", "").strip()
