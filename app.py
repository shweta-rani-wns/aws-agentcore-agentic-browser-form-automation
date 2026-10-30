from __future__ import annotations

import json
import os
import secrets
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path

import boto3
import requests
from dotenv import load_dotenv
from flask import Flask, jsonify, render_template, request, session

load_dotenv()
BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
DATA_DIR.mkdir(exist_ok=True)
DB_PATH = DATA_DIR / "bookings.sqlite3"

app = Flask(__name__)
app.secret_key = os.getenv("APP_SECRET_KEY", "local-dev-only-change-me")
app.config.update(SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE="Lax")

AWS_REGION = os.getenv("AWS_REGION", "us-west-2")
SES_FROM_EMAIL = os.getenv("SES_FROM_EMAIL", "").strip()
SES_TO_EMAIL = os.getenv("SES_TO_EMAIL", "").strip()
OTP_TTL_SECONDS = int(os.getenv("OTP_TTL_SECONDS", "300"))
RECAPTCHA_SITE_KEY = os.getenv("RECAPTCHA_SITE_KEY", "").strip()
RECAPTCHA_SECRET_KEY = os.getenv("RECAPTCHA_SECRET_KEY", "").strip()
RECAPTCHA_VERIFY_URL = os.getenv(
    "RECAPTCHA_VERIFY_URL", "https://www.google.com/recaptcha/api/siteverify"
)

LOCATIONS = {
    "Singapore": {
        "Central": ["Marina Bay", "Orchard", "Tuas"],
        "East": ["Changi", "Pasir Ris"],
        "West": ["Jurong", "Pioneer"],
    },
    "Malaysia": {
        "Kuala Lumpur": ["KL Sentral", "Bangsar", "Cheras"],
        "Penang": ["George Town", "Bayan Lepas"],
        "Selangor": ["Shah Alam", "Petaling Jaya"],
    },
    "Thailand": {
        "Bangkok": ["Sukhumvit", "Silom"],
        "Phuket": ["Patong", "Kathu"],
    },
}

def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()

def connect_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    with connect_db() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS bookings (
                booking_id TEXT PRIMARY KEY,
                booking_reference TEXT NOT NULL UNIQUE,
                customer_name TEXT NOT NULL,
                customer_email TEXT NOT NULL,
                country TEXT NOT NULL,
                region TEXT NOT NULL,
                city TEXT NOT NULL,
                service_date TEXT NOT NULL,
                service_type TEXT NOT NULL,
                status TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS audit_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                booking_id TEXT,
                event_type TEXT NOT NULL,
                details_json TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
        """)

def audit(event_type: str, booking_id: str | None, details: dict):
    with connect_db() as conn:
        conn.execute(
            "INSERT INTO audit_events (booking_id, event_type, details_json, created_at) VALUES (?, ?, ?, ?)",
            (booking_id, event_type, json.dumps(details, ensure_ascii=False), utc_now()),
        )

def send_otp_email(email: str, otp: str):
    if not SES_FROM_EMAIL:
        raise RuntimeError("SES_FROM_EMAIL is not configured; OTP delivery is unavailable.")
    # SES identity/account permissions are checked by AWS.
    ses = boto3.client("ses", region_name=AWS_REGION)
    ses.send_email(
        Source=SES_FROM_EMAIL,
        Destination={"ToAddresses": [email]},
        Message={
            "Subject": {"Data": "Your Booking Automation Lab verification code"},
            "Body": {"Text": {"Data": f"Your verification code is {otp}. It expires in {OTP_TTL_SECONDS} seconds. If you did not request it, ignore this email."}},
        },
    )

def verify_captcha(token: str, remote_ip: str | None) -> bool:
    if not RECAPTCHA_SECRET_KEY or not RECAPTCHA_SITE_KEY:
        return False
    if not token:
        return False
    try:
        response = requests.post(
            RECAPTCHA_VERIFY_URL,
            data={"secret": RECAPTCHA_SECRET_KEY, "response": token, "remoteip": remote_ip},
            timeout=8,
        )
        response.raise_for_status()
        payload = response.json()
        return bool(payload.get("success"))
    except (requests.RequestException, ValueError):
        return False

@app.get("/")
def index():
    return render_template(
        "index.html",
        recaptcha_site_key=RECAPTCHA_SITE_KEY,
    )

@app.get("/api/health")
def health():
    return jsonify({"status": "ok", "service": "booking-automation-lab"})

@app.get("/api/locations")
def locations():
    country = request.args.get("country", "")
    region = request.args.get("region", "")
    if not country:
        return jsonify({"countries": list(LOCATIONS.keys())})
    if country not in LOCATIONS:
        return jsonify({"error": "Unknown country"}), 400
    if not region:
        return jsonify({"regions": list(LOCATIONS[country].keys())})
    if region not in LOCATIONS[country]:
        return jsonify({"error": "Unknown region for selected country"}), 400
    return jsonify({"cities": LOCATIONS[country][region]})

@app.post("/api/otp/request")
def request_otp():
    payload = request.get_json(silent=True) or {}
    email = str(payload.get("email", "")).strip().lower()
    if not email or "@" not in email or len(email) > 254:
        return jsonify({"error": "Enter a valid email address."}), 400
    otp = f"{secrets.randbelow(1_000_000):06d}"
    try:
        send_otp_email(email, otp)
    except Exception as exc:
        app.logger.warning("OTP delivery failed: %s", type(exc).__name__)
        audit("otp_delivery_failed", None, {"email_domain": email.rsplit("@", 1)[-1]})
        return jsonify({"error": "OTP email could not be sent. Check SES configuration and verified identities."}), 503
    session["otp_hash"] = __import__("hashlib").sha256(otp.encode()).hexdigest()
    session["otp_email"] = email
    session["otp_expires"] = int(time.time()) + OTP_TTL_SECONDS
    session["otp_attempts"] = 0
    audit("otp_requested", None, {"email_domain": email.rsplit("@", 1)[-1]})
    return jsonify({"message": "Verification code sent. Check the email inbox."})

@app.post("/api/bookings")
def create_booking():
    payload = request.get_json(silent=True) or {}
    required = [
        "booking_reference", "customer_name", "customer_email",
        "country", "region", "city", "service_date", "service_type",
    ]
    missing = [key for key in required if not str(payload.get(key, "")).strip()]
    if missing:
        return jsonify({"error": "Missing required fields.", "fields": missing}), 400

    email = str(payload["customer_email"]).strip().lower()
    if session.get("otp_email") != email or not session.get("otp_hash"):
        return jsonify({"error": "Request a verification code for this email first."}), 400
    if int(time.time()) > int(session.get("otp_expires", 0)):
        return jsonify({"error": "Verification code expired. Request a new one."}), 400
    attempts = int(session.get("otp_attempts", 0))
    if attempts >= 5:
        return jsonify({"error": "Too many verification attempts. Request a new code."}), 429

    import hashlib
    supplied_otp = str(payload.get("otp", "")).strip()
    session["otp_attempts"] = attempts + 1
    if not secrets.compare_digest(
        hashlib.sha256(supplied_otp.encode()).hexdigest(),
        str(session.get("otp_hash", "")),
    ):
        audit("otp_verification_failed", None, {"email_domain": email.rsplit("@", 1)[-1]})
        return jsonify({"error": "Verification code is incorrect."}), 400

    captcha_token = str(payload.get("captcha_token", "")).strip()
    if not verify_captcha(captcha_token, request.remote_addr):
        audit("captcha_verification_failed", None, {})
        return jsonify({"error": "CAPTCHA verification failed. Complete the CAPTCHA and try again."}), 400

    country = str(payload["country"]).strip()
    region = str(payload["region"]).strip()
    city = str(payload["city"]).strip()
    if country not in LOCATIONS or region not in LOCATIONS[country] or city not in LOCATIONS[country][region]:
        return jsonify({"error": "Selected country, region, and city combination is invalid."}), 400

    booking_id = secrets.token_urlsafe(12)
    reference = str(payload["booking_reference"]).strip()
    created_at = utc_now()
    try:
        with connect_db() as conn:
            conn.execute(
                """INSERT INTO bookings
                (booking_id, booking_reference, customer_name, customer_email, country, region, city, service_date, service_type, status, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    booking_id, reference, str(payload["customer_name"]).strip(), email,
                    country, region, city, str(payload["service_date"]),
                    str(payload["service_type"]).strip(), "CONFIRMED", created_at,
                ),
            )
    except sqlite3.IntegrityError:
        return jsonify({"error": "Booking reference already exists. Choose a unique reference."}), 409

    audit("booking_confirmed", booking_id, {
        "booking_reference": reference,
        "country": country,
        "region": region,
        "city": city,
        "verification": {"otp": True, "captcha": True},
    })
    session.pop("otp_hash", None)
    session.pop("otp_email", None)
    session.pop("otp_expires", None)
    session.pop("otp_attempts", None)
    return jsonify({
        "booking_id": booking_id,
        "booking_reference": reference,
        "status": "CONFIRMED",
        "created_at": created_at,
        "verification": {"otp": True, "captcha": True},
    }), 201

@app.get("/api/bookings/<booking_reference>")
def get_booking(booking_reference):
    with connect_db() as conn:
        row = conn.execute(
            "SELECT booking_id, booking_reference, customer_name, country, region, city, service_date, service_type, status, created_at FROM bookings WHERE booking_reference = ?",
            (booking_reference,),
        ).fetchone()
    if not row:
        return jsonify({"error": "Booking not found"}), 404
    return jsonify(dict(row))

@app.get("/api/audit")
def get_audit():
    # Local test endpoint; protect or remove before public deployment.
    with connect_db() as conn:
        rows = conn.execute(
            "SELECT id, booking_id, event_type, details_json, created_at FROM audit_events ORDER BY id DESC LIMIT 100"
        ).fetchall()
    return jsonify([dict(row) for row in rows])

init_db()

if __name__ == "__main__":
    app.run(host="127.0.0.1", port=8000, debug=True)
