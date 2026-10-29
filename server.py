#!/usr/bin/env python3
"""Local web server and role-protected API for the Wattwise dashboard."""

import argparse
import csv
import getpass
import hashlib
import hmac
import json
import logging
import os
import re
import secrets
import sqlite3
import threading
import time
from collections import defaultdict, deque
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from http import HTTPStatus
from http.cookies import CookieError, SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from ipaddress import ip_address
from math import isfinite
from pathlib import Path
from urllib.parse import parse_qs, urlsplit


ROOT = Path(__file__).resolve().parent
DATABASE = Path(os.environ.get("WATTWISE_DATABASE", ROOT / "instance" / "wattwise.sqlite3"))
SAMPLE_DATA = ROOT / "data" / "energy_usage.csv"
ADMIN_EMAIL = "divyanshusingh31113@gmail.com"
CUSTOMER_EMAIL = "thehorrorofcentury@gmail.com"
SESSION_COOKIE = "wattwise_session"
SESSION_HOURS = 8
PASSWORD_ITERATIONS = 310_000
MAX_REQUEST_BYTES = 16_384
EMAIL_PATTERN = re.compile(r"^[^@\s]{1,64}@[^@\s]{1,190}\.[^@\s]{2,}$")
LOGIN_ATTEMPTS = defaultdict(deque)
LOGIN_ATTEMPTS_LOCK = threading.Lock()

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
LOGGER = logging.getLogger("wattwise")


class RequestError(Exception):
    def __init__(self, status, message):
        super().__init__(message)
        self.status = status


def utc_now():
    return datetime.now(timezone.utc)


@contextmanager
def connect_database():
    connection = sqlite3.connect(DATABASE, timeout=10)
    try:
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 10000")
        yield connection
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def initialize_database():
    DATABASE.parent.mkdir(parents=True, exist_ok=True)
    with connect_database() as connection:
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY,
                email TEXT NOT NULL UNIQUE COLLATE NOCASE,
                display_name TEXT NOT NULL,
                role TEXT NOT NULL CHECK (role IN ('admin', 'customer')),
                password_salt BLOB NOT NULL,
                password_hash BLOB NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS energy_readings (
                id INTEGER PRIMARY KEY,
                user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                reading_date TEXT NOT NULL,
                usage_kwh REAL NOT NULL,
                cost_inr REAL NOT NULL,
                solar_kwh REAL NOT NULL,
                carbon_kg REAL NOT NULL,
                UNIQUE (user_id, reading_date)
            );
            CREATE INDEX IF NOT EXISTS energy_user_date_idx
                ON energy_readings(user_id, reading_date);
            CREATE TABLE IF NOT EXISTS sessions (
                token_hash TEXT PRIMARY KEY,
                user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                csrf_token TEXT NOT NULL,
                expires_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS sessions_expiry_idx ON sessions(expires_at);
            """
        )
        connection.execute("DELETE FROM sessions WHERE expires_at <= ?", (utc_now().isoformat(),))


def validate_email(email):
    if not isinstance(email, str):
        raise RequestError(HTTPStatus.BAD_REQUEST, "Enter a valid email address.")
    normalized = email.strip().lower()
    try:
        normalized.encode("utf-8")
    except UnicodeEncodeError as error:
        raise RequestError(HTTPStatus.BAD_REQUEST, "Enter a valid email address.") from error
    if len(normalized) > 254 or not EMAIL_PATTERN.fullmatch(normalized):
        raise RequestError(HTTPStatus.BAD_REQUEST, "Enter a valid email address.")
    return normalized


def validate_password(password):
    if not isinstance(password, str) or not 12 <= len(password) <= 128:
        raise RequestError(HTTPStatus.BAD_REQUEST, "Password must be between 12 and 128 characters.")
    try:
        password.encode("utf-8")
    except UnicodeEncodeError as error:
        raise RequestError(HTTPStatus.BAD_REQUEST, "Password contains an invalid character.") from error
    return password


def validate_display_name(name):
    if not isinstance(name, str):
        raise RequestError(HTTPStatus.BAD_REQUEST, "Enter a customer name.")
    cleaned = name.strip()
    if (
        not 1 <= len(cleaned) <= 100
        or any(ord(character) < 32 or 0xD800 <= ord(character) <= 0xDFFF for character in cleaned)
    ):
        raise RequestError(HTTPStatus.BAD_REQUEST, "Customer name must be between 1 and 100 characters.")
    return cleaned


def hash_password(password, salt):
    return hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), salt, PASSWORD_ITERATIONS
    )


def create_user(connection, email, name, password, role):
    salt = secrets.token_bytes(16)
    cursor = connection.execute(
        """
        INSERT INTO users (email, display_name, role, password_salt, password_hash, created_at)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            email,
            name,
            role,
            salt,
            hash_password(password, salt),
            utc_now().isoformat(),
        ),
    )
    return cursor.lastrowid


def seed_customer_readings(connection, user_id):
    randomizer = secrets.SystemRandom()
    with SAMPLE_DATA.open("r", encoding="utf-8", newline="") as sample_file:
        reader = csv.DictReader(sample_file)
        expected = {"date", "usage_kwh", "cost_inr", "solar_kwh", "carbon_kg"}
        if not expected.issubset(reader.fieldnames or []):
            raise RuntimeError("The sample energy data is missing required columns.")

        for line_number, reading in enumerate(reader, start=2):
            try:
                date = datetime.strptime(reading["date"], "%Y-%m-%d").date().isoformat()
                values = [
                    float(reading["usage_kwh"]),
                    float(reading["cost_inr"]),
                    float(reading["solar_kwh"]),
                    float(reading["carbon_kg"]),
                ]
                usage_scale = randomizer.uniform(0.8, 1.2)
                solar_scale = randomizer.uniform(0.75, 1.25)
                values = [
                    round(values[0] * usage_scale, 2),
                    round(values[1] * usage_scale, 2),
                    round(min(values[2] * solar_scale, values[0] * usage_scale), 2),
                    round(values[3] * usage_scale, 2),
                ]
            except (TypeError, ValueError, KeyError) as error:
                raise RuntimeError(f"Invalid energy sample on CSV line {line_number}.") from error
            if not all(isfinite(value) for value in values):
                raise RuntimeError(f"Invalid energy sample on CSV line {line_number}.")
            connection.execute(
                """
                INSERT INTO energy_readings
                    (user_id, reading_date, usage_kwh, cost_inr, solar_kwh, carbon_kg)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (user_id, date, *values),
            )


def count_admins():
    with connect_database() as connection:
        return connection.execute(
            "SELECT COUNT(*) FROM users WHERE role = 'admin'"
        ).fetchone()[0]


def count_users():
    with connect_database() as connection:
        return connection.execute("SELECT COUNT(*) FROM users").fetchone()[0]


def is_loopback_client(address):
    try:
        return ip_address(address).is_loopback
    except ValueError:
        return False


def create_initial_admin(email, name):
    password = getpass.getpass("Choose an admin password (at least 12 characters): ")
    repeated_password = getpass.getpass("Enter the admin password again: ")
    if password != repeated_password:
        raise SystemExit("Passwords do not match.")
    try:
        normalized_email = validate_email(email)
        display_name = validate_display_name(name)
        validate_password(password)
    except RequestError as error:
        raise SystemExit(str(error)) from error

    with connect_database() as connection:
        connection.execute("BEGIN IMMEDIATE")
        if connection.execute(
            "SELECT 1 FROM users WHERE role = 'admin' LIMIT 1"
        ).fetchone():
            raise SystemExit("An admin account already exists; ask an admin to manage access.")
        if connection.execute(
            "SELECT 1 FROM users WHERE email = ? COLLATE NOCASE",
            (normalized_email,),
        ).fetchone():
            raise SystemExit("An account with that email already exists.")
        create_user(connection, normalized_email, display_name, password, "admin")

    print(f"Admin account created for {normalized_email}. Start the server with: python server.py")


def prompt_account_password(email, role_label):
    password = getpass.getpass(f"Choose a {role_label} password for {email} (at least 12 characters): ")
    repeated_password = getpass.getpass(f"Enter the {role_label} password again: ")
    if password != repeated_password:
        raise SystemExit(f"The {role_label} passwords do not match.")
    try:
        return validate_password(password)
    except RequestError as error:
        raise SystemExit(str(error)) from error


def setup_project_accounts():
    with connect_database() as connection:
        existing_admin = connection.execute(
            "SELECT id, role FROM users WHERE email = ? COLLATE NOCASE",
            (ADMIN_EMAIL,),
        ).fetchone()
        existing_customer = connection.execute(
            "SELECT id, role FROM users WHERE email = ? COLLATE NOCASE",
            (CUSTOMER_EMAIL,),
        ).fetchone()
        admin_count = connection.execute(
            "SELECT COUNT(*) FROM users WHERE role = 'admin'"
        ).fetchone()[0]

    if existing_admin and existing_admin["role"] != "admin":
        raise SystemExit(f"{ADMIN_EMAIL} already belongs to a non-admin account.")
    if existing_customer and existing_customer["role"] != "customer":
        raise SystemExit(f"{CUSTOMER_EMAIL} already belongs to a non-customer account.")
    if existing_admin and existing_customer:
        print("Both requested accounts already exist. Start the website with: python server.py")
        return
    if admin_count and not existing_admin:
        raise SystemExit("An admin account already exists. Sign in as that admin to manage customers.")

    admin_password = None if existing_admin else prompt_account_password(ADMIN_EMAIL, "admin")
    customer_password = None if existing_customer else prompt_account_password(CUSTOMER_EMAIL, "customer")

    with connect_database() as connection:
        connection.execute("BEGIN IMMEDIATE")
        if admin_password:
            create_user(connection, ADMIN_EMAIL, "Administrator", admin_password, "admin")
        if customer_password:
            customer_id = create_user(
                connection,
                CUSTOMER_EMAIL,
                "Customer",
                customer_password,
                "customer",
            )
            seed_customer_readings(connection, customer_id)

    print("Admin and customer accounts are ready:")
    print(f"  Admin: {ADMIN_EMAIL}")
    print(f"  Customer: {CUSTOMER_EMAIL}")
    print("Start the website with: python server.py")


class WattwiseHandler(BaseHTTPRequestHandler):
    server_version = "Wattwise"
    sys_version = ""

    def log_message(self, format_string, *args):
        LOGGER.info("%s - %s", self.address_string(), format_string % args)

    def _send(self, status, body=b"", content_type="application/json; charset=utf-8", headers=None):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "strict-origin-when-cross-origin")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline' "
            "https://fonts.googleapis.com; font-src 'self' https://fonts.gstatic.com; "
            "connect-src 'self'; img-src 'self' data:; object-src 'none'; "
            "base-uri 'self'; form-action 'self'; frame-ancestors 'none'",
        )
        if self.path.startswith("/api/") or self.path in {"/", "/login", "/dashboard", "/admin"}:
            self.send_header("Cache-Control", "no-store")
        else:
            self.send_header("Cache-Control", "no-cache")
        for name, value in (headers or {}).items():
            self.send_header(name, value)
        self.end_headers()
        if body:
            self.wfile.write(body)

    def send_json(self, status, payload, headers=None):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self._send(status, body, headers=headers)

    def send_error_json(self, status, message):
        self.send_json(status, {"error": message})

    def redirect(self, location):
        self._send(HTTPStatus.SEE_OTHER, headers={"Location": location})

    def send_page(self, filename, status=HTTPStatus.OK):
        content = (ROOT / filename).read_bytes()
        self._send(status, content, "text/html; charset=utf-8")

    def send_asset(self, filename):
        asset = ROOT / filename
        if not asset.is_file():
            self.send_error_json(HTTPStatus.NOT_FOUND, "Page or file not found.")
            return
        content_types = {
            ".css": "text/css; charset=utf-8",
            ".js": "text/javascript; charset=utf-8",
        }
        self._send(
            HTTPStatus.OK,
            asset.read_bytes(),
            content_types.get(asset.suffix, "application/octet-stream"),
        )

    def current_user(self):
        cookie = SimpleCookie()
        try:
            cookie.load(self.headers.get("Cookie", ""))
        except (CookieError, ValueError, KeyError):
            return None
        morsel = cookie.get(SESSION_COOKIE)
        if morsel is None or not morsel.value:
            return None

        token_hash = hashlib.sha256(morsel.value.encode("utf-8")).hexdigest()
        with connect_database() as connection:
            row = connection.execute(
                """
                SELECT users.id, users.display_name, users.email, users.role, sessions.csrf_token
                FROM sessions
                JOIN users ON users.id = sessions.user_id
                WHERE sessions.token_hash = ? AND sessions.expires_at > ?
                """,
                (token_hash, utc_now().isoformat()),
            ).fetchone()
        return dict(row) if row else None

    def require_role(self, role=None):
        user = self.current_user()
        if user is None:
            raise RequestError(HTTPStatus.UNAUTHORIZED, "Please sign in to continue.")
        if role and user["role"] != role:
            raise RequestError(HTTPStatus.FORBIDDEN, "You do not have permission to do that.")
        return user

    def read_json(self):
        content_type = self.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
        if content_type != "application/json":
            raise RequestError(HTTPStatus.UNSUPPORTED_MEDIA_TYPE, "Send this request as JSON.")
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError as error:
            raise RequestError(HTTPStatus.BAD_REQUEST, "Invalid request body.") from error
        if length <= 0 or length > MAX_REQUEST_BYTES:
            raise RequestError(HTTPStatus.BAD_REQUEST, "The request body is empty or too large.")
        try:
            payload = json.loads(self.rfile.read(length))
        except (json.JSONDecodeError, UnicodeDecodeError) as error:
            raise RequestError(HTTPStatus.BAD_REQUEST, "Invalid JSON request body.") from error
        if not isinstance(payload, dict):
            raise RequestError(HTTPStatus.BAD_REQUEST, "Invalid request body.")
        return payload

    def check_csrf(self, user):
        supplied = self.headers.get("X-CSRF-Token", "")
        if not supplied or not hmac.compare_digest(supplied, user["csrf_token"]):
            raise RequestError(HTTPStatus.FORBIDDEN, "Your session expired. Refresh the page and try again.")

    def send_request_error(self, error):
        self.send_error_json(error.status, str(error))

    def send_internal_error(self):
        LOGGER.exception("Unexpected request error")
        self.send_error_json(HTTPStatus.INTERNAL_SERVER_ERROR, "The request could not be completed.")

    def do_GET(self):
        try:
            self._route_get()
        except RequestError as error:
            self.send_request_error(error)
        except (OSError, sqlite3.Error):
            self.send_internal_error()

    def _route_get(self):
        request = urlsplit(self.path)
        path = request.path

        if path == "/":
            user = self.current_user()
            if user:
                self.redirect("/admin" if user["role"] == "admin" else "/dashboard")
            else:
                self.redirect("/login" if count_users() else "/setup")
            return
        if path == "/login":
            user = self.current_user()
            if user:
                self.redirect("/admin" if user["role"] == "admin" else "/dashboard")
            elif count_users() == 0:
                self.redirect("/setup")
            else:
                self.send_page("login.html")
            return
        if path == "/setup":
            user = self.current_user()
            if user:
                self.redirect("/admin" if user["role"] == "admin" else "/dashboard")
            elif count_users() == 0:
                self.send_page("login.html")
            else:
                self.redirect("/login")
            return
        if path in {"/dashboard", "/index.html"}:
            user = self.current_user()
            if not user:
                self.redirect("/login")
            elif user["role"] == "admin":
                self.redirect("/admin")
            else:
                self.send_page("index.html")
            return
        if path in {"/admin", "/admin.html"}:
            user = self.current_user()
            if not user:
                self.redirect("/login")
            elif user["role"] != "admin":
                self.redirect("/dashboard")
            else:
                self.send_page("admin.html")
            return
        if path in {
            "/styles.css",
            "/auth.css",
            "/app.js",
            "/auth.js",
            "/login.js",
            "/admin.js",
        }:
            self.send_asset(path.lstrip("/"))
            return
        if path == "/api/me":
            user = self.require_role()
            self.send_json(
                HTTPStatus.OK,
                {
                    "user": {
                        "id": user["id"],
                        "name": user["display_name"],
                        "email": user["email"],
                        "role": user["role"],
                    },
                    "csrfToken": user["csrf_token"],
                },
            )
            return
        if path == "/api/setup-status":
            self.send_json(
                HTTPStatus.OK,
                {
                    "setupRequired": is_loopback_client(self.client_address[0]) and count_users() == 0,
                    "adminEmail": ADMIN_EMAIL,
                    "customerEmail": CUSTOMER_EMAIL,
                },
            )
            return
        if path == "/api/health":
            self.send_json(HTTPStatus.OK, {"ok": True})
            return
        if path == "/api/energy":
            user = self.require_role("customer")
            with connect_database() as connection:
                readings = connection.execute(
                    """
                    SELECT reading_date, usage_kwh, cost_inr, solar_kwh, carbon_kg
                    FROM energy_readings WHERE user_id = ? ORDER BY reading_date DESC LIMIT 90
                    """,
                    (user["id"],),
                ).fetchall()
            readings = list(reversed(readings))
            self.send_json(
                HTTPStatus.OK,
                {
                    "readings": [
                        {
                            "date": row["reading_date"],
                            "usage": row["usage_kwh"],
                            "cost": row["cost_inr"],
                            "solar": row["solar_kwh"],
                            "carbon": row["carbon_kg"],
                        }
                        for row in readings
                    ]
                },
            )
            return
        if path == "/api/admin/customers":
            self.require_role("admin")
            with connect_database() as connection:
                customers = connection.execute(
                    """
                    SELECT users.id, users.display_name, users.email, users.created_at,
                           COUNT(energy_readings.id) AS reading_count,
                           COALESCE(SUM(energy_readings.usage_kwh), 0) AS usage_kwh,
                           COALESCE(SUM(energy_readings.cost_inr), 0) AS cost_inr,
                           COALESCE(SUM(energy_readings.solar_kwh), 0) AS solar_kwh,
                           COALESCE(SUM(energy_readings.carbon_kg), 0) AS carbon_kg
                    FROM users
                    LEFT JOIN energy_readings ON energy_readings.user_id = users.id
                    WHERE users.role = 'customer'
                    GROUP BY users.id
                    ORDER BY users.created_at DESC
                    """
                ).fetchall()
            self.send_json(
                HTTPStatus.OK,
                {
                    "customers": [
                        {
                            "id": row["id"],
                            "name": row["display_name"],
                            "email": row["email"],
                            "createdAt": row["created_at"],
                            "readingCount": row["reading_count"],
                            "usageKwh": round(row["usage_kwh"], 2),
                            "costInr": round(row["cost_inr"], 2),
                            "solarKwh": round(row["solar_kwh"], 2),
                            "carbonKg": round(row["carbon_kg"], 2),
                        }
                        for row in customers
                    ]
                },
            )
            return
        if path == "/api/admin/readings":
            self.require_role("admin")
            raw_id = parse_qs(request.query).get("customer_id", [""])[0]
            customer_id = self.parse_customer_id(raw_id)
            with connect_database() as connection:
                customer = connection.execute(
                    "SELECT id, display_name, email FROM users WHERE id = ? AND role = 'customer'",
                    (customer_id,),
                ).fetchone()
                if customer is None:
                    raise RequestError(HTTPStatus.NOT_FOUND, "Customer account not found.")
                rows = connection.execute(
                    """
                    SELECT reading_date, usage_kwh, cost_inr, solar_kwh, carbon_kg
                    FROM energy_readings WHERE user_id = ? ORDER BY reading_date DESC LIMIT 90
                    """,
                    (customer_id,),
                ).fetchall()
            self.send_json(
                HTTPStatus.OK,
                {
                    "customer": {"id": customer["id"], "name": customer["display_name"], "email": customer["email"]},
                    "readings": [dict(row) for row in rows],
                },
            )
            return

        self.send_error_json(HTTPStatus.NOT_FOUND, "Page or file not found.")

    def do_POST(self):
        try:
            self._route_post()
        except RequestError as error:
            self.send_request_error(error)
        except sqlite3.IntegrityError as error:
            message = str(error).lower()
            if "users.email" in message or "unique constraint" in message:
                self.send_error_json(HTTPStatus.CONFLICT, "An account with that email already exists.")
            else:
                LOGGER.exception("Database constraint rejected a request")
                self.send_error_json(HTTPStatus.CONFLICT, "The requested change conflicts with existing data.")
        except (OSError, RuntimeError, ValueError, sqlite3.Error):
            self.send_internal_error()

    def _route_post(self):
        path = urlsplit(self.path).path
        payload = self.read_json()

        if path == "/api/login":
            self._login(payload)
            return

        if path == "/api/setup":
            self._setup_accounts(payload)
            return

        user = self.require_role()
        self.check_csrf(user)

        if path == "/api/logout":
            token = self._raw_session_token()
            if token:
                with connect_database() as connection:
                    connection.execute(
                        "DELETE FROM sessions WHERE token_hash = ?",
                        (hashlib.sha256(token.encode("utf-8")).hexdigest(),),
                    )
            self.send_json(
                HTTPStatus.OK,
                {"ok": True},
                headers={"Set-Cookie": self._cookie_header("", max_age=0)},
            )
            return

        customer_match = re.fullmatch(r"/api/admin/customers/(\d+)(?:/(password))?", path)
        if path == "/api/admin/customers":
            if user["role"] != "admin":
                raise RequestError(HTTPStatus.FORBIDDEN, "You do not have permission to do that.")
            self._create_customer(user, payload)
            return
        if path == "/api/admin/admins":
            if user["role"] != "admin":
                raise RequestError(HTTPStatus.FORBIDDEN, "You do not have permission to do that.")
            self._create_admin(user, payload)
            return
        if customer_match and customer_match.group(2) == "password":
            if user["role"] != "admin":
                raise RequestError(HTTPStatus.FORBIDDEN, "You do not have permission to do that.")
            self._reset_customer_password(
                user,
                self.parse_customer_id(customer_match.group(1)),
                payload,
            )
            return

        self.send_error_json(HTTPStatus.NOT_FOUND, "Page or file not found.")

    def do_DELETE(self):
        try:
            user = self.require_role("admin")
            self.check_csrf(user)
            match = re.fullmatch(r"/api/admin/customers/(\d+)", urlsplit(self.path).path)
            if not match:
                self.send_error_json(HTTPStatus.NOT_FOUND, "Page or file not found.")
                return
            customer_id = self.parse_customer_id(match.group(1))
            with connect_database() as connection:
                result = connection.execute(
                    "DELETE FROM users WHERE id = ? AND role = 'customer'",
                    (customer_id,),
                )
                if result.rowcount == 0:
                    raise RequestError(HTTPStatus.NOT_FOUND, "Customer account not found.")
            self.send_json(HTTPStatus.OK, {"ok": True})
        except RequestError as error:
            self.send_request_error(error)
        except (OSError, RuntimeError, ValueError, sqlite3.Error):
            self.send_internal_error()

    @staticmethod
    def parse_customer_id(raw_id):
        if not raw_id.isdecimal() or len(raw_id) > 10:
            raise RequestError(HTTPStatus.BAD_REQUEST, "Choose a valid customer account.")
        return int(raw_id)

    def _raw_session_token(self):
        cookie = SimpleCookie()
        try:
            cookie.load(self.headers.get("Cookie", ""))
        except (CookieError, ValueError, KeyError):
            return None
        morsel = cookie.get(SESSION_COOKIE)
        return morsel.value if morsel else None

    @staticmethod
    def _cookie_header(token, max_age=None):
        secure = "; Secure" if os.environ.get("WATTWISE_SECURE_COOKIE") == "1" else ""
        if not token:
            return f"{SESSION_COOKIE}=; Path=/; HttpOnly; SameSite=Strict; Max-Age=0{secure}"
        age = max_age if max_age is not None else SESSION_HOURS * 60 * 60
        return f"{SESSION_COOKIE}={token}; Path=/; HttpOnly; SameSite=Strict; Max-Age={age}{secure}"

    def _login(self, payload):
        client_ip = self.client_address[0]
        now = time.monotonic()
        with LOGIN_ATTEMPTS_LOCK:
            attempts = LOGIN_ATTEMPTS[client_ip]
            while attempts and now - attempts[0] > 900:
                attempts.popleft()
            if len(attempts) >= 10:
                raise RequestError(HTTPStatus.TOO_MANY_REQUESTS, "Too many sign-in attempts. Try again in 15 minutes.")

        email = payload.get("email")
        password = payload.get("password")
        normalized_email = email.strip().lower() if isinstance(email, str) else ""
        if (
            not normalized_email
            or len(normalized_email) > 254
            or not EMAIL_PATTERN.fullmatch(normalized_email)
            or any(0xD800 <= ord(character) <= 0xDFFF for character in normalized_email)
            or not isinstance(password, str)
            or len(password) > 128
            or any(0xD800 <= ord(character) <= 0xDFFF for character in password)
        ):
            with LOGIN_ATTEMPTS_LOCK:
                LOGIN_ATTEMPTS[client_ip].append(now)
            raise RequestError(HTTPStatus.UNAUTHORIZED, "Email or password is incorrect.")

        with connect_database() as connection:
            account = connection.execute(
                """
                SELECT id, display_name, email, role, password_salt, password_hash
                FROM users WHERE email = ? COLLATE NOCASE
                """,
                (normalized_email,),
            ).fetchone()
            if account is None:
                dummy_hash = hash_password(password, bytes(16))
                hmac.compare_digest(dummy_hash, bytes(32))
            elif not hmac.compare_digest(
                hash_password(password, account["password_salt"]),
                account["password_hash"],
            ):
                account = None

            if account is None:
                with LOGIN_ATTEMPTS_LOCK:
                    LOGIN_ATTEMPTS[client_ip].append(now)
                raise RequestError(HTTPStatus.UNAUTHORIZED, "Email or password is incorrect.")

            token = secrets.token_urlsafe(32)
            csrf_token = secrets.token_urlsafe(32)
            expires_at = (utc_now() + timedelta(hours=SESSION_HOURS)).isoformat()
            connection.execute(
                "INSERT INTO sessions (token_hash, user_id, csrf_token, expires_at) VALUES (?, ?, ?, ?)",
                (
                    hashlib.sha256(token.encode("utf-8")).hexdigest(),
                    account["id"],
                    csrf_token,
                    expires_at,
                ),
            )

        with LOGIN_ATTEMPTS_LOCK:
            LOGIN_ATTEMPTS.pop(client_ip, None)
        self.send_json(
            HTTPStatus.OK,
            {"role": account["role"], "name": account["display_name"]},
            headers={"Set-Cookie": self._cookie_header(token)},
        )

    def _setup_accounts(self, payload):
        origin = urlsplit(self.headers.get("Origin", ""))
        host = self.headers.get("Host", "")
        if (
            not is_loopback_client(self.client_address[0])
            or origin.scheme not in {"http", "https"}
            or origin.netloc.casefold() != host.casefold()
            or origin.path
            or origin.query
            or origin.fragment
            or origin.username
            or origin.password
        ):
            raise RequestError(
                HTTPStatus.FORBIDDEN,
                "Initial account setup is only available from this computer.",
            )

        admin_password = validate_password(payload.get("adminPassword"))
        customer_password = validate_password(payload.get("customerPassword"))
        if hmac.compare_digest(admin_password, customer_password):
            raise RequestError(
                HTTPStatus.BAD_REQUEST,
                "Choose a different password for each account.",
            )

        with connect_database() as connection:
            connection.execute("BEGIN IMMEDIATE")
            if connection.execute("SELECT 1 FROM users LIMIT 1").fetchone():
                raise RequestError(
                    HTTPStatus.CONFLICT,
                    "Accounts have already been set up. Please sign in.",
                )
            create_user(
                connection,
                ADMIN_EMAIL,
                "Administrator",
                admin_password,
                "admin",
            )
            customer_id = create_user(
                connection,
                CUSTOMER_EMAIL,
                "Customer",
                customer_password,
                "customer",
            )
            seed_customer_readings(connection, customer_id)

        LOGGER.info("Initial admin and customer accounts were set up on this computer.")
        self.send_json(
            HTTPStatus.CREATED,
            {"ok": True, "message": "Your accounts are ready. Sign in to continue."},
        )

    def _create_customer(self, admin, payload):
        email = validate_email(payload.get("email", ""))
        name = validate_display_name(payload.get("name", ""))
        password = validate_password(payload.get("password"))
        with connect_database() as connection:
            customer_id = create_user(connection, email, name, password, "customer")
            seed_customer_readings(connection, customer_id)
        LOGGER.info("Admin %s created customer account %s", admin["id"], customer_id)
        self.send_json(HTTPStatus.CREATED, {"id": customer_id, "name": name, "email": email})

    def _create_admin(self, admin, payload):
        email = validate_email(payload.get("email", ""))
        name = validate_display_name(payload.get("name", "Additional Administrator"))
        password = validate_password(payload.get("password"))
        with connect_database() as connection:
            admin_id = create_user(connection, email, name, password, "admin")
        LOGGER.info("Admin %s created admin account %s", admin["id"], admin_id)
        self.send_json(HTTPStatus.CREATED, {"id": admin_id, "name": name, "email": email})

    def _reset_customer_password(self, admin, customer_id, payload):
        password = validate_password(payload.get("password"))
        salt = secrets.token_bytes(16)
        with connect_database() as connection:
            result = connection.execute(
                """
                UPDATE users SET password_salt = ?, password_hash = ?
                WHERE id = ? AND role = 'customer'
                """,
                (salt, hash_password(password, salt), customer_id),
            )
            if result.rowcount == 0:
                raise RequestError(HTTPStatus.NOT_FOUND, "Customer account not found.")
            connection.execute("DELETE FROM sessions WHERE user_id = ?", (customer_id,))
        LOGGER.info("Admin %s reset customer account %s", admin["id"], customer_id)
        self.send_json(HTTPStatus.OK, {"ok": True})


def main():
    parser = argparse.ArgumentParser(description="Run the Wattwise energy website.")
    commands = parser.add_subparsers(dest="command")
    commands.add_parser("setup-accounts", help="create the configured admin and customer accounts")
    admin_parser = commands.add_parser("create-admin", help="create the first admin account")
    admin_parser.add_argument("--email", required=True, help="admin email address")
    admin_parser.add_argument("--name", required=True, help="admin display name")
    args = parser.parse_args()

    initialize_database()
    if args.command == "setup-accounts":
        setup_project_accounts()
        return
    if args.command == "create-admin":
        create_initial_admin(args.email, args.name)
        return
    host = os.environ.get("WATTWISE_HOST", "127.0.0.1")
    port = int(os.environ.get("PORT", os.environ.get("WATTWISE_PORT", "8001")))
    server = ThreadingHTTPServer((host, port), WattwiseHandler)
    server.daemon_threads = True
    if count_admins() == 0:
        LOGGER.info(
            "First-time setup: open http://%s:%s/setup and create your admin and customer passwords.",
            host,
            port,
        )
    LOGGER.info("Wattwise website listening at http://%s:%s", host, port)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        LOGGER.info("Shutting down Wattwise.")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
