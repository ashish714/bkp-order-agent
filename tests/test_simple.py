"""The no-AI assistant: scripted conversations against the sample orders."""
import pytest

from bkp_agent.config import ai_enabled
from bkp_agent.simple import SimpleAgent, inr


@pytest.fixture
def bee(store):
    return SimpleAgent(store)


def test_inr_grouping():
    assert inr(2499) == "₹2,499"
    assert inr(123456) == "₹1,23,456"
    assert inr(49) == "₹49"


def test_track_asks_for_missing_details_then_answers(bee):
    assert "order ID" in bee.chat("Where is my order?")
    assert "email" in bee.chat("BKP-10002")
    reply = bee.chat("rahul@example.com")
    assert "on its way" in reply
    assert "DLV998877665" in reply
    assert "16 Oct 2026" in reply


def test_details_in_one_message(bee):
    reply = bee.chat("show me order details for BKP-10001 priya@example.com")
    assert "Wireless Headphones" in reply
    assert "Total: ₹3,097" in reply


def test_wrong_email_does_not_reveal_order(bee):
    reply = bee.chat("track BKP-10002 wrong@example.com")
    assert "couldn't find" in reply
    assert "Delhivery" not in reply


def test_refund_needs_yes(bee, store):
    assert "₹2,698" in bee.chat("cancel my order BKP-10003 ananya@example.com")
    assert "haven't processed" in bee.chat("no")
    assert store.get_status("BKP-10003", "ananya@example.com")["status"] == "PROCESSING"

    bee.chat("refund BKP-10003 ananya@example.com")
    reply = bee.chat("yes please")
    assert "confirmed" in reply and "cancelled" in reply
    assert store.get_status("BKP-10003", "ananya@example.com")["status"] == "CANCELLED"


def test_changing_subject_drops_pending_refund(bee, store):
    bee.chat("refund BKP-10001 priya@example.com")
    bee.chat("actually, is shipping free?")
    assert "Shall I" not in bee.chat("yes")
    assert store.get_status("BKP-10001", "priya@example.com")["status"] == "DELIVERED"


@pytest.mark.parametrize("msg", [
    "refund BKP-10002 rahul@example.com",      # in transit
    "can I get a refund for BKP-10004? priya@example.com",  # outside 30 days
])
def test_ineligible_refunds_explained(bee, msg):
    assert "can't be refunded" in bee.chat(msg)


@pytest.mark.parametrize("msg,expected", [
    ("how long do refunds take?", "1-3 business days"),
    ("is cash on delivery available", "Cash on delivery"),
    ("Is shipping free?", "₹999"),
    ("what is the return window?", "30 days"),
    ("my order says delivered but i didn't get it", "neighbours"),
    ("how do I reach a human?", "1800-000-0000"),
    ("do you ship outside india?", "within India"),
])
def test_faq(bee, msg, expected):
    assert expected in bee.chat(msg)


@pytest.mark.parametrize("msg", [
    "is my order delivered?", "when will my order arrive?", "can i get a refund?",
    "what did i order", "I want to cancel",
])
def test_action_questions_ask_for_order(bee, msg):
    assert "order ID" in bee.chat(msg)


def test_gibberish_shows_menu(bee):
    assert "I can help you with" in bee.chat("asdf qwerty")


def test_mode_selection(monkeypatch):
    for var in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "BKP_AGENT_MODE"):
        monkeypatch.delenv(var, raising=False)
    assert ai_enabled() is False
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    assert ai_enabled() is True
    monkeypatch.setenv("BKP_AGENT_MODE", "simple")
    assert ai_enabled() is False


@pytest.mark.parametrize("button", ["Track my order", "Order details", "I want a refund"])
def test_quick_buttons_start_actions(bee, button):
    assert "order ID" in bee.chat(button)


def test_order_and_email_reply_continues_action(bee):
    bee.chat("Track my order")
    assert "on its way" in bee.chat("BKP-10002 rahul@example.com")
