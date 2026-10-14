"""A no-AI version of the BKP assistant: keyword rules plus a scripted flow.

Needs no API key and costs nothing to run. It understands short, direct
messages ("track BKP-10002", "I want a refund", "how long does delivery
take?") and asks for anything it's missing. It has the same interface as
SupportAgent, so the web and terminal chats can use either one.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from bkp_agent.store import OrderStore, StoreError

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_FAQ = ROOT / "data" / "faq.md"

ORDER_ID_RE = re.compile(r"\bBKP[-\s]?(\d{5})\b", re.IGNORECASE)
EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")

INTENT_KEYWORDS = {
    # Checked in this order; the first match wins.
    "refund": ["refund", "return", "cancel", "money back", "send it back", "send back"],
    "details": ["detail", "items", "what did i order", "what i ordered", "invoice",
                "receipt", "summary", "total", "bill", "what did i buy"],
    "track": ["track", "where", "status", "delivered", "delivery date", "deliver my",
              "shipped", "shipping status", "arrive", "when will", "dispatch", "late"],
}
YES = {"yes", "y", "yeah", "yep", "sure", "ok", "okay", "confirm", "go ahead",
       "please do", "do it", "proceed", "haan", "ha"}
NO = {"no", "n", "nope", "nah", "don't", "dont", "cancel that", "stop", "not now", "nahi"}
GREETINGS = {"hi", "hello", "hey", "hii", "namaste", "good morning", "good evening",
             "good afternoon"}
THANKS = ("thank", "thanks", "thx", "dhanyavad")
QUESTION_STARTS = ("how", "what", "which", "do ", "does", "is ", "are ", "can ",
                   "could", "will", "why", "when")
STOPWORDS = set("""a an the i me my we you your our is are was were be been do does did
to of in on at for from with and or but if it its this that these those can could
will would should how what when where which who why there here have has had please
get got am any about into out up so not just""".split())

MENU = ("I can help you with:\n"
        "• Track an order: type \"track my order\"\n"
        "• Order details: type \"order details\"\n"
        "• Refund or cancellation: type \"refund\"\n"
        "• Questions about shipping, returns or payments: just ask")


@dataclass
class FaqEntry:
    question: str
    answer: str
    words: set[str]
    answer_words: set[str]


def load_faq(path: str | Path) -> list[FaqEntry]:
    """Parse `- **Question?** Answer` bullets (with indented follow-on lines)."""
    entries: list[FaqEntry] = []
    for line in Path(path).read_text().splitlines():
        m = re.match(r"^- \*\*(.+?)\*\*\s*(.*)$", line)
        if m:
            entries.append(FaqEntry(m.group(1), m.group(2), set(), set()))
        elif entries and line.startswith("  ") and line.strip():
            entries[-1].answer += "\n" + line.strip()
    for e in entries:
        e.answer = e.answer.replace("**", "").strip()
        e.words = _words(e.question)
        e.answer_words = _words(e.answer)
    return entries


def _words(text: str) -> set[str]:
    words = re.findall(r"[a-z]+", text.lower())
    # Crude stemming so "refunds"/"refund" and "shipped"/"ship" match.
    return {re.sub(r"(ing|ed|es|s)$", "", w) or w for w in words if w not in STOPWORDS}


def inr(amount: float) -> str:
    """₹ amount with Indian digit grouping: 123456.0 -> ₹1,23,456."""
    rupees = int(round(amount))
    s = str(abs(rupees))
    if len(s) > 3:
        head, tail = s[:-3], s[-3:]
        head = ",".join(re.findall(r"\d{1,2}", head[::-1]))[::-1]
        s = f"{head},{tail}"
    return f"{'-' if rupees < 0 else ''}₹{s}"


def nice_date(value: str | None) -> str:
    if not value:
        return "not available yet"
    try:
        d = datetime.fromisoformat(value)
    except ValueError:
        return value
    return f"{d.day} {d.strftime('%b %Y')}"


STATUS_TEXT = {
    "PLACED": "has been placed",
    "PROCESSING": "is being packed and hasn't shipped yet",
    "SHIPPED": "has shipped",
    "IN_TRANSIT": "is on its way",
    "DELIVERED": "has been delivered",
    "CANCELLED": "was cancelled",
    "RETURN_REQUESTED": "has a return in progress",
}


class SimpleAgent:
    """One conversation with one customer, no AI involved."""

    def __init__(self, store: OrderStore, faq_path: str | Path = DEFAULT_FAQ):
        self.store = store
        self.faq = load_faq(faq_path)
        self.reset()

    def reset(self) -> None:
        self.order_id: str | None = None
        self.email: str | None = None
        self.intent: str | None = None          # action waiting for order ID/email
        self.awaiting_refund: str | None = None  # refund_id waiting for yes/no
        self.refund_reason = ""

    # ---- entry point ------------------------------------------------------

    def chat(self, message: str) -> str:
        text = message.strip()
        low = re.sub(r"\s+", " ", text.lower()).strip(" .!?")

        if self.awaiting_refund:
            if low in YES or any(low.startswith(y + " ") for y in YES):
                return self._confirm_refund()
            if low in NO or any(low.startswith(n + " ") for n in NO):
                self.store.cancel_refund_request(self.awaiting_refund)
                self.awaiting_refund = None
                return "No problem, I haven't processed the refund. Anything else I can help with?"
            # Anything else: carry on normally, but drop the pending refund.
            self.store.cancel_refund_request(self.awaiting_refund)
            self.awaiting_refund = None

        if low in ("menu", "help", "options", "start over", "reset"):
            if low in ("start over", "reset"):
                self.reset()
            return MENU
        if low in GREETINGS:
            return "Hi! I'm Bee from BKP. " + MENU
        if any(low.startswith(t) for t in THANKS):
            return "You're welcome! Anything else I can help with?"

        found_order = self._take_slots(text)
        found_email = bool(EMAIL_RE.search(text))
        intent = self._detect_intent(low)
        # Order IDs and emails would only confuse the FAQ word matching.
        faq_text = EMAIL_RE.sub(" ", ORDER_ID_RE.sub(" ", low))
        faq, faq_score, by_question = self._match_faq(faq_text)

        if not intent and self.intent and (found_order or found_email):
            return self._run(self.intent)  # customer is answering our question

        # An order ID means "do it for this order". Otherwise a question that
        # closely matches an FAQ question is about policy, even if it mentions
        # an action ("how long do refunds take?"). Short commands like
        # "track my order" are actions.
        question_like = text.endswith("?") or low.startswith(QUESTION_STARTS)
        policy_question = (faq is not None and by_question and faq_score >= 0.5
                           and (question_like or len(_words(faq_text)) >= 4))
        if intent and (found_order or not policy_question):
            return self._start(intent, text)
        if faq:
            reply = faq.answer
            if intent:
                reply += ("\n\nWant me to check a specific order? Send your order ID "
                          "(like BKP-10001) and the email you ordered with.")
            return reply
        if found_order or found_email:
            return ("Got it. What would you like to do with this order? I can "
                    "track it, show its details, or help with a refund.")
        return ("Sorry, I didn't quite get that. " + MENU +
                "\n\nFor anything else, email support@bkp.example or call 1800-000-0000.")

    # ---- understanding ------------------------------------------------------

    def _take_slots(self, text: str) -> bool:
        found_order = False
        if m := ORDER_ID_RE.search(text):
            new_id = f"BKP-{m.group(1)}"
            found_order = True
            if new_id != self.order_id:
                self.order_id = new_id
        if m := EMAIL_RE.search(text):
            self.email = m.group(0).lower()
        return found_order

    @staticmethod
    def _detect_intent(low: str) -> str | None:
        for intent, words in INTENT_KEYWORDS.items():
            if any(re.search(r"\b" + re.escape(w), low) for w in words):
                return intent
        return None

    def _match_faq(self, low: str) -> tuple[FaqEntry | None, float, bool]:
        """Best FAQ entry, its score, and whether it matched on the question."""
        words = _words(low)
        best, best_score, by_question = None, 0.0, False
        for e in self.faq:
            # Mostly match on the question; fall back to the answer text so
            # "is cash on delivery available?" finds the payments entry.
            q_overlap = len(words & e.words)
            a_overlap = len(words & e.answer_words)
            q_score = q_overlap / len(e.words) if q_overlap >= 2 else 0
            a_score = 0.8 * a_overlap / len(words) if a_overlap >= 2 else 0
            if max(q_score, a_score) > best_score:
                best, best_score, by_question = e, max(q_score, a_score), q_score >= a_score
        if best_score < 0.34:
            return None, 0.0, False
        return best, best_score, by_question

    # ---- actions -------------------------------------------------------------

    def _start(self, intent: str, text: str) -> str:
        self.intent = intent
        if intent == "refund":
            self.refund_reason = text[:300]
        return self._run(intent)

    def _run(self, intent: str) -> str:
        missing = [label for label, v in (("order ID (like BKP-10001)", self.order_id),
                                          ("the email address you ordered with", self.email))
                   if not v]
        if missing:
            return "Sure, I can help with that. Please share your " + " and ".join(missing) + "."
        try:
            reply = {"track": self._track, "details": self._details,
                     "refund": self._refund}[intent]()
        except StoreError:
            self.order_id = self.email = None
            return ("I couldn't find that order with that email. Please check both and "
                    "send them again, e.g. \"BKP-10001, name@example.com\".")
        self.intent = None
        return reply

    def _track(self) -> str:
        s = self.store.get_status(self.order_id, self.email)
        lines = [f"Your order {s['order_id']} {STATUS_TEXT.get(s['status'], 'is ' + s['status'].lower())}."]
        if s["status"] == "DELIVERED":
            lines.append(f"Delivered on {nice_date(s['delivered_at'])}.")
        elif s["status"] not in ("CANCELLED", "RETURN_REQUESTED") and s["estimated_delivery"]:
            lines.append(f"Expected delivery: {nice_date(s['estimated_delivery'])}.")
        if s["carrier"]:
            lines.append(f"Carrier: {s['carrier']}, tracking number {s['tracking_number']}.")
        lines.append("\nTimeline:")
        lines += [f"• {nice_date(e['at'])}: {e['note']}" for e in s["timeline"]]
        return "\n".join(lines)

    def _details(self) -> str:
        d = self.store.get_details(self.order_id, self.email)
        lines = [f"Order {d['order_id']} for {d['customer_name']}, placed {nice_date(d['placed_at'])}:"]
        lines += [f"• {i['qty']} × {i['name']}: {inr(i['qty'] * i['unit_price'])}" for i in d["items"]]
        shipping = "Free" if not d["shipping_fee"] else inr(d["shipping_fee"])
        lines += [f"Subtotal: {inr(d['subtotal'])}", f"Shipping: {shipping}",
                  f"Total: {inr(d['total'])} (paid by {d['payment_method']})",
                  f"Delivering to: {d['shipping_address']}",
                  f"Status: {d['status'].replace('_', ' ').title()}"]
        for r in d["refunds"]:
            if r["status"] == "CONFIRMED":
                lines.append(f"Refund {r['refund_id']}: {inr(r['amount'])} confirmed.")
        return "\n".join(lines)

    def _refund(self) -> str:
        e = self.store.check_refund_eligibility(self.order_id, self.email)
        if not e["eligible"]:
            return (f"I'm sorry, order {e['order_id']} can't be refunded right now. "
                    f"{e['explanation']}\n\nQuestions? Email support@bkp.example or "
                    "call 1800-000-0000.")
        r = self.store.create_refund_request(self.order_id, self.email,
                                             self.refund_reason or "Requested via chat")
        self.awaiting_refund = r["refund_id"]
        return (f"Order {r['order_id']} qualifies for a refund of {inr(r['amount'])}.\n"
                f"{e['explanation']}\n\n"
                "Shall I go ahead? Reply \"yes\" to confirm or \"no\" to cancel.")

    def _confirm_refund(self) -> str:
        refund_id, self.awaiting_refund = self.awaiting_refund, None
        try:
            c = self.store.confirm_refund(refund_id)
        except StoreError as e:
            return f"Sorry, I couldn't process that refund: {e}"
        extra = (" Since it hadn't shipped, your order is now cancelled."
                 if c["order_status"] == "CANCELLED"
                 else " We'll schedule a pickup for the item within 2 business days.")
        return (f"Done! Refund {c['refund_id']} of {inr(c['amount'])} is confirmed. "
                f"It will go back to your {c['refund_to']} within {c['expected_in']}.{extra}")
