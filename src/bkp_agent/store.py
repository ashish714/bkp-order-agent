"""Order and refund data for BKP, backed by SQLite.

Every customer-facing lookup requires the order ID *and* the email it was
placed with, so the agent can never reveal an order to someone who only
guessed an order number.
"""
from __future__ import annotations

import json
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import date, datetime
from pathlib import Path

REFUND_WINDOW_DAYS = 30

SCHEMA = """
CREATE TABLE IF NOT EXISTS orders (
    order_id TEXT PRIMARY KEY,
    email TEXT NOT NULL,
    status TEXT NOT NULL,
    data TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS refunds (
    refund_id TEXT PRIMARY KEY,
    order_id TEXT NOT NULL,
    amount REAL NOT NULL,
    reason TEXT NOT NULL,
    status TEXT NOT NULL,
    created_at TEXT NOT NULL,
    confirmed_at TEXT
);
"""


class StoreError(Exception):
    """A customer-facing error (unknown order, wrong email, not eligible...)."""


class OrderStore:
    def __init__(self, path: str | Path, seed_file: str | Path | None = None,
                 today: date | None = None):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._today = today
        with self._conn() as conn:
            conn.executescript(SCHEMA)
            empty = conn.execute("SELECT COUNT(*) FROM orders").fetchone()[0] == 0
        if empty and seed_file:
            self.seed(json.loads(Path(seed_file).read_text()))

    @property
    def today(self) -> date:
        return self._today or date.today()

    @contextmanager
    def _conn(self):
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def seed(self, orders: list[dict]) -> None:
        with self._conn() as conn:
            for o in orders:
                conn.execute(
                    "INSERT OR REPLACE INTO orders (order_id, email, status, data) "
                    "VALUES (?, ?, ?, ?)",
                    (o["order_id"].upper(), o["email"].lower(), o["status"], json.dumps(o)),
                )

    # ---- lookups ---------------------------------------------------------

    def _load(self, order_id: str, email: str) -> dict:
        order_id = order_id.strip().upper()
        with self._conn() as conn:
            row = conn.execute(
                "SELECT email, status, data FROM orders WHERE order_id=?", (order_id,)
            ).fetchone()
        # Same message for "no such order" and "wrong email" so the agent
        # can't be used to probe which order IDs exist.
        if row is None or row["email"] != email.strip().lower():
            raise StoreError(
                f"No order {order_id} found for that email. Ask the customer to "
                "double-check the order ID and the email used at checkout."
            )
        order = json.loads(row["data"])
        order["status"] = row["status"]
        return order

    def get_status(self, order_id: str, email: str) -> dict:
        o = self._load(order_id, email)
        return {
            "order_id": o["order_id"],
            "status": o["status"],
            "carrier": o["carrier"],
            "tracking_number": o["tracking_number"],
            "estimated_delivery": o["estimated_delivery"],
            "delivered_at": o["delivered_at"],
            "latest_event": o["events"][-1] if o["events"] else None,
            "timeline": o["events"],
        }

    def get_details(self, order_id: str, email: str) -> dict:
        o = self._load(order_id, email)
        subtotal = sum(i["qty"] * i["unit_price"] for i in o["items"])
        return {
            "order_id": o["order_id"],
            "customer_name": o["customer_name"],
            "placed_at": o["placed_at"],
            "status": o["status"],
            "items": o["items"],
            "subtotal": subtotal,
            "shipping_fee": o["shipping_fee"],
            "total": subtotal + o["shipping_fee"],
            "payment_method": o["payment_method"],
            "shipping_address": o["shipping_address"],
            "refunds": self._refunds_for(o["order_id"]),
        }

    # ---- refunds ---------------------------------------------------------

    def _refunds_for(self, order_id: str) -> list[dict]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT refund_id, amount, reason, status, created_at, confirmed_at "
                "FROM refunds WHERE order_id=? ORDER BY created_at", (order_id,)
            ).fetchall()
        return [dict(r) for r in rows]

    def check_refund_eligibility(self, order_id: str, email: str) -> dict:
        o = self._load(order_id, email)
        subtotal = sum(i["qty"] * i["unit_price"] for i in o["items"])
        result = {"order_id": o["order_id"], "status": o["status"],
                  "eligible": False, "refund_amount": 0.0, "explanation": ""}

        if any(r["status"] == "CONFIRMED" for r in self._refunds_for(o["order_id"])):
            result["explanation"] = "A refund has already been confirmed for this order."
        elif o["status"] == "CANCELLED":
            result["explanation"] = "This order was cancelled and refunded automatically."
        elif o["status"] in ("SHIPPED", "IN_TRANSIT"):
            result["explanation"] = (
                "The order is on its way, so it can't be refunded until it's delivered. "
                "You can refuse the delivery, or request a return after it arrives."
            )
        elif o["status"] == "PROCESSING":
            result.update(eligible=True, refund_amount=subtotal + o["shipping_fee"],
                          explanation="It hasn't shipped yet, so you get a full refund including shipping.")
        elif o["status"] == "DELIVERED":
            delivered = datetime.fromisoformat(o["delivered_at"]).date()
            days = (self.today - delivered).days
            if days <= REFUND_WINDOW_DAYS:
                result.update(
                    eligible=True, refund_amount=subtotal,
                    explanation=(f"It was delivered {days} day(s) ago, within the "
                                 f"{REFUND_WINDOW_DAYS}-day return window. You get the item price "
                                 "back (the shipping fee isn't refundable), and we'll schedule a "
                                 "pickup for the item."),
                )
            else:
                result["explanation"] = (
                    f"It was delivered {days} days ago, outside the {REFUND_WINDOW_DAYS}-day return window."
                )
        else:
            result["explanation"] = f"Orders with status {o['status']} can't be refunded."
        return result

    def create_refund_request(self, order_id: str, email: str, reason: str) -> dict:
        """Create a PENDING refund. Nothing is paid out until confirm_refund."""
        elig = self.check_refund_eligibility(order_id, email)
        if not elig["eligible"]:
            raise StoreError(f"Not eligible for a refund: {elig['explanation']}")
        oid = elig["order_id"]
        with self._conn() as conn:
            pending = conn.execute(
                "SELECT refund_id, amount FROM refunds WHERE order_id=? AND status='PENDING'",
                (oid,),
            ).fetchone()
            if pending:
                return {"refund_id": pending["refund_id"], "order_id": oid,
                        "amount": pending["amount"], "status": "PENDING",
                        "note": "A pending refund request already exists for this order."}
            refund_id = "RF-" + uuid.uuid4().hex[:8].upper()
            conn.execute(
                "INSERT INTO refunds (refund_id, order_id, amount, reason, status, created_at) "
                "VALUES (?, ?, ?, ?, 'PENDING', ?)",
                (refund_id, oid, elig["refund_amount"], reason, datetime.now().isoformat()),
            )
        return {"refund_id": refund_id, "order_id": oid, "amount": elig["refund_amount"],
                "status": "PENDING", "explanation": elig["explanation"]}

    def cancel_refund_request(self, refund_id: str) -> None:
        """Drop a PENDING refund the customer decided not to go ahead with."""
        with self._conn() as conn:
            conn.execute(
                "UPDATE refunds SET status='WITHDRAWN' WHERE refund_id=? AND status='PENDING'",
                (refund_id.strip().upper(),),
            )

    def confirm_refund(self, refund_id: str) -> dict:
        refund_id = refund_id.strip().upper()
        with self._conn() as conn:
            row = conn.execute(
                "SELECT r.order_id, r.amount, r.status, o.status AS order_status, o.data "
                "FROM refunds r JOIN orders o ON o.order_id = r.order_id "
                "WHERE r.refund_id=?", (refund_id,),
            ).fetchone()
            if row is None:
                raise StoreError(f"No refund request {refund_id} found.")
            if row["status"] != "PENDING":
                raise StoreError(f"Refund {refund_id} is already {row['status']}.")
            new_status = "CANCELLED" if row["order_status"] == "PROCESSING" else "RETURN_REQUESTED"
            conn.execute(
                "UPDATE refunds SET status='CONFIRMED', confirmed_at=? WHERE refund_id=?",
                (datetime.now().isoformat(), refund_id),
            )
            conn.execute("UPDATE orders SET status=? WHERE order_id=?",
                         (new_status, row["order_id"]))
        payment = json.loads(row["data"])["payment_method"]
        eta = "1-3 business days" if payment == "UPI" else "5-7 business days"
        return {"refund_id": refund_id, "order_id": row["order_id"], "amount": row["amount"],
                "status": "CONFIRMED", "order_status": new_status,
                "refund_to": payment, "expected_in": eta}
