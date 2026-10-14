import pytest

from bkp_agent.store import StoreError


def test_status_requires_matching_email(store):
    s = store.get_status("bkp-10002", "Rahul@Example.com")
    assert s["status"] == "IN_TRANSIT"
    assert s["tracking_number"] == "DLV998877665"
    with pytest.raises(StoreError):
        store.get_status("BKP-10002", "priya@example.com")


def test_unknown_order_and_wrong_email_look_the_same(store):
    with pytest.raises(StoreError) as a:
        store.get_status("BKP-99999", "x@example.com")
    with pytest.raises(StoreError) as b:
        store.get_status("BKP-10001", "x@example.com")
    assert str(a.value).replace("99999", "") == str(b.value).replace("10001", "")


def test_details_totals(store):
    d = store.get_details("BKP-10002", "rahul@example.com")
    assert d["subtotal"] == 5999.0
    assert d["total"] == 6048.0


@pytest.mark.parametrize("order_id,email,eligible,amount", [
    ("BKP-10001", "priya@example.com", True, 3097.0),     # delivered 10 days ago, no shipping fee
    ("BKP-10002", "rahul@example.com", False, 0.0),       # in transit
    ("BKP-10003", "ananya@example.com", True, 2698.0),    # processing: full cancel
    ("BKP-10004", "priya@example.com", False, 0.0),       # delivered > 30 days ago
    ("BKP-10005", "vikram@example.com", False, 0.0),      # already cancelled
])
def test_refund_eligibility(store, order_id, email, eligible, amount):
    e = store.check_refund_eligibility(order_id, email)
    assert e["eligible"] is eligible
    assert e["refund_amount"] == amount


def test_refund_flow(store):
    r = store.create_refund_request("BKP-10003", "ananya@example.com", "Ordered by mistake")
    assert r["status"] == "PENDING"
    again = store.create_refund_request("BKP-10003", "ananya@example.com", "again")
    assert again["refund_id"] == r["refund_id"]  # no duplicate pending refunds

    c = store.confirm_refund(r["refund_id"])
    assert c["status"] == "CONFIRMED"
    assert c["order_status"] == "CANCELLED"
    assert store.get_status("BKP-10003", "ananya@example.com")["status"] == "CANCELLED"

    with pytest.raises(StoreError):
        store.confirm_refund(r["refund_id"])
    with pytest.raises(StoreError):
        store.create_refund_request("BKP-10003", "ananya@example.com", "twice")


def test_delivered_refund_becomes_return(store):
    r = store.create_refund_request("BKP-10001", "priya@example.com", "Sound is bad")
    c = store.confirm_refund(r["refund_id"])
    assert c["order_status"] == "RETURN_REQUESTED"
    assert c["expected_in"] == "1-3 business days"  # UPI


def test_ineligible_refund_request_rejected(store):
    with pytest.raises(StoreError):
        store.create_refund_request("BKP-10002", "rahul@example.com", "too slow")
