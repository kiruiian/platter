from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
import os
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from sqlalchemy.engine import URL


class CashierPaymentTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        database_path = str(Path(self.temp_dir.name) / "platter-test.db")
        with patch.dict(
            os.environ,
            {"PLATTER_ENV": "development", "DATABASE_URL": "sqlite://"},
        ):
            from app import app as base_app, create_app
            from app.extensions import db
            from app.models import MealOrder, Payment

            type(self).base_app = base_app
            type(self).db = db
            self.app = create_app(
                {
                    "TESTING": True,
                    "ENVIRONMENT": "development",
                    "SQLALCHEMY_DATABASE_URI": URL.create(
                        "sqlite",
                        database=database_path,
                    ),
                    "SQLALCHEMY_ENGINE_OPTIONS": {
                        "connect_args": {"timeout": 10},
                    },
                },
            )
        self.db = db
        self.MealOrder = MealOrder
        self.Payment = Payment
        self.client = self._client_as_cashier()
        with self.app.app_context():
            self.db.create_all()

    def tearDown(self):
        with self.app.app_context():
            self.db.session.remove()
            self.db.engine.dispose()
        self.temp_dir.cleanup()

    @classmethod
    def tearDownClass(cls):
        if hasattr(cls, "base_app"):
            with cls.base_app.app_context():
                cls.db.session.remove()
                cls.db.engine.dispose()

    def _create_order(self, total=Decimal("0.30"), payments=()):
        with self.app.app_context():
            order = self.MealOrder(
                order_number="TEST-001",
                order_type="dine_in",
                status="ready",
                party_type="customer",
                total=total,
            )
            self.db.session.add(order)
            self.db.session.flush()
            for amount, method, result_desc in payments:
                self.db.session.add(
                    self.Payment(
                        meal_order_id=order.id,
                        amount=amount,
                        method=method,
                        result_desc=result_desc,
                    )
                )
            self.db.session.commit()
            return order.id

    def _client_as_cashier(self):
        client = self.app.test_client()
        with client.session_transaction() as session:
            session["user_id"] = 1
            session["role"] = "cashier"
        return client

    def _pay(self, client, order_id, **form):
        return client.post(f"/cashier/{order_id}/pay", data=form)

    def test_partial_payments_use_exact_decimal_balance(self):
        order_id = self._create_order(
            payments=[(Decimal("0.10"), "card", None)]
        )

        response = self._pay(
            self.client,
            order_id,
            method="card",
            amount="0.20",
        )

        self.assertEqual(response.status_code, 302)
        self.assertIn("/cashier/receipts/", response.location)
        with self.app.app_context():
            order = self.db.session.get(self.MealOrder, order_id)
            self.assertEqual(order.status, "closed")
            self.assertEqual(
                [payment.amount for payment in order.payments],
                [Decimal("0.10"), Decimal("0.20")],
            )

    def test_payment_cannot_exceed_exact_remaining_balance(self):
        order_id = self._create_order(
            payments=[(Decimal("0.20"), "card", None)]
        )

        response = self._pay(
            self.client,
            order_id,
            method="card",
            amount="0.11",
        )

        self.assertEqual(response.status_code, 302)
        with self.app.app_context():
            order = self.db.session.get(self.MealOrder, order_id)
            self.assertEqual(order.status, "ready")
            self.assertEqual(len(order.payments), 1)

    def test_summary_reports_render_decimal_totals(self):
        for path in ("/reports/daily-summary", "/reports/monthly-summary"):
            with self.subTest(path=path):
                response = self.client.get(path)
                self.assertEqual(response.status_code, 200)

    def test_pending_stk_payment_blocks_manual_payment(self):
        order_id = self._create_order(
            payments=[(Decimal("0.30"), "mpesa_stk", "pending")]
        )

        response = self._pay(
            self.client,
            order_id,
            method="cash",
            tendered="0.30",
        )

        self.assertEqual(response.status_code, 302)
        with self.app.app_context():
            self.assertEqual(
                self.Payment.query.filter_by(meal_order_id=order_id).count(),
                1,
            )

    def test_stk_callback_closes_order_with_exact_amount(self):
        order_id = self._create_order(total=Decimal("10.00"))
        with self.app.app_context():
            self.db.session.add(
                self.Payment(
                    meal_order_id=order_id,
                    amount=Decimal("10.00"),
                    method="mpesa_stk",
                    checkout_request_id="TEST-CHECKOUT",
                    result_desc="pending",
                )
            )
            self.db.session.commit()

        response = self.client.post(
            "/mpesa/stk-callback",
            json={
                "Body": {
                    "stkCallback": {
                        "CheckoutRequestID": "TEST-CHECKOUT",
                        "ResultCode": 0,
                        "ResultDesc": "Success",
                        "CallbackMetadata": {
                            "Item": [
                                {"Name": "Amount", "Value": 10},
                                {"Name": "MpesaReceiptNumber", "Value": "ABC123"}
                            ]
                        },
                    }
                }
            },
        )

        self.assertEqual(response.status_code, 200)
        with self.app.app_context():
            order = self.db.session.get(self.MealOrder, order_id)
            self.assertEqual(order.status, "closed")
            self.assertEqual(order.payments[0].amount, Decimal("10.00"))

    def test_stk_callback_amount_mismatch_does_not_confirm_payment(self):
        order_id = self._create_order(total=Decimal("10.00"))
        with self.app.app_context():
            self.db.session.add(
                self.Payment(
                    meal_order_id=order_id,
                    amount=Decimal("10.00"),
                    method="mpesa_stk",
                    checkout_request_id="TEST-CHECKOUT",
                    result_desc="pending",
                )
            )
            self.db.session.commit()

        self.client.post(
            "/mpesa/stk-callback",
            json={
                "Body": {
                    "stkCallback": {
                        "CheckoutRequestID": "TEST-CHECKOUT",
                        "ResultCode": 0,
                        "ResultDesc": "Success",
                        "CallbackMetadata": {
                            "Item": [
                                {"Name": "Amount", "Value": 11},
                                {"Name": "MpesaReceiptNumber", "Value": "ABC123"},
                            ]
                        },
                    }
                }
            },
        )

        with self.app.app_context():
            order = self.db.session.get(self.MealOrder, order_id)
            self.assertEqual(order.status, "ready")
            self.assertEqual(order.payments[0].result_desc, "pending")

    def test_concurrent_payments_do_not_overpay_order(self):
        order_id = self._create_order(total=Decimal("1.00"))
        start = threading.Barrier(2)

        def make_payment():
            client = self._client_as_cashier()
            start.wait()
            return self._pay(
                client,
                order_id,
                method="card",
                amount="0.60",
            ).status_code

        with ThreadPoolExecutor(max_workers=2) as executor:
            statuses = list(executor.map(lambda _: make_payment(), range(2)))

        self.assertEqual(statuses, [302, 302])
        with self.app.app_context():
            order = self.db.session.get(self.MealOrder, order_id)
            total_paid = sum(
                (payment.amount for payment in order.payments),
                Decimal("0.00"),
            )
            self.assertEqual(total_paid, Decimal("0.60"))
            self.assertEqual(order.status, "ready")


if __name__ == "__main__":
    unittest.main()
