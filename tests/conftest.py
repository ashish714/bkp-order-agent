from datetime import date
from pathlib import Path

import pytest

from bkp_agent.store import OrderStore

DATA = Path(__file__).resolve().parents[1] / "data"


@pytest.fixture
def store(tmp_path):
    return OrderStore(tmp_path / "test.db", seed_file=DATA / "orders.json",
                      today=date(2026, 10, 14))
