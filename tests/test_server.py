import http.cookiejar
import json
import logging
import secrets
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from http.server import ThreadingHTTPServer

import server


class APIClient:
    def __init__(self, base_url):
        self.base_url = base_url
        self.cookies = http.cookiejar.CookieJar()
        self.opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(self.cookies),
            urllib.request.HTTPHandler(),
        )
        self.csrf_token = None

    def request(self, method, path, payload=None, include_csrf=False, origin=None):
        data = None if payload is None else json.dumps(payload).encode("utf-8")
        headers = {}
        if data is not None:
            headers["Content-Type"] = "application/json"
        if method in {"POST", "DELETE"}:
            headers["Origin"] = self.base_url if origin is None else origin
        if include_csrf and self.csrf_token:
            headers["X-CSRF-Token"] = self.csrf_token
        request = urllib.request.Request(
            f"{self.base_url}{path}",
            data=data,
            headers=headers,
            method=method,
        )

        try:
            response = self.opener.open(request)
        except urllib.error.HTTPError as error:
            response = error
        with response:
            content_type = response.headers.get("Content-Type", "")
            body = response.read()
            payload = json.loads(body) if "application/json" in content_type else body
            return response.status, payload, response.headers

    def sign_in(self, email, password):
        status, payload, _ = self.request(
            "POST",
            "/api/login",
            {"email": email, "password": password},
        )
        if status == 200:
            user_status, user_payload, _ = self.request("GET", "/api/me")
            if user_status != 200:
                raise AssertionError(f"Sign-in session was rejected: {user_payload}")
            self.csrf_token = user_payload["csrfToken"]
        return status, payload


class ServerAccessTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary_directory = tempfile.TemporaryDirectory(prefix="wattwise-test-")
        server.DATABASE = Path(cls.temporary_directory.name) / "wattwise.sqlite3"
        server.LOGIN_ATTEMPTS.clear()
        logging.getLogger("wattwise").setLevel(logging.WARNING)
        server.initialize_database()

        cls.admin_email = "admin@example.com"
        cls.admin_password = secrets.token_urlsafe(24)
        cls.customer_email = "customer@example.com"
        cls.customer_password = secrets.token_urlsafe(24)

        with server.connect_database() as connection:
            admin_id = server.create_user(
                connection,
                cls.admin_email,
                "Test Admin",
                cls.admin_password,
                "admin",
            )
            cls.customer_id = server.create_user(
                connection,
                cls.customer_email,
                "Test Customer",
                cls.customer_password,
                "customer",
            )
            server.seed_customer_readings(connection, cls.customer_id)
            cls.admin_id = admin_id

        cls.httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.WattwiseHandler)
        cls.httpd.daemon_threads = True
        cls.thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.thread.start()
        cls.base_url = f"http://127.0.0.1:{cls.httpd.server_port}"

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()
        cls.thread.join(timeout=3)
        cls.temporary_directory.cleanup()

    def setUp(self):
        self.admin = APIClient(self.base_url)
        status, payload = self.admin.sign_in(self.admin_email, self.admin_password)
        self.assertEqual(status, 200, payload)

        self.customer = APIClient(self.base_url)
        status, payload = self.customer.sign_in(self.customer_email, self.customer_password)
        self.assertEqual(status, 200, payload)

    def test_customer_reads_only_their_own_energy(self):
        status, payload, _ = self.customer.request("GET", "/api/energy")
        self.assertEqual(status, 200)
        self.assertEqual(len(payload["readings"]), 30)
        self.assertEqual(payload["readings"][-1]["date"], "2026-10-28")

    def test_health_endpoint_and_remote_setup_guard(self):
        status, payload, _ = self.customer.request("GET", "/api/health")
        self.assertEqual(status, 200)
        self.assertEqual(payload, {"ok": True})
        self.assertTrue(server.is_loopback_client("127.0.0.1"))
        self.assertFalse(server.is_loopback_client("203.0.113.1"))

    def test_customer_cannot_access_admin_data_or_actions(self):
        status, _, _ = self.customer.request("GET", "/api/admin/customers")
        self.assertEqual(status, 403)

        status, _, _ = self.customer.request("POST", "/api/admin/customers", {})
        self.assertEqual(status, 403)

        status, _, _ = self.customer.request(
            "POST",
            "/api/admin/admins",
            {"email": "unauthorized@example.com", "password": "a-unique-password-123"},
            include_csrf=True,
        )
        self.assertEqual(status, 403)

        status, _, _ = self.customer.request(
            "DELETE",
            f"/api/admin/customers/{self.customer_id}",
            include_csrf=True,
        )
        self.assertEqual(status, 403)

    def test_admin_can_create_customer_and_review_private_readings(self):
        email = f"new-{secrets.token_hex(8)}@example.com"
        password = secrets.token_urlsafe(24)
        status, created, _ = self.admin.request(
            "POST",
            "/api/admin/customers",
            {"name": "New Account", "email": email, "password": password},
            include_csrf=True,
        )
        self.assertEqual(status, 201)
        self.assertEqual(created["email"], email)

        status, listing, _ = self.admin.request("GET", "/api/admin/customers")
        self.assertEqual(status, 200)
        self.assertTrue(any(customer["email"] == email for customer in listing["customers"]))

        status, details, _ = self.admin.request(
            "GET",
            f"/api/admin/readings?customer_id={created['id']}",
        )
        self.assertEqual(status, 200)
        self.assertEqual(details["customer"]["email"], email)
        self.assertEqual(len(details["readings"]), 30)

        new_customer = APIClient(self.base_url)
        status, _ = new_customer.sign_in(email, password)
        self.assertEqual(status, 200)
        status, readings, _ = new_customer.request("GET", "/api/energy")
        self.assertEqual(status, 200)
        self.assertEqual(len(readings["readings"]), 30)
        _, existing_readings, _ = self.customer.request("GET", "/api/energy")
        self.assertNotEqual(
            [(reading["usage"], reading["solar"]) for reading in readings["readings"]],
            [
                (reading["usage"], reading["solar"])
                for reading in existing_readings["readings"]
            ],
        )

    def test_admin_can_create_another_admin_account(self):
        email = f"admin-{secrets.token_hex(8)}@example.com"
        password = secrets.token_urlsafe(24)
        status, created, _ = self.admin.request(
            "POST",
            "/api/admin/admins",
            {"email": email, "password": password},
            include_csrf=True,
        )
        self.assertEqual(status, 201)
        self.assertEqual(created["email"], email)
        self.assertEqual(created["name"], "Additional Administrator")

        other_admin = APIClient(self.base_url)
        status, payload = other_admin.sign_in(email, password)
        self.assertEqual(status, 200, payload)
        status, account, _ = other_admin.request("GET", "/api/me")
        self.assertEqual(status, 200)
        self.assertEqual(account["user"]["role"], "admin")
        status, _, _ = other_admin.request("GET", "/api/admin/customers")
        self.assertEqual(status, 200)

    def test_admin_mutations_require_a_valid_csrf_token(self):
        status, _, _ = self.admin.request(
            "POST",
            "/api/admin/customers",
            {"name": "No CSRF", "email": "nocsrf@example.com", "password": "test-password-123"},
        )
        self.assertEqual(status, 403)

        status, _, _ = self.admin.request(
            "POST",
            "/api/admin/admins",
            {"email": "nocsrf-admin@example.com", "password": "test-password-123"},
        )
        self.assertEqual(status, 403)

        status, listing, _ = self.admin.request("GET", "/api/admin/customers")
        self.assertEqual(status, 200)
        self.assertFalse(any(customer["email"] == "nocsrf@example.com" for customer in listing["customers"]))

    def test_password_reset_revokes_existing_customer_session(self):
        new_password = secrets.token_urlsafe(24)
        status, payload, _ = self.admin.request(
            "POST",
            f"/api/admin/customers/{self.customer_id}/password",
            {"password": new_password},
            include_csrf=True,
        )
        self.assertEqual(status, 200)

        status, _, _ = self.customer.request("GET", "/api/me")
        self.assertEqual(status, 401)

        status, _ = APIClient(self.base_url).sign_in(self.customer_email, self.customer_password)
        self.assertEqual(status, 401)

        new_customer = APIClient(self.base_url)
        status, _ = new_customer.sign_in(self.customer_email, new_password)
        self.assertEqual(status, 200)
        type(self).customer_password = new_password

    def test_invalid_or_duplicate_customer_accounts_are_rejected(self):
        status, _, _ = self.admin.request(
            "POST",
            "/api/admin/customers",
            {"name": "Invalid Email", "email": "invalid", "password": "test-password-123"},
            include_csrf=True,
        )
        self.assertEqual(status, 400)

        status, _, _ = self.admin.request(
            "POST",
            "/api/admin/customers",
            {"name": "Short Password", "email": "short@example.com", "password": "short"},
            include_csrf=True,
        )
        self.assertEqual(status, 400)

        status, _, _ = self.admin.request(
            "POST",
            "/api/admin/customers",
            {"name": "Duplicate", "email": self.customer_email, "password": "test-password-123"},
            include_csrf=True,
        )
        self.assertEqual(status, 409)

    def test_account_setup_creates_configured_roles_and_is_idempotent(self):
        original_database = server.DATABASE
        server.DATABASE = Path(self.temporary_directory.name) / "account-setup.sqlite3"
        try:
            server.initialize_database()
            client = APIClient(self.base_url)
            status, setup_status, _ = client.request("GET", "/api/setup-status")
            self.assertEqual(status, 200)
            self.assertTrue(setup_status["setupRequired"])

            admin_password = secrets.token_urlsafe(24)
            customer_password = secrets.token_urlsafe(24)
            status, _, _ = client.request(
                "POST",
                "/api/setup",
                {
                    "adminPassword": admin_password,
                    "customerPassword": customer_password,
                },
                origin="http://attacker.example",
            )
            self.assertEqual(status, 403)

            status, _, _ = client.request(
                "POST",
                "/api/setup",
                {
                    "adminPassword": admin_password,
                    "customerPassword": admin_password,
                },
            )
            self.assertEqual(status, 400)
            status, setup_status, _ = client.request("GET", "/api/setup-status")
            self.assertTrue(setup_status["setupRequired"])

            status, created, _ = client.request(
                "POST",
                "/api/setup",
                {
                    "adminPassword": admin_password,
                    "customerPassword": customer_password,
                },
            )
            self.assertEqual(status, 201)
            self.assertTrue(created["ok"])

            with server.connect_database() as connection:
                configured_admin = connection.execute(
                    "SELECT id, role FROM users WHERE email = ?",
                    (server.ADMIN_EMAIL,),
                ).fetchone()
                configured_customer = connection.execute(
                    "SELECT id, role FROM users WHERE email = ?",
                    (server.CUSTOMER_EMAIL,),
                ).fetchone()
                seeded_readings = connection.execute(
                    "SELECT COUNT(*) FROM energy_readings WHERE user_id = ?",
                    (configured_customer["id"],),
                ).fetchone()[0]

            self.assertEqual(configured_admin["role"], "admin")
            self.assertEqual(configured_customer["role"], "customer")
            self.assertEqual(seeded_readings, 30)

            status, setup_status, _ = client.request("GET", "/api/setup-status")
            self.assertEqual(status, 200)
            self.assertFalse(setup_status["setupRequired"])

            status, _, _ = client.request(
                "POST",
                "/api/setup",
                {
                    "adminPassword": admin_password,
                    "customerPassword": customer_password,
                },
            )
            self.assertEqual(status, 409)

            admin = APIClient(self.base_url)
            status, account = admin.sign_in(server.ADMIN_EMAIL, admin_password)
            self.assertEqual(status, 200)
            self.assertEqual(account["role"], "admin")

            customer = APIClient(self.base_url)
            status, account = customer.sign_in(server.CUSTOMER_EMAIL, customer_password)
            self.assertEqual(status, 200)
            self.assertEqual(account["role"], "customer")
        finally:
            server.DATABASE = original_database

    def test_setup_page_and_customer_dashboard_are_served_to_correct_roles(self):
        setup_database = Path(self.temporary_directory.name) / "account-setup.sqlite3"
        original_database = server.DATABASE
        server.DATABASE = setup_database
        try:
            server.initialize_database()
            status, body, headers = APIClient(self.base_url).request("GET", "/setup")
            self.assertEqual(status, 200)
            self.assertIn(b"Create both project accounts", body)
            self.assertIn("text/html", headers["Content-Type"])
        finally:
            server.DATABASE = original_database

        status, body, headers = self.customer.request("GET", "/dashboard")
        self.assertEqual(status, 200)
        self.assertIn(b"customer-first-name", body)
        self.assertIn("text/html", headers["Content-Type"])

        status, _, _ = self.customer.request("GET", "/admin")
        self.assertEqual(status, 200)
        with self.customer.opener.open(f"{self.base_url}/admin") as response:
            self.assertEqual(response.geturl(), f"{self.base_url}/dashboard")

    def test_logout_invalidates_customer_session(self):
        status, _, _ = self.customer.request(
            "POST",
            "/api/logout",
            {},
            include_csrf=True,
        )
        self.assertEqual(status, 200)
        status, _, _ = self.customer.request("GET", "/api/energy")
        self.assertEqual(status, 401)


if __name__ == "__main__":
    unittest.main()
