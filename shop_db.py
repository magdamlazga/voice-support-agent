"""
The BrightCart shop database and the actions the support agent can take on it.

Every business rule (30-day return window, no refunds on opened electronics,
vouchers of at most 10%, identity check) is enforced HERE, in code, not only
in the agent's prompt. Even if a customer talks the model into trying
something it shouldn't, the tool refuses.
"""

import random
import sqlite3
import string
from contextlib import contextmanager
from datetime import date, datetime, timedelta
from pathlib import Path

DB_PATH = Path(__file__).parent / "shop.db"

RETURN_WINDOW_DAYS = 30
MAX_VOUCHER_PERCENT = 10


@contextmanager
def _connect():
    """Open a connection, commit on success, and always close it (Windows keeps unclosed files locked)."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def reset_database():
    """Create (or recreate) the database with sample orders. Dates are relative to today."""
    today = date.today()

    def days_ago(n):
        return (today - timedelta(days=n)).isoformat()

    def days_ahead(n):
        return (today + timedelta(days=n)).isoformat()

    orders = [
        # order_number, email, name, item, category, price, status, order_date, expected_delivery, delivered_date
        ("BC-1001", "anna.kowalska@example.com", "Anna Kowalska", "Wireless headphones", "electronics", 89.99,
         "delivered", days_ago(15), days_ago(10), days_ago(10)),
        ("BC-1002", "anna.kowalska@example.com", "Anna Kowalska", "Bluetooth speaker", "electronics", 49.99,
         "delivered", days_ago(16), days_ago(12), days_ago(12)),
        ("BC-1003", "james.miller@example.com", "James Miller", "Running shoes", "clothing", 120.00,
         "delivered", days_ago(50), days_ago(45), days_ago(45)),
        ("BC-1004", "james.miller@example.com", "James Miller", "Coffee maker", "home", 75.50,
         "shipped", days_ago(12), days_ago(5), None),
        ("BC-1005", "sofia.rossi@example.com", "Sofia Rossi", "Yoga mat", "sport", 29.90,
         "processing", days_ago(1), days_ahead(3), None),
    ]

    with _connect() as conn:
        conn.executescript("""
            DROP TABLE IF EXISTS orders;
            DROP TABLE IF EXISTS vouchers;
            DROP TABLE IF EXISTS handoffs;
            CREATE TABLE orders (
                order_number TEXT PRIMARY KEY,
                email TEXT NOT NULL,
                customer_name TEXT NOT NULL,
                item TEXT NOT NULL,
                category TEXT NOT NULL,
                price REAL NOT NULL,
                status TEXT NOT NULL,
                order_date TEXT NOT NULL,
                expected_delivery TEXT NOT NULL,
                delivered_date TEXT,
                refund_status TEXT NOT NULL DEFAULT 'none'
            );
            CREATE TABLE vouchers (
                code TEXT PRIMARY KEY,
                order_number TEXT NOT NULL,
                percent INTEGER NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE TABLE handoffs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                order_number TEXT,
                summary TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
        """)
        conn.executemany(
            "INSERT INTO orders (order_number, email, customer_name, item, category, price, status, "
            "order_date, expected_delivery, delivered_date) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            orders,
        )


def ensure_database():
    if not DB_PATH.exists():
        reset_database()


# ---------- helpers ----------

def normalize_order_number(raw):
    """Accept spoken or typed variants: 'bc 1001', 'BC1001', 'B C - 1001' -> 'BC-1001'."""
    if not raw:
        return ""
    cleaned = "".join(ch for ch in str(raw).upper() if ch.isalnum())
    if cleaned.startswith("BC"):
        cleaned = cleaned[2:]
    return f"BC-{cleaned}" if cleaned else ""


def normalize_email(raw):
    return (raw or "").strip().lower().replace(" ", "")


def _verified_order(conn, order_number, email):
    """Return the order row only if the order number AND email match. Otherwise None."""
    row = conn.execute(
        "SELECT * FROM orders WHERE order_number = ?", (normalize_order_number(order_number),)
    ).fetchone()
    if row is None or row["email"] != normalize_email(email):
        return None
    return row


NOT_VERIFIED = {
    "ok": False,
    "message": "No order matches that order number and email. Ask the customer to double-check both. "
               "Do not reveal whether the order number exists.",
}


def _is_late(row, today):
    expected = date.fromisoformat(row["expected_delivery"])
    if row["delivered_date"]:
        return date.fromisoformat(row["delivered_date"]) > expected
    return today > expected


# ---------- tools the agent can call ----------

def get_order_status(order_number, email):
    with _connect() as conn:
        row = _verified_order(conn, order_number, email)
        if row is None:
            return NOT_VERIFIED
        return {
            "ok": True,
            "order_number": row["order_number"],
            "customer_name": row["customer_name"],
            "item": row["item"],
            "category": row["category"],
            "price": row["price"],
            "status": row["status"],
            "order_date": row["order_date"],
            "expected_delivery": row["expected_delivery"],
            "delivered_date": row["delivered_date"],
            "refund_status": row["refund_status"],
            "is_late": _is_late(row, date.today()),
            "today": date.today().isoformat(),
        }


def request_refund(order_number, email, reason, item_opened):
    today = date.today()
    with _connect() as conn:
        row = _verified_order(conn, order_number, email)
        if row is None:
            return NOT_VERIFIED
        if row["refund_status"] != "none":
            return {"ok": False, "message": f"A refund for this order is already {row['refund_status']}."}
        if row["status"] != "delivered":
            return {"ok": False, "message": "The order has not been delivered yet, so it can't be refunded. "
                                            "Offer to hand the customer over to a human agent if they want to cancel."}
        days_since = (today - date.fromisoformat(row["delivered_date"])).days
        if days_since > RETURN_WINDOW_DAYS:
            return {"ok": False, "message": f"Delivered {days_since} days ago, outside the "
                                            f"{RETURN_WINDOW_DAYS}-day return window. Refund not allowed."}
        if row["category"] == "electronics" and item_opened:
            return {"ok": False, "message": "Opened electronics can't be refunded under the return policy."}

        conn.execute("UPDATE orders SET refund_status = 'approved' WHERE order_number = ?", (row["order_number"],))
        return {
            "ok": True,
            "message": f"Refund of ${row['price']:.2f} approved for {row['item']}. "
                       "The money is returned to the original payment method within 5 business days.",
            "reason_recorded": reason,
        }


def issue_late_delivery_voucher(order_number, email, percent):
    with _connect() as conn:
        row = _verified_order(conn, order_number, email)
        if row is None:
            return NOT_VERIFIED
        if not _is_late(row, date.today()):
            return {"ok": False, "message": "This order is not late, so no voucher can be issued."}
        try:
            percent = int(percent)
        except (TypeError, ValueError):
            return {"ok": False, "message": "Percent must be a whole number."}
        if percent < 1 or percent > MAX_VOUCHER_PERCENT:
            return {"ok": False, "message": f"Vouchers must be between 1% and {MAX_VOUCHER_PERCENT}%."}
        existing = conn.execute(
            "SELECT code, percent FROM vouchers WHERE order_number = ?", (row["order_number"],)
        ).fetchone()
        if existing:
            return {"ok": False, "message": f"A {existing['percent']}% voucher ({existing['code']}) "
                                            "was already issued for this order."}
        code = "SORRY-" + "".join(random.choices(string.ascii_uppercase + string.digits, k=6))
        conn.execute(
            "INSERT INTO vouchers (code, order_number, percent, created_at) VALUES (?, ?, ?, ?)",
            (code, row["order_number"], percent, datetime.now().isoformat(timespec="seconds")),
        )
        return {"ok": True, "voucher_code": code, "percent": percent,
                "message": f"{percent}% voucher {code} issued and emailed to the customer."}


def escalate_to_human(summary, order_number=None):
    with _connect() as conn:
        cursor = conn.execute(
            "INSERT INTO handoffs (order_number, summary, created_at) VALUES (?, ?, ?)",
            (normalize_order_number(order_number) or None, summary, datetime.now().isoformat(timespec="seconds")),
        )
        return {"ok": True, "handoff_id": cursor.lastrowid,
                "message": f"Handed over to a human agent (case #{cursor.lastrowid}). "
                           "They will call back within one business day."}


# ---------- read-only views for the UI ----------

def table(name):
    """Return (headers, rows) for one table, for display in the UI."""
    with _connect() as conn:
        cursor = conn.execute(f"SELECT * FROM {name}")  # name comes from our own code, not user input
        headers = [col[0] for col in cursor.description]
        return headers, [list(r) for r in cursor.fetchall()]
