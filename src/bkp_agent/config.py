import os
from pathlib import Path

from bkp_agent.store import OrderStore

ROOT = Path(__file__).resolve().parents[2]
DB_PATH = Path(os.environ.get("BKP_DB_PATH", ROOT / "data" / "bkp.db"))
SEED_FILE = ROOT / "data" / "orders.json"


def open_store() -> OrderStore:
    """Open the order DB, seeding it with the sample orders on first run."""
    return OrderStore(DB_PATH, seed_file=SEED_FILE)
