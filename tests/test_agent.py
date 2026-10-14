"""Agent loop tests with a scripted fake Claude client (no API calls)."""
import json
from types import SimpleNamespace as NS

from bkp_agent.agent import TOOLS, SupportAgent


def text(t):
    return NS(type="text", text=t)


def tool(name, id_, **inp):
    return NS(type="tool_use", name=name, id=id_, input=inp)


def resp(*blocks, stop=None):
    stop = stop or ("tool_use" if any(b.type == "tool_use" for b in blocks) else "end_turn")
    return NS(stop_reason=stop, content=list(blocks))


class FakeClient:
    """Plays back scripted responses. A script entry may be a function of the
    request kwargs, for responses that depend on earlier tool results."""

    def __init__(self, script):
        self.script = list(script)
        self.calls = []
        self.beta = NS(messages=NS(create=self._create))

    def _create(self, **kwargs):
        self.calls.append({**kwargs, "messages": list(kwargs["messages"])})
        nxt = self.script.pop(0)
        return nxt(kwargs) if callable(nxt) else nxt


def tool_results(call):
    return {r["tool_use_id"]: r for r in call["messages"][-1]["content"]}


def test_tools_are_strict_schemas():
    for t in TOOLS:
        assert t["strict"] is True
        assert t["input_schema"]["additionalProperties"] is False


def test_tracks_order_via_tool(store):
    client = FakeClient([
        resp(tool("get_order_status", "t1", order_id="BKP-10002", email="rahul@example.com")),
        resp(text("Your smart watch is in transit and should arrive by 16 Oct.")),
    ])
    agent = SupportAgent(store, client=client)
    reply = agent.chat("Where is BKP-10002? email rahul@example.com")
    assert "in transit" in reply
    result = tool_results(client.calls[1])["t1"]
    assert result["is_error"] is False
    assert json.loads(result["content"])["status"] == "IN_TRANSIT"
    # request shape
    first = client.calls[0]
    assert first["model"] == "claude-opus-5-5"
    assert first["fallbacks"] == "default"
    assert "<faq>" in first["system"]


def test_wrong_email_is_tool_error(store):
    client = FakeClient([
        resp(tool("get_order_details", "t1", order_id="BKP-10002", email="me@example.com")),
        resp(text("I couldn't find that order for that email.")),
    ])
    SupportAgent(store, client=client).chat("details for BKP-10002, me@example.com")
    assert tool_results(client.calls[1])["t1"]["is_error"] is True


def test_refund_cannot_be_confirmed_in_same_turn(store):
    """Even if the model tries to confirm right away, the customer must reply first."""
    rid = {}

    def confirm(tool_id):
        def make(kwargs):
            if "id" not in rid:
                rid["id"] = json.loads(kwargs["messages"][-1]["content"][0]["content"])["refund_id"]
            return resp(tool("confirm_refund", tool_id, refund_id=rid["id"]))
        return make

    client = FakeClient([
        resp(tool("request_refund", "t1", order_id="BKP-10003",
                  email="ananya@example.com", reason="Ordered by mistake")),
        confirm("t2"),  # model jumps the gun in the same turn
        resp(text("I can refund ₹2,698. Shall I go ahead?")),
        confirm("t3"),  # after the customer says yes
        resp(text("Done! Your refund of ₹2,698 is confirmed.")),
    ])
    agent = SupportAgent(store, client=client)

    agent.chat("Refund BKP-10003 please, ananya@example.com, ordered by mistake")
    early = tool_results(client.calls[2])["t2"]
    assert early["is_error"] is True and "hasn't confirmed" in early["content"]
    assert store.get_status("BKP-10003", "ananya@example.com")["status"] == "PROCESSING"

    reply = agent.chat("Yes, go ahead")
    confirmed = tool_results(client.calls[4])["t3"]
    assert confirmed["is_error"] is False
    assert json.loads(confirmed["content"])["status"] == "CONFIRMED"
    assert "confirmed" in reply
    assert store.get_status("BKP-10003", "ananya@example.com")["status"] == "CANCELLED"


def test_confirm_unknown_refund_rejected(store):
    client = FakeClient([
        resp(tool("confirm_refund", "t1", refund_id="RF-DEADBEEF")),
        resp(text("I don't see that refund.")),
    ])
    SupportAgent(store, client=client).chat("confirm RF-DEADBEEF")
    assert tool_results(client.calls[1])["t1"]["is_error"] is True


def test_refusal_rolls_back_turn(store):
    client = FakeClient([resp(stop="refusal"), resp(text("Hi! How can I help?"))])
    agent = SupportAgent(store, client=client)
    agent.chat("something off-topic")
    assert agent.messages == []
    agent.chat("hello")
    assert [m["role"] for m in agent.messages] == ["user", "assistant"]


def test_history_keeps_full_content_blocks(store):
    thinking = NS(type="thinking", thinking="", signature="sig")
    client = FakeClient([resp(thinking, text("Hello!"))])
    agent = SupportAgent(store, client=client)
    agent.chat("hi")
    assert agent.messages[-1]["content"][0] is thinking
