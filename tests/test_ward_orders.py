import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from sqlalchemy.engine import URL


class WardOrderTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        database_path = str(Path(self.temp_dir.name) / "platter-ward-test.db")
        with patch.dict(
            os.environ,
            {"PLATTER_ENV": "development", "DATABASE_URL": "sqlite://"},
        ):
            from app import app as base_app, create_app
            from app.extensions import db
            from app.models import MealOrder, RestaurantTable

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
                },
            )
        self.db = db
        self.MealOrder = MealOrder
        self.RestaurantTable = RestaurantTable
        self.client = self.app.test_client()
        with self.client.session_transaction() as session:
            session["user_id"] = 1
            session["role"] = "cashier"
        with self.app.app_context():
            self.db.create_all()
            table = self.RestaurantTable.query.filter_by(label="TEST-1").first()
            if table is None:
                table = self.RestaurantTable(label="TEST-1", capacity=4, status="free")
                self.db.session.add(table)
                self.db.session.commit()
            self.table_id = table.id

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

    def test_ward_delivery_creates_patient_free_ticket_without_occupying_table(self):
        response = self.client.post(
            f"/orders/open/{self.table_id}",
            data={
                "party_type": "patient",
                "ward_delivery": "1",
                "ward_name": "Medical Ward",
                "meal_slot": "lunch",
                "diet_type": "Soft meals",
                "notes": "Ward-level note",
            },
        )

        self.assertEqual(response.status_code, 302)
        self.assertIn("/orders/", response.location)
        with self.app.app_context():
            order = self.MealOrder.query.one()
            self.assertEqual(order.order_type, "ward")
            self.assertEqual(order.party_type, "patient")
            self.assertEqual(order.ward_name, "Medical Ward")
            self.assertEqual(order.meal_slot, "lunch")
            self.assertEqual(order.diet_type, "Soft meals")
            self.assertEqual(order.notes, "Ward-level note")
            self.assertIsNone(order.patient_name)
            self.assertIsNone(order.external_patient_id)
            self.assertIsNone(order.table_id)
            table = self.db.session.get(self.RestaurantTable, self.table_id)
            self.assertEqual(table.status, "free")

    def test_invalid_meal_slot_does_not_create_order(self):
        response = self.client.post(
            f"/orders/open/{self.table_id}",
            data={
                "party_type": "patient",
                "ward_delivery": "1",
                "ward_name": "Medical Ward",
                "meal_slot": "midnight",
            },
        )

        self.assertEqual(response.status_code, 302)
        with self.app.app_context():
            self.assertEqual(self.MealOrder.query.count(), 0)

    def test_patient_table_order_still_uses_selected_table(self):
        response = self.client.post(
            f"/orders/open/{self.table_id}",
            data={
                "party_type": "patient",
                "ward_name": "Medical Ward",
                "patient_name": "Bed 4",
            },
        )

        self.assertEqual(response.status_code, 302)
        with self.app.app_context():
            order = self.MealOrder.query.one()
            self.assertEqual(order.order_type, "dine_in")
            self.assertEqual(order.table_id, self.table_id)
            self.assertEqual(order.patient_name, "Bed 4")
            self.assertIsNone(order.meal_slot)
            table = self.db.session.get(self.RestaurantTable, self.table_id)
            self.assertEqual(table.status, "occupied")

    def test_tables_page_uses_authenticated_hmis_ward_list(self):
        self.app.config.update(
            HMIS_API_BASE_URL="https://hmis.example.test",
            HMIS_API_TOKEN="ward-api-test-token-for-integration-1234567890",
        )
        hmis_response = Mock()
        hmis_response.json.return_value = {
            "wards": [{"name": "Medical Ward", "department": "Medicine"}]
        }

        with patch("app.routes.tables.requests.get", return_value=hmis_response) as get:
            response = self.client.get("/tables")

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Medical Ward", response.data)
        self.assertIn(b'name="ward_delivery"', response.data)
        self.assertIn(b'<select name="ward_name"', response.data)
        html = response.data.decode()
        delivery_fields = html.split('class="ward-delivery-fields d-none"', 1)[1]
        self.assertIn('<select name="ward_name"', delivery_fields)
        self.assertNotIn(b"/orders/ward", response.data)
        get.assert_called_once_with(
            "https://hmis.example.test/api/v1/integrations/platter/wards",
            headers={
                "Authorization": "Bearer ward-api-test-token-for-integration-1234567890"
            },
            timeout=(2, 4),
        )

    def test_tables_page_keeps_manual_ward_entry_when_hmis_is_unconfigured(self):
        self.app.config.update(HMIS_API_BASE_URL="", HMIS_API_TOKEN="")

        response = self.client.get("/tables")

        self.assertEqual(response.status_code, 200)
        self.assertIn(b'name="ward_name"', response.data)
        self.assertIn(b"HMIS ward lookup is not configured", response.data)
        self.assertIn(b'type="text" name="ward_name"', response.data)


if __name__ == "__main__":
    unittest.main()
