import os
from pathlib import Path

from bkp_agent.store import OrderStore

ROOT = Path(__file__).resolve().parents[2]
DB_PATH = Path(os.environ.get("BKP_DB_PATH", ROOT / "data" / "bkp.db"))
SEED_FILE = ROOT / "data" / "orders.json"


def open_store() -> OrderStore:
    """Open the order DB, seeding it with the sample orders on first run."""
    return OrderStore(DB_PATH, seed_file=SEED_FILE)


def ai_enabled() -> bool:
    """Use Claude only when asked to, or when an API key is set.

    BKP_AGENT_MODE: "simple" (no AI, no key needed), "claude", or "auto"
    (default: Claude if ANTHROPIC_API_KEY or ANTHROPIC_AUTH_TOKEN is set).
    """
    mode = os.environ.get("BKP_AGENT_MODE", "auto").lower()
    if mode in ("simple", "claude"):
        return mode == "claude"
    return bool(os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN"))


def make_agent(store: OrderStore):
    if ai_enabled():
        from bkp_agent.agent import SupportAgent
        return SupportAgent(store)
    from bkp_agent.simple import SimpleAgent
    return SimpleAgent(store)
