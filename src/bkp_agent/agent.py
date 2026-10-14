"""The BKP support agent: a Claude tool-use loop over the order store."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import anthropic

from bkp_agent.store import OrderStore, StoreError

MODEL = "claude-opus-5-5"
# If Claude declines a request, the API re-runs it on a fallback model
# chosen by the server instead of returning a bare refusal.
FALLBACK_BETA = "server-side-fallback-2026-07-01"

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_FAQ = ROOT / "data" / "faq.md"

SYSTEM_TEMPLATE = """You are Bee, the customer-support assistant for BKP, an online \
electronics and accessories store in India.

You help customers with:
- Tracking an order (status, carrier, tracking number, delivery estimate).
- Order details (items, prices, totals, payment method, address).
- Refunds and cancellations.
- General questions, answered from the FAQ below.

Rules:
- To look up any order you need BOTH the order ID (format BKP-XXXXX) and the \
email used to place it. Ask for whichever is missing. Never guess either one.
- Only state order facts that came from a tool result. If a tool returns an \
error, explain it plainly and say what the customer can do next.
- Refunds take two steps. First call request_refund, which creates a PENDING \
request and tells you the amount. Tell the customer the amount and what will \
happen (pickup, where the money goes, how long it takes) and ask them to \
confirm. Only after the customer clearly says yes in a later message, call \
confirm_refund. If they say no, don't confirm.
- Answer policy questions only from the FAQ. If the FAQ doesn't cover \
something, say so and share the human support contact.
- Amounts are in Indian rupees; write them like ₹2,499.
- Be warm and brief. This is a chat, so use short paragraphs or bullets, not \
long essays.

<faq>
{faq}
</faq>"""

_ORDER_PARAMS = {
    "order_id": {"type": "string", "description": "BKP order ID, e.g. BKP-10001."},
    "email": {"type": "string", "description": "Email address the order was placed with."},
}


def _tool(name: str, description: str, properties: dict, required: list[str]) -> dict:
    return {
        "name": name,
        "description": description,
        "strict": True,
        "input_schema": {
            "type": "object",
            "properties": properties,
            "required": required,
            "additionalProperties": False,
        },
    }


TOOLS = [
    _tool("get_order_status",
          "Get the current shipping status of an order: status, carrier, tracking "
          "number, estimated delivery and the full tracking timeline.",
          _ORDER_PARAMS, ["order_id", "email"]),
    _tool("get_order_details",
          "Get full order details: items, quantities, prices, subtotal, shipping fee, "
          "total, payment method, shipping address and any refunds on the order.",
          _ORDER_PARAMS, ["order_id", "email"]),
    _tool("check_refund_eligibility",
          "Check whether an order can be refunded and for how much, without creating "
          "anything. Use this to answer 'can I get a refund?' questions.",
          _ORDER_PARAMS, ["order_id", "email"]),
    _tool("request_refund",
          "Create a PENDING refund request for an eligible order. Does not pay out "
          "anything; the customer must then confirm and you call confirm_refund.",
          {**_ORDER_PARAMS,
           "reason": {"type": "string", "description": "The customer's reason, in their words."}},
          ["order_id", "email", "reason"]),
    _tool("confirm_refund",
          "Confirm a PENDING refund so it is processed. Only call this after the "
          "customer has explicitly agreed, in a later message, to the amount you told them.",
          {"refund_id": {"type": "string", "description": "Refund ID from request_refund, e.g. RF-1A2B3C4D."}},
          ["refund_id"]),
]


class SupportAgent:
    """One conversation with one customer. Not thread-safe; use one per chat."""

    def __init__(self, store: OrderStore, client: Any | None = None,
                 faq_path: str | Path = DEFAULT_FAQ, model: str = MODEL):
        self.store = store
        self.client = client or anthropic.Anthropic()
        self.model = model
        self.system = SYSTEM_TEMPLATE.format(faq=Path(faq_path).read_text().strip())
        self.messages: list[dict] = []
        self.turn = 0
        # refund_id -> the customer turn it was requested in. A refund can only be
        # confirmed on a later turn, so the customer always gets to say yes or no
        # first, whatever the model decides to do.
        self._pending_refunds: dict[str, int] = {}

    # ---- tools -----------------------------------------------------------

    def _run_tool(self, name: str, args: dict) -> dict:
        if name == "get_order_status":
            return self.store.get_status(args["order_id"], args["email"])
        if name == "get_order_details":
            return self.store.get_details(args["order_id"], args["email"])
        if name == "check_refund_eligibility":
            return self.store.check_refund_eligibility(args["order_id"], args["email"])
        if name == "request_refund":
            result = self.store.create_refund_request(
                args["order_id"], args["email"], args["reason"])
            self._pending_refunds.setdefault(result["refund_id"], self.turn)
            return result
        if name == "confirm_refund":
            refund_id = args["refund_id"].strip().upper()
            requested_turn = self._pending_refunds.get(refund_id)
            if requested_turn is None:
                raise StoreError(
                    f"Refund {refund_id} wasn't requested in this conversation. "
                    "Call request_refund first.")
            if requested_turn >= self.turn:
                raise StoreError(
                    "The customer hasn't confirmed yet. Tell them the refund amount "
                    "and ask them to confirm, then wait for their reply.")
            result = self.store.confirm_refund(refund_id)
            del self._pending_refunds[refund_id]
            return result
        raise StoreError(f"Unknown tool {name}.")

    def _tool_result(self, block) -> dict:
        try:
            content, is_error = json.dumps(self._run_tool(block.name, block.input)), False
        except StoreError as e:
            content, is_error = str(e), True
        except (KeyError, TypeError) as e:
            content, is_error = f"Invalid tool input: {e}", True
        return {"type": "tool_result", "tool_use_id": block.id,
                "content": content, "is_error": is_error}

    # ---- conversation ----------------------------------------------------

    def chat(self, user_message: str) -> str:
        """Send one customer message; returns Bee's reply."""
        self.turn += 1
        checkpoint = len(self.messages)
        self.messages.append({"role": "user", "content": user_message})

        for _ in range(10):  # tool-call rounds per customer message
            response = self.client.beta.messages.create(
                model=self.model,
                max_tokens=16000,
                system=self.system,
                tools=TOOLS,
                messages=self.messages,
                output_config={"effort": "medium"},
                cache_control={"type": "ephemeral"},
                betas=[FALLBACK_BETA],
                fallbacks="default",
            )

            if response.stop_reason == "refusal":
                # Drop this turn so the conversation can carry on cleanly.
                del self.messages[checkpoint:]
                return ("Sorry, I can't help with that one. For anything else about "
                        "your BKP orders, I'm happy to help.")

            # Append the full content (not just text) so thinking blocks round-trip.
            self.messages.append({"role": "assistant", "content": response.content})

            tool_uses = [b for b in response.content if b.type == "tool_use"]
            if response.stop_reason != "tool_use" or not tool_uses:
                return _text(response) or "Sorry, I didn't catch that. Could you rephrase?"

            # All results go back in a single user message.
            self.messages.append(
                {"role": "user", "content": [self._tool_result(b) for b in tool_uses]})

        return ("Sorry, I'm having trouble with that request. Please try again, or "
                "contact support@bkp.example.")

    def reset(self) -> None:
        self.messages.clear()
        self._pending_refunds.clear()
        self.turn = 0


def is_missing_credentials(exc: Exception) -> bool:
    """The SDK raises a TypeError (not an API error) when it finds no API key."""
    return isinstance(exc, TypeError) and "authentication method" in str(exc)


NO_CREDENTIALS_MSG = ("No Anthropic credentials found. Set ANTHROPIC_API_KEY "
                      "(or run `ant auth login`) and try again.")


def _text(response) -> str:
    return "\n".join(b.text for b in response.content if b.type == "text").strip()
